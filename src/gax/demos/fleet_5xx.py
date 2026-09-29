from gax.demos.harness import final_attempt, outcomes

NAME = "fleet_5xx"
TITLE = "fleet-api returns HTTP 500 twice, then succeeds"


async def run(d):
    wid = d.incident_id()
    d.fault("restart_consumer", fail_next=2)
    before = d.consumer()
    h = await d.start(wid)
    status = await d.outcome(h)
    rows, hist, after, counters = d.ledger(wid), await d.history(h), d.consumer(), d.counters()
    d.evidence.update(ledger=rows, history=hist, counters=counters)
    got, last = outcomes(rows), final_attempt(hist, "execute_action") or {}
    d.check("injected 500s reached execute_action", "HTTP_500" in got, got, reproduce=True)
    d.check("ledger attempts HTTP_500, HTTP_500, APPLIED", got == ["HTTP_500", "HTTP_500", "APPLIED"], got)
    d.check("history: execute_action attempt 3, lastFailure fleet-api HTTP 500",
            last.get("attempt") == 3 and "HTTP 500" in (last.get("last_failure") or ""), last)
    d.check("fleet-api received 3 restart requests", counters.get("restart_consumer") == 3, counters)
    d.check("state changed exactly once", after["restart_count"] - before["restart_count"] == 1,
            {"before": before["restart_count"], "after": after["restart_count"]})
    d.check("status VERIFIED", status == "VERIFIED", status)
