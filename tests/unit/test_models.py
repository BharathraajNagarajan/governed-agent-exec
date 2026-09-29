import json
import pytest
from pydantic import ValidationError
from gax.models import AuditEvent, Incident, LedgerEntry, MalformedProposal, PolicyDecision, parse_proposal

VALID = {
    "action": "restart_consumer",
    "target": "orders-consumer",
    "environment": "staging",
    "params": {"replicas": None},
    "justification": "0 active members",
    "cited_chunk_ids": ["rb-1"],
}


def test_parse_valid_proposal():
    p = parse_proposal(json.dumps(VALID))
    assert p.action == "restart_consumer"
    assert p.params.replicas is None


@pytest.mark.parametrize("raw,err_type,loc", [
    (json.dumps({**VALID, "action": "drop_topic"}), "literal_error", ("action",)),
    (json.dumps({k: v for k, v in VALID.items() if k != "environment"}), "missing", ("environment",)),
    (json.dumps({**VALID, "extra": 1}), "extra_forbidden", ("extra",)),
    (json.dumps({**VALID, "params": {"replicas": 2, "force": True}}), "extra_forbidden", ("params", "force")),
    (json.dumps({**VALID, "environment": "dev"}), "literal_error", ("environment",)),
    ("Sure! Restart the consumer.", "json_invalid", ()),
])
def test_malformed_proposal(raw, err_type, loc):
    with pytest.raises(MalformedProposal) as e:
        parse_proposal(raw)
    errors = e.value.error.errors()
    assert errors[0]["type"] == err_type
    assert tuple(errors[0]["loc"]) == loc
    assert e.value.raw == raw


def test_policy_decision_rejects_unknown_decision():
    with pytest.raises(ValidationError):
        PolicyDecision(decision="MAYBE", reason="x", policy_version="policy-v1")


def test_incident_defaults_created_at():
    i = Incident(incident_id="INC-1", environment="staging", target="orders-consumer", summary="lag")
    assert i.created_at.tzinfo is not None


def test_audit_event():
    e = AuditEvent(workflow_id="INC-1", incident_id="INC-1", step="evaluate_policy", detail={"decision": "ALLOW"})
    assert e.detail["decision"] == "ALLOW"


def test_ledger_entry_validates_attempt_and_status():
    base = dict(workflow_id="INC-1", step="execute_action", idempotency_key="INC-1:execute_action", action="restart_consumer",
                target="orders-consumer", environment="staging", params={}, credential_mode="LOCAL-ONLY")
    assert LedgerEntry(**base, attempt=1, status="APPLIED").status == "APPLIED"
    with pytest.raises(ValidationError):
        LedgerEntry(**base, attempt=0, status="APPLIED")
    with pytest.raises(ValidationError):
        LedgerEntry(**base, attempt=1, status="DONE")
