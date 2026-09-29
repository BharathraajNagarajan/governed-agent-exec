from gax.demos.harness import PROPOSALS, outcomes, proposal_json

NAME = "duplicate_start"
TITLE = "Second start of a running and of a completed incident id is rejected"


async def run(d):
    wid = d.incident_id()
    PROPOSALS.mkdir(parents=True, exist_ok=True)
    path = PROPOSALS / f"{wid}.json"
    path.write_text(proposal_json("restart_consumer"), encoding="utf-8")
    args = ["incident", "start", "--id", wid, "--env", "staging", "--target", "orders-consumer", "--summary", "orders-consumer lag",
            "--proposal-file", str(path), "--no-retrieval"]
    d.fault("restart_consumer", latency_ms=6000)
    before = d.consumer()
    first = d.cli(*args)
    d.evidence["first"] = {"rc": first.returncode, "stdout": first.stdout.strip()}
    if not d.check("first start accepted", first.returncode == 0 and "started" in first.stdout, d.evidence["first"], reproduce=True):
        return
    run_id = (await d.client.get_workflow_handle(wid).describe()).run_id
    h = d.track(wid, run_id)
    await d.wait_status(h, "EXECUTING")
    running = d.cli(*args)
    d.evidence["while_running"] = {"rc": running.returncode, "stdout": running.stdout.strip()}
    d.check("duplicate start while running rejected (exit 2)", running.returncode == 2 and "rejected" in running.stdout, d.evidence["while_running"])
    status = await d.outcome(h)
    done = d.cli(*args)
    d.evidence["after_completion"] = {"rc": done.returncode, "stdout": done.stdout.strip()}
    d.check("duplicate start after completion rejected (exit 2)", done.returncode == 2 and "rejected" in done.stdout, d.evidence["after_completion"])
    latest = (await d.client.get_workflow_handle(wid).describe()).run_id
    rows, after = d.ledger(wid), d.consumer()
    d.check("still one run for the incident id", latest == run_id, {"first_run": run_id, "latest_run": latest})
    d.check("state changed once", after["restart_count"] - before["restart_count"] == 1 and d.counters().get("restart_consumer") == 1
            and outcomes(rows) == ["APPLIED"], {"restart_count": after["restart_count"], "ledger": outcomes(rows), "counters": d.counters()})
    d.check("status VERIFIED", status == "VERIFIED", status)
