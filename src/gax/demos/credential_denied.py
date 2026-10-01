import asyncio
import time
from gax.credentials import CredentialDenied, CredentialUnavailable
from gax.demos.harness import final_attempt, outcomes

NAME = "credential_denied"
TITLE = "Credential grant revoked: non-retryable denial, then restore and rerun the same incident id"
PROBE_INTERVAL_S, PROBE_MAX_TRIES = 10, 30
KEYCARD_POLICY = "gax-forbid-restart"


async def probe_until(d, prompt: str, denied: bool) -> dict:
    print(f"\n  >>> OPERATOR: {prompt}\n", flush=True)
    started, results = time.monotonic(), []
    for n in range(1, PROBE_MAX_TRIES + 1):
        try:
            await asyncio.to_thread(d.broker.issue, "restart_consumer", "orders-consumer", "staging", f"{d.incident_id()}-PROBE", n)
            results.append("ISSUED")
        except CredentialDenied:
            results.append("DENIED")
        except CredentialUnavailable:
            results.append("UNAVAILABLE")
        print(f"  probe {n}: {results[-1]}", flush=True)
        if results[-1] == ("DENIED" if denied else "ISSUED"):
            break
        if n < PROBE_MAX_TRIES:
            await asyncio.sleep(PROBE_INTERVAL_S)
    probe = {"prompt": prompt, "probes": len(results), "elapsed_s": round(time.monotonic() - started, 1), "results": results}
    d.evidence.setdefault("probes", []).append(probe)
    return probe


async def run(d):
    wid = d.incident_id()
    keycard = d.broker.mode == "KEYCARD"
    before = d.consumer()
    if keycard:
        probe = await probe_until(d, "ACTIVATE gax-zone-policies in the Keycard console now", denied=True)
        if not d.check("probe mint denied after policy flip", probe["results"][-1] == "DENIED", probe, reproduce=True):
            return
    else:
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
    if keycard:
        d.check(f"ledger error names Keycard policy {KEYCARD_POLICY}", len(rows1) == 1 and KEYCARD_POLICY in (rows1[0].get("error") or ""),
                [r.get("error") for r in rows1])
        d.check("worker log shows KEYCARD denial for this incident",
                any("KEYCARD broker CredentialDenied" in l and wid in l for l in d.worker_log().splitlines()), wid)
        probe = await probe_until(d, "RE-ACTIVATE default-zone-policies now", denied=False)
        if not d.check("probe mint issued after policy restore", probe["results"][-1] == "ISSUED", probe):
            return
    else:
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
    if keycard:
        d.evidence["probe_mints"] = sum(p["probes"] for p in d.evidence["probes"])
