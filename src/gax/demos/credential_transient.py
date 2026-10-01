from gax.credentials.local_only_broker import GRANTS_COLLECTION
from gax.demos.harness import final_attempt, outcomes

NAME = "credential_transient"
TITLE = "Credential broker transiently unavailable twice, then issues"


async def run(d):
    wid = d.incident_id()
    before = d.consumer()
    d.broker.set_transient("restart_consumer", 2)
    h = await d.start(wid)
    status = await d.outcome(h)
    rows, hist, after, counters = d.ledger(wid), await d.history(h), d.consumer(), d.counters()
    d.evidence.update(ledger=rows, history=hist, counters=counters)
    got, last = outcomes(rows), final_attempt(hist, "execute_action") or {}
    d.check("broker returned transient errors", "CREDENTIAL_UNAVAILABLE" in got, got, reproduce=True)
    d.check("ledger attempts CREDENTIAL_UNAVAILABLE x2, APPLIED", got == ["CREDENTIAL_UNAVAILABLE", "CREDENTIAL_UNAVAILABLE", "APPLIED"], got)
    d.check("history: execute_action attempt 3, lastFailure CredentialUnavailable",
            last.get("attempt") == 3 and last.get("last_failure_type") == "CredentialUnavailable", last)
    d.check("fleet-api received 1 restart request (none without a credential)", counters.get("restart_consumer") == 1, counters)
    d.check("transient grant consumed", d.db[GRANTS_COLLECTION].count_documents({}) == 0, list(d.db[GRANTS_COLLECTION].find()))
    d.check("state changed exactly once", after["restart_count"] - before["restart_count"] == 1,
            {"before": before["restart_count"], "after": after["restart_count"]})
    d.check("status VERIFIED", status == "VERIFIED", status)
    if d.broker.mode == "KEYCARD":
        d.check("history lastFailure says the transient was SIMULATED", "SIMULATED" in (last.get("last_failure") or ""), last.get("last_failure"))
        d.note("KEYCARD mode: the 2 transient failures are SIMULATED in front of the real mint (Keycard has no fault injection, ADR 0002 §6). "
               "The final execute_action mint is a real Keycard mint.")
