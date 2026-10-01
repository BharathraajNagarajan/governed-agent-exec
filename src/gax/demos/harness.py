import asyncio
import ctypes
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
import httpx
from pymongo import MongoClient
from temporalio.api.enums.v1 import EventType, RetryState
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowFailureError, WorkflowHandle
from gax.config import Settings
from gax.credentials import build_broker
from gax.credentials.local_only_broker import GRANTS_COLLECTION, MongoGrantStore
from gax.models import Incident
from gax.remediation.activities import AUDIT, LEDGER
from gax.remediation.types import RemediationInput
from gax.workflows import INCIDENT_ID_REUSE, TASK_QUEUE, RemediationWorkflow

ROOT = Path(__file__).resolve().parents[3]
GAX = ROOT / ".gax"
RESULTS = GAX / "demo-results"
PROPOSALS = GAX / "demo-proposals"
WORKER_LOG = GAX / "logs" / "worker.log"
FLEET_LOG = GAX / "logs" / "fleet-api.err.log"
WORKER_PIDS = GAX / "pids" / "worker.pid"
CONTAINER = "gae-atlas-local"
SECRET_PATTERN = re.compile(rb"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|eyJhbGciOi|Bearer [A-Za-z0-9._~+/=-]+")
PASS, FAIL, NOT_REPRODUCED = "PASS", "FAIL", "NOT REPRODUCED"
STILL_ACTIVE = 259


def verdict(checks: list[dict]) -> str:
    if not checks:
        return FAIL
    if any(c["reproduce"] and not c["ok"] for c in checks):
        return NOT_REPRODUCED
    return PASS if all(c["ok"] for c in checks) else FAIL


def summarize_history(events) -> dict:
    names, out = {}, {"scheduled": [], "attempts": [], "completed": [], "failures": []}
    for e in events:
        t = e.event_type
        if t == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
            names[e.event_id] = e.activity_task_scheduled_event_attributes.activity_type.name
            out["scheduled"].append(names[e.event_id])
        elif t == EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED:
            a = e.activity_task_started_event_attributes
            out["attempts"].append({"activity": names.get(a.scheduled_event_id), "attempt": a.attempt,
                                    "last_failure": a.last_failure.message or None,
                                    "last_failure_type": a.last_failure.application_failure_info.type or None})
        elif t == EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
            out["completed"].append(names.get(e.activity_task_completed_event_attributes.scheduled_event_id))
        elif t in (EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED, EventType.EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT):
            a = (e.activity_task_failed_event_attributes if t == EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED
                 else e.activity_task_timed_out_event_attributes)
            info = a.failure.application_failure_info
            out["failures"].append({"activity": names.get(a.scheduled_event_id), "type": info.type or None, "message": a.failure.message,
                                    "non_retryable": info.non_retryable, "retry_state": RetryState.Name(a.retry_state)})
    return out


def final_attempt(history: dict, activity: str) -> Optional[dict]:
    return next((a for a in reversed(history["attempts"]) if a["activity"] == activity), None)


def secret_hits(blobs: list[bytes]) -> int:
    return sum(len(SECRET_PATTERN.findall(b)) for b in blobs)


def outcomes(rows: list[dict]) -> list[str]:
    return [r.get("outcome") or r["status"] for r in rows]


def proposal_json(action: str, environment: str = "staging", target: str = "orders-consumer", replicas: Optional[int] = None) -> str:
    return json.dumps({"action": action, "target": target, "environment": environment, "params": {"replicas": replicas},
                       "justification": f"demo-injected proposal: {action}", "cited_chunk_ids": []})


def summary_table(results: list[dict]) -> str:
    rows = [("demo", "status", "seconds", "checks")] + [
        (r["name"], r["status"], f"{r['duration_s']:.1f}", f"{sum(c['ok'] for c in r['checks'])}/{len(r['checks'])}") for r in results]
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    return "\n".join("  ".join(v.ljust(w) for v, w in zip(row, widths)) for row in rows)


def pid_alive(pid: int) -> bool:
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)
    if not h:
        return False
    code = ctypes.c_ulong()
    k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return code.value == STILL_ACTIVE


def read_from(path: Path, offset: int) -> str:
    if not path.exists():
        return ""
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read().decode("utf-8", errors="replace")


def size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def run_script(name: str) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / name)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"{name} failed: {r.stdout[-500:]} {r.stderr[-500:]}")
    return r.stdout.strip()


def worker_pids() -> list[int]:
    return [int(x) for x in WORKER_PIDS.read_text().split()] if WORKER_PIDS.exists() else []


def running_worker() -> Optional[int]:
    pids = worker_pids()
    return pids[-1] if len(pids) >= 2 and pid_alive(pids[-1]) else None


def start_worker() -> int:
    print(f"  {run_script('start-worker.ps1')}", flush=True)
    return worker_pids()[-1]


def docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=180)


def mongo_ready(uri: str) -> bool:
    try:
        with MongoClient(uri, serverSelectionTimeoutMS=1000) as c:
            c.admin.command("ping")
        return True
    except Exception:
        return False


class Demo:
    def __init__(self, name: str, settings: Settings, client: Client):
        self.name, self.s, self.client = name, settings, client
        self.db = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)[settings.gax_db]
        self.fleet = httpx.Client(base_url=settings.fleet_api_url, timeout=15)
        self.broker = build_broker(settings, store=MongoGrantStore(self.db[GRANTS_COLLECTION]))
        self.checks: list[dict] = []
        self.evidence: dict[str, Any] = {}
        self.notes: list[str] = []
        self.handles: list[WorkflowHandle] = []
        self.mongo_stopped = False
        self.worker_offset, self.fleet_offset = size(WORKER_LOG), size(FLEET_LOG)
        self.stamp = datetime.now().strftime("%Y%m%d%H%M%S")

    def incident_id(self, suffix: str = "") -> str:
        return f"DEMO-{self.name.upper().replace('_', '-')}-{self.stamp}{suffix}"

    def check(self, name: str, ok: Any, observed: Any = None, reproduce: bool = False) -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "observed": observed, "reproduce": reproduce})
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: {json.dumps(observed, default=str)[:300]}", flush=True)
        return bool(ok)

    def note(self, text: str) -> None:
        self.notes.append(text)
        print(f"  NOTE  {text}", flush=True)

    def reset(self) -> None:
        self.fleet.post("/admin/reset").raise_for_status()
        self.db[GRANTS_COLLECTION].delete_many({})

    def fault(self, action: str, **spec) -> None:
        self.fleet.post("/admin/faults", json={"action": action, **spec}).raise_for_status()
        self.evidence.setdefault("faults", []).append({"action": action, **spec})

    def counters(self) -> dict:
        return self.fleet.get("/admin/counters").json()

    def consumer(self, environment: str = "staging", target: str = "orders-consumer") -> dict:
        return self.db["fleet_state"].find_one({"_id": f"{environment}:{target}"}, {"_id": 0, "updated_at": 0})

    def ledger(self, wid: str, run_id: Optional[str] = None) -> list[dict]:
        q = {"workflow_id": wid, **({"run_id": run_id} if run_id else {})}
        return list(self.db[LEDGER].find(q, {"_id": 0, "attempt": 1, "status": 1, "outcome": 1, "error": 1, "run_id": 1, "at": 1})
                    .sort("at", 1))

    def audit(self, wid: str, run_id: str) -> Optional[dict]:
        return self.db[AUDIT].find_one({"_id": f"{wid}:{run_id}"})

    def worker_log(self) -> str:
        return read_from(WORKER_LOG, self.worker_offset)

    def fleet_log(self) -> str:
        return read_from(FLEET_LOG, self.fleet_offset)

    async def start(self, wid: str, action: str = "restart_consumer", environment: str = "staging", target: str = "orders-consumer",
                    proposal: Optional[str] = None, retrieve: bool = False, approval_timeout: int = 900) -> WorkflowHandle:
        incident = Incident(incident_id=wid, environment=environment, target=target,
                            summary=f"{target} has 0 active members and consumer lag keeps climbing")
        inp = RemediationInput(incident=incident, proposal_json=proposal or proposal_json(action, environment, target), retrieve=retrieve,
                               approval_timeout_seconds=approval_timeout)
        h = await self.client.start_workflow(RemediationWorkflow.run, inp, id=wid, task_queue=TASK_QUEUE, id_reuse_policy=INCIDENT_ID_REUSE)
        print(f"  started {wid} run_id={h.result_run_id}", flush=True)
        return self.track(wid, h.result_run_id)

    def track(self, wid: str, run_id: str) -> WorkflowHandle:
        h = self.client.get_workflow_handle_for(RemediationWorkflow.run, wid, run_id=run_id)
        self.handles.append(h)
        return h

    async def outcome(self, h: WorkflowHandle, timeout: float = 180) -> str:
        try:
            return (await asyncio.wait_for(h.result(), timeout)).status
        except WorkflowFailureError as e:
            return getattr(e.cause, "type", None) or str(e.cause)

    async def wait_for(self, fn: Callable, timeout: float, interval: float = 0.25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = fn()
            if asyncio.iscoroutine(value):
                value = await value
            if value:
                return value
            await asyncio.sleep(interval)
        return None

    async def wait_status(self, h: WorkflowHandle, status: str, timeout: float = 60) -> bool:
        async def current():
            return (await h.query(RemediationWorkflow.current_status))["status"] == status
        return bool(await self.wait_for(current, timeout))

    async def history(self, h: WorkflowHandle) -> dict:
        return summarize_history((await h.fetch_history()).events)

    async def scan_secrets(self) -> None:
        blobs = [e.SerializeToString() for h in self.handles for e in (await h.fetch_history()).events]
        blobs.append(self.worker_log().encode())
        self.check("no credential material in workflow history or worker log", secret_hits(blobs) == 0,
                   {"histories": len(self.handles), "hits": secret_hits(blobs)})

    def kill_worker(self, pid: int) -> None:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        deadline = time.monotonic() + 15
        while pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "gax.cli", *args], capture_output=True, text=True, timeout=60, cwd=ROOT)

    async def cleanup(self) -> None:
        if self.mongo_stopped or not mongo_ready(self.s.mongo_uri):
            docker("start", CONTAINER)
            await self.wait_for(lambda: mongo_ready(self.s.mongo_uri), 180, 1)
        for h in self.handles:
            try:
                if (await h.describe()).status == WorkflowExecutionStatus.RUNNING:
                    await h.terminate("demo cleanup")
                    print(f"  terminated {h.id} run_id={h.run_id}", flush=True)
            except Exception as e:
                print(f"  cleanup could not terminate {h.id}: {e}", flush=True)
        self.fleet.delete("/admin/faults")
        self.db[GRANTS_COLLECTION].delete_many({})
        if running_worker() is None:
            start_worker()
        self.fleet.close()
        self.db.client.close()
