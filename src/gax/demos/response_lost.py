from gax.demos.harness import final_attempt, outcomes

NAME = "response_lost"
TITLE = "fleet-api commits the restart, then drops the response"


async def run(d):
    wid = d.incident_id()
    d.fault("restart_consumer", drop_next=1)
    before = d.consumer()
    h = await d.start(wid)
    status = await d.outcome(h)
    rows, hist, after, counters = d.ledger(wid), await d.history(h), d.consumer(), d.counters()
    d.evidence.update(ledger=rows, history=hist, counters=counters)
    got, last = outcomes(rows), final_attempt(hist, "execute_action") or {}
    dropped = [l for l in d.fleet_log().splitlines() if "commit-then-drop" in l and wid in l]
    d.check("fleet-api committed then dropped the response", dropped, dropped, reproduce=True)
    d.check("ledger attempts TRANSPORT_ERROR, REPLAYED", got == ["TRANSPORT_ERROR", "REPLAYED"], got)
    d.check("history: execute_action attempt 2, lastFailure transport error",
            last.get("attempt") == 2 and "transport error" in (last.get("last_failure") or ""), last)
    d.check("fleet-api received 2 restart requests", counters.get("restart_consumer") == 2, counters)
    d.check("state changed exactly once", after["restart_count"] - before["restart_count"] == 1,
            {"before": before["restart_count"], "after": after["restart_count"]})
    d.check("status VERIFIED", status == "VERIFIED", status)
