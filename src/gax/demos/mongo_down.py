import time
from gax.demos.harness import CONTAINER, docker, final_attempt, mongo_ready
from gax.remediation.types import ApprovalInput
from gax.workflows import RemediationWorkflow

NAME = "mongo_down"
TITLE = "atlas-local stopped while an approved incident proceeds, then started again"


def pending_failures(desc) -> list[dict]:
    return [{"activity": p.activity_type.name, "attempt": p.attempt, "last_failure": p.last_failure.message or None}
            for p in desc.raw_description.pending_activities]


async def run(d):
    wid = d.incident_id()
    h = await d.start(wid, action="pause_pipeline")
    d.check("reached AWAITING_APPROVAL", await d.wait_status(h, "AWAITING_APPROVAL"), "AWAITING_APPROVAL")
    t = time.monotonic()
    stop = docker("stop", CONTAINER)
    d.mongo_stopped = True
    stopped = {"docker_stop_s": round(time.monotonic() - t, 1), "docker_stop_rc": stop.returncode,
               "container_exit_code": docker("inspect", "-f", "{{.State.ExitCode}}", CONTAINER).stdout.strip(),
               "running": docker("inspect", "-f", "{{.State.Running}}", CONTAINER).stdout.strip()}
    d.evidence["stop"] = stopped
    if not d.check("atlas-local stopped", stopped["running"] == "false" and not mongo_ready(d.s.mongo_uri), stopped, reproduce=True):
        return
    await h.execute_update(RemediationWorkflow.approve, ApprovalInput(by="demo-operator", comment="approved while mongo is down"))
    approved = time.monotonic()
    seen = []

    async def failing():
        seen[:] = pending_failures(await h.describe())
        return any(p["attempt"] >= 3 and p["last_failure"] for p in seen)

    await d.wait_for(failing, 60, 1)
    d.evidence["pending_while_down"] = list(seen)
    d.check("retryable activity failures while mongo is down", any(p["last_failure"] for p in seen), seen, reproduce=True)
    d.check("lastFailure names the cause (not truncated by the server)",
            any("mongo unavailable" in (p["last_failure"] or "") for p in seen), seen)
    t = time.monotonic()
    docker("start", CONTAINER)
    ready = await d.wait_for(lambda: mongo_ready(d.s.mongo_uri), 180, 1)
    d.mongo_stopped = not ready
    d.evidence["restart"] = {"down_after_approval_s": round(time.monotonic() - approved, 1), "mongo_ready_after_start_s": round(time.monotonic() - t, 1)}
    d.check("atlas-local back", ready, d.evidence["restart"])
    status = await d.outcome(h, 300)
    hist = await d.history(h)
    d.evidence["history"] = hist
    retried = [a for a in hist["attempts"] if a["attempt"] > 1]
    d.check("history shows retried activities with lastFailure", retried, retried)
    audit = d.audit(wid, h.run_id)
    d.check("audit record exists", audit and audit["detail"]["status"] == status, audit and {"_id": audit["_id"], "status": audit["detail"]["status"]})
    d.check("paused once", d.consumer()["paused"] and d.counters().get("pause_pipeline") == 1, d.counters())
    d.check("status VERIFIED", status == "VERIFIED", status)
