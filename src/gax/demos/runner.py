import json
import time
from datetime import datetime, timezone
import httpx
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from gax.config import Settings
from gax.demos import DEMOS
from gax.demos.harness import PASS, RESULTS, Demo, mongo_ready, run_script, running_worker, start_worker, summary_table, verdict


async def preflight(s: Settings) -> Client:
    problems = []
    if not mongo_ready(s.mongo_uri):
        problems.append("atlas-local not reachable")
    try:
        httpx.get(f"{s.fleet_api_url}/healthz", timeout=3).raise_for_status()
    except httpx.HTTPError:
        problems.append("fleet-api not healthy")
    try:
        client = await Client.connect(s.temporal_address, data_converter=pydantic_data_converter)
    except Exception:
        problems.append("temporal not reachable")
    if problems:
        raise SystemExit(f"stack not ready ({', '.join(problems)}); run scripts/start-stack.ps1")
    return client


async def run_demo(name: str, s: Settings, client: Client) -> dict:
    module = DEMOS[name]
    print(f"== {name}: {module.TITLE}", flush=True)
    d = Demo(name, s, client)
    started, error = time.monotonic(), None
    try:
        d.reset()
        await module.run(d)
        if d.broker.mode == "KEYCARD":
            d.keycard_checks()
        await d.scan_secrets()
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        d.check("demo ran without error", False, error)
    finally:
        await d.cleanup()
    result = {"name": name, "title": module.TITLE, "status": verdict(d.checks), "duration_s": round(time.monotonic() - started, 1),
              "finished_at": datetime.now(timezone.utc).isoformat(), "error": error, "checks": d.checks, "notes": d.notes,
              "incidents": [{"workflow_id": h.id, "run_id": h.run_id} for h in d.handles], "evidence": d.evidence}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{name}.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"== {name}: {result['status']} ({result['duration_s']}s) -> {RESULTS / (name + '.json')}\n", flush=True)
    return result


async def run(target: str, s: Settings) -> int:
    if target != "all" and target not in DEMOS:
        print(f"unknown demo {target}; choose from: all, {', '.join(DEMOS)}")
        return 2
    names = list(DEMOS) if target == "all" else [target]
    client = await preflight(s)
    own_worker = running_worker() is None
    if own_worker:
        start_worker()
    started, results = time.monotonic(), []
    try:
        for name in names:
            results.append(await run_demo(name, s, client))
    finally:
        if own_worker:
            print(f"  {run_script('stop-worker.ps1')}", flush=True)
    total = round(time.monotonic() - started, 1)
    print(summary_table(results))
    print(f"total {total}s")
    if target == "all":
        (RESULTS / "summary.json").write_text(json.dumps({"total_s": total, "results": [
            {k: r[k] for k in ("name", "status", "duration_s")} for r in results]}, indent=2), encoding="utf-8")
    return 0 if all(r["status"] == PASS for r in results) else 1
