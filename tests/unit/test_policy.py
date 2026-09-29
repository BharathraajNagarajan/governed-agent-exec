import pytest
from gax.models import ActionParams, ActionProposal
from gax.policy import POLICY_VERSION, evaluate


def proposal(action, environment, replicas=None):
    return ActionProposal(
        action=action,
        target="orders-consumer",
        environment=environment,
        params=ActionParams(replicas=replicas),
        justification="test",
        cited_chunk_ids=["rb-1"],
    )


@pytest.mark.parametrize("action,environment,expected", [
    ("restart_consumer", "staging", "ALLOW"),
    ("restart_consumer", "prod", "REQUIRE_APPROVAL"),
    ("pause_pipeline", "staging", "REQUIRE_APPROVAL"),
    ("pause_pipeline", "prod", "REQUIRE_APPROVAL"),
    ("reset_consumer_offset", "staging", "REQUIRE_APPROVAL"),
    ("reset_consumer_offset", "prod", "DENY"),
])
def test_table_cells(action, environment, expected):
    decision = evaluate(proposal(action, environment))
    assert decision.decision == expected
    assert decision.policy_version == POLICY_VERSION
    assert decision.reason


@pytest.mark.parametrize("environment", ["staging", "prod"])
@pytest.mark.parametrize("replicas,expected", [(0, "DENY"), (1, "ALLOW"), (5, "ALLOW"), (10, "ALLOW"), (11, "DENY"), (-1, "DENY"), (None, "DENY")])
def test_scale_boundaries(environment, replicas, expected):
    decision = evaluate(proposal("scale_consumer", environment, replicas))
    assert decision.decision == expected
    assert decision.policy_version == POLICY_VERSION


def test_deterministic():
    p = proposal("reset_consumer_offset", "prod")
    assert evaluate(p) == evaluate(p)


def test_version_is_set():
    assert POLICY_VERSION == "policy-v1"
