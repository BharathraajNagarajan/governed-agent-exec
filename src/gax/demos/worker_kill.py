import asyncio
import time
from gax.demos.harness import final_attempt, outcomes, pid_alive, running_worker, start_worker, worker_pids

NAME = "worker_kill"
TITLE = "Real worker process hard-killed while execute_action attempt 1 is in flight"


async def run(d):
    wid = d.incident_id()
    pid = running_worker()
    d.fault("restart_consumer", latency_ms=8000)
    before = d.consumer()
    h = await d.start(wid)
    pending = await d.wait_for(lambda: next((r for r in d.ledger(wid) if r["attempt"] == 1 and r["status"] == "PENDING"), None), 60)
    if not d.check("ledger shows execute_action attempt 1 PENDING", pending, pending, reproduce=True):
        return
    await asyncio.sleep(2)
    at_kill = {"fleet_counters": d.counters(), "launcher_pid": worker_pids()[0], "real_pid": pid}
    killed = time.monotonic()
    d.kill_worker(pid)
    d.check("real worker PID hard-killed during attempt 1", not pid_alive(pid), at_kill, reproduce=True)
    new_pid = start_worker()
    d.evidence["worker"] = {"killed_pid": pid, "restarted_pid": new_pid, **at_kill}
    status = await d.outcome(h, 150)
    d.evidence["kill_to_completion_s"] = round(time.monotonic() - killed, 1)
    rows, hist, after = d.ledger(wid), await d.history(h), d.consumer()
    d.evidence.update(ledger=rows, history=hist)
    retry = final_attempt(hist, "execute_action")
    d.check("execute_action retried after start_to_close timeout (history lastFailure)",
            retry and retry["attempt"] >= 2 and "StartToClose" in (retry["last_failure"] or ""), retry)
    d.check("killed attempt 1 never finished in ledger", rows and rows[0]["attempt"] == 1 and rows[0]["status"] == "PENDING", rows[:1])
    d.check("attempt 2 outcome REPLAYED or APPLIED", len(rows) == 2 and rows[1]["attempt"] == 2 and rows[1]["outcome"] in ("REPLAYED", "APPLIED"),
            outcomes(rows))
    d.check("restart_count increased exactly once", after["restart_count"] - before["restart_count"] == 1,
            {"before": before["restart_count"], "after": after["restart_count"]})
    d.check("restarted worker logged its real pid", f"worker pid={new_pid}" in d.worker_log(), new_pid)
    d.check("status VERIFIED", status == "VERIFIED", status)
