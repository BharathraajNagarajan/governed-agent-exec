# 0001: Authorization layering

Status: Accepted (M0)

## Context

The workflow executes actions proposed by an LLM against fleet-api. Two questions must both be answered before any request leaves the worker:

1. Is this specific action, with these parameters, in this environment, allowed right now?
2. Is this worker allowed to hold a credential that can call fleet-api for this action?

## Decision

Authorization has two layers, and both must pass.

**Layer 1: in-app action policy.** Deterministic, versioned code evaluated in `evaluate_policy`. Input is the validated `ActionProposal` (action, target, environment, params). Output is `ALLOW`, `REQUIRE_APPROVAL` or `DENY` plus the policy version, which is written to the audit record.

| Action | staging | prod |
|---|---|---|
| `restart_consumer` | ALLOW | REQUIRE_APPROVAL |
| `scale_consumer` | ALLOW within 1..10 | ALLOW within 1..10 |
| `pause_pipeline` | REQUIRE_APPROVAL | REQUIRE_APPROVAL |
| `reset_consumer_offset` | REQUIRE_APPROVAL | DENY |

A `DENY` ends the workflow before `execute_action` is scheduled, so fleet-api receives zero requests.

**Layer 2: credential issuance.** `execute_action` obtains a short-lived credential from a credential broker (Keycard via `@grant`; a LOCAL-ONLY broker until then, see 0002). fleet-api validates that credential.

## Why the credential layer alone is not enough

A credential-issuance system decides whether an identity may obtain a token for a resource and scope. It does not see the proposal. It cannot express rules that depend on parameter values or on the combination of action and environment, for example:

- `scale_consumer` is allowed only when `1 <= replicas <= 10`.
- `reset_consumer_offset` is `DENY` in prod but `REQUIRE_APPROVAL` in staging.
- Some actions need a human approval recorded in the workflow before they run.

Those rules live in Layer 1, where they are deterministic, testable and versioned. Layer 2 enforces who may touch the system at all, and limits the blast radius if Layer 1 has a bug.

## Re-check on every attempt

The credential is requested inside the activity on every attempt, never cached in workflow state or passed as an argument. Consequences:

- A revoked grant stops the next retry, even mid-run. Access denied is raised as a non-retryable error; a transient broker error is retryable.
- Credentials never enter workflow history. A test scans history for credential material.
- The LLM never sees credentials; it only produces the proposal.

The policy decision is made once per proposal and recorded in history, so replay is deterministic. The credential check is repeated per attempt because it depends on live external state.

## Consequences

- Two places to update when a new action is added: the policy table and the grant scope.
- Every run's audit record carries the policy version and decision, and whether credential issuance succeeded.
