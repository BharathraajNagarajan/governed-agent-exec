from gax.demos.harness import ROOT

NAME = "policy_deny"
TITLE = "Prod reset_consumer_offset is denied by policy before any fleet request"


async def run(d):
    wid = d.incident_id()
    proposal = (ROOT / "scripts" / "proposals" / "prod-reset-offset.json").read_text(encoding="utf-8")
    before = d.consumer("prod")
    h = await d.start(wid, environment="prod", proposal=proposal)
    status = await d.outcome(h)
    hist, counters, after = await d.history(h), d.counters(), d.consumer("prod")
    decision = (d.audit(wid, h.run_id) or {}).get("detail", {}).get("decision")
    d.evidence.update(history=hist, counters=counters, decision=decision)
    d.check("policy decision DENY", (decision or {}).get("decision") == "DENY", decision, reproduce=True)
    d.check("status DENIED", status == "DENIED", status)
    d.check("fleet-api counters empty", counters == {}, counters)
    d.check("no snapshot_state or execute_action scheduled", not {"snapshot_state", "execute_action"} & set(hist["scheduled"]), hist["scheduled"])
    d.check("prod offset unchanged", after["offset"] == before["offset"], {"before": before["offset"], "after": after["offset"]})
    d.check("no ledger rows", d.ledger(wid) == [], d.ledger(wid))
