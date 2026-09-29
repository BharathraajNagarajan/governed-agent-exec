import json

NAME = "llm_malformed"
TITLE = "Malformed proposal ends NEEDS_HUMAN with zero fleet requests"
MALFORMED = json.dumps({"action": "drop_topic", "target": "orders-consumer", "environment": "staging", "params": {"replicas": None},
                        "justification": "injected malformed proposal", "cited_chunk_ids": []})


async def run(d):
    wid = d.incident_id()
    h = await d.start(wid, proposal=MALFORMED)
    status = await d.outcome(h)
    hist, counters = await d.history(h), d.counters()
    error = (d.audit(wid, h.run_id) or {}).get("detail", {}).get("proposal_error")
    d.evidence.update(history=hist, counters=counters, proposal_error=error)
    d.check("validator rejected the proposal (MalformedProposal)", (error or {}).get("type") == "MalformedProposal", error, reproduce=True)
    d.check("status NEEDS_HUMAN", status == "NEEDS_HUMAN", status)
    failed = [f for f in hist["failures"] if f["activity"] == "propose_action"]
    d.check("propose_action failed once, non-retryable", len(failed) == 1 and failed[0]["non_retryable"], failed)
    d.check("zero fleet requests", counters == {}, counters)
    d.check("nothing after propose_action scheduled except record_audit", hist["scheduled"] == ["propose_action", "record_audit"], hist["scheduled"])
    d.note("The malformed proposal was injected. Real-model malformed output was NOT reproduced: the proposer uses structured "
           "outputs, which constrain decoding to the ActionProposal schema (docs/m0-findings.md finding 6).")
    d.note("The bounded 3-attempt retry on malformed model output is covered by tests/integration/test_remediation_workflow.py::"
           "test_malformed_proposal_needs_human_after_three_bounded_attempts with ProposerTestDouble.")
