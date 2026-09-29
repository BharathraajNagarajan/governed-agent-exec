from gax.models import ActionProposal, PolicyDecision

POLICY_VERSION = "policy-v1"
SCALE_MIN = 1
SCALE_MAX = 10

TABLE = {
    ("restart_consumer", "staging"): "ALLOW",
    ("restart_consumer", "prod"): "REQUIRE_APPROVAL",
    ("pause_pipeline", "staging"): "REQUIRE_APPROVAL",
    ("pause_pipeline", "prod"): "REQUIRE_APPROVAL",
    ("reset_consumer_offset", "staging"): "REQUIRE_APPROVAL",
    ("reset_consumer_offset", "prod"): "DENY",
}


def _decide(decision: str, reason: str) -> PolicyDecision:
    return PolicyDecision(decision=decision, reason=reason, policy_version=POLICY_VERSION)


def evaluate(proposal: ActionProposal) -> PolicyDecision:
    action, env = proposal.action, proposal.environment
    if action == "scale_consumer":
        replicas = proposal.params.replicas
        if replicas is None:
            return _decide("DENY", "scale_consumer requires params.replicas")
        if SCALE_MIN <= replicas <= SCALE_MAX:
            return _decide("ALLOW", f"scale_consumer replicas={replicas} within {SCALE_MIN}..{SCALE_MAX} in {env}")
        return _decide("DENY", f"scale_consumer replicas={replicas} outside {SCALE_MIN}..{SCALE_MAX} in {env}")
    decision = TABLE[(action, env)]
    return _decide(decision, f"{action} in {env} is {decision}")
