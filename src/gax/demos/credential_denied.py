from gax.demos.harness import final_attempt, outcomes

NAME = "credential_denied"
TITLE = "Credential grant revoked: non-retryable denial, then restore and rerun the same incident id"


async def run(d):
    wid = d.incident_id()
    before = d.consumer()
    d.broker.revoke("restart_consumer")
    h1 = await d.start(wid)
    status1 = await d.outcome(h1)
    rows1, hist1, counters1 = d.ledger(wid, h1.run_id), await d.history(h1), d.counters()
    d.evidence["denied_run"] = {"run_id": h1.run_id, "ledger": rows1, "history": hist1, "counters": counters1}
    failed = next((f for f in hist1["failures"] if f["activity"] == "execute_action"), {})
    d.check("broker denied the credential", outcomes(rows1) == ["CREDENTIAL_DENIED"], outcomes(rows1), reproduce=True)
    d.check("status CREDENTIAL_DENIED", status1 == "CREDENTIAL_DENIED", status1)
    d.check("exactly 1 execute_action attempt, failure non-retryable",
            len(rows1) == 1 and (final_attempt(hist1, "execute_action") or {}).get("attempt") == 1 and failed.get("non_retryable"), failed)
    d.check("fleet-api restart counter 0", counters1.get("restart_consumer", 0) == 0, counters1)
    d.check("worker log shows LOCAL-ONLY denial for this incident",
            any("LOCAL-ONLY broker denied credential" in l and wid in l for l in d.worker_log().splitlines()), wid)

    d.broker.restore("restart_consumer")
    h2 = await d.start(wid)
    status2 = await d.outcome(h2)
    rows2, after, counters2 = d.ledger(wid, h2.run_id), d.consumer(), d.counters()
    d.evidence["rerun"] = {"run_id": h2.run_id, "ledger": rows2, "counters": counters2}
    d.check("rerun of same incident id VERIFIED", status2 == "VERIFIED", status2)
    d.check("rerun ledger attempt 1 APPLIED", outcomes(rows2) == ["APPLIED"], outcomes(rows2))
    d.check("state changed exactly once overall", after["restart_count"] - before["restart_count"] == 1 and counters2.get("restart_consumer") == 1,
            {"before": before["restart_count"], "after": after["restart_count"], "counters": counters2})
