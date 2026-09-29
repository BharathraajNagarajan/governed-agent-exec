# Design

## Thesis

Governed, durable execution of AI-proposed actions. Retrieval is supporting context.

## Domain

Incident remediation for a simulated streaming data platform.

## External system: fleet-api

- FastAPI service running as a separate process with its own state.
- Supports `Idempotency-Key` on mutating requests.
- Fault injection: fail N times, added latency, commit-then-drop-response.
- Validates JWTs when Keycard is available.

## Actions

| Action | Semantics |
|---|---|
| `restart_consumer` | non-idempotent |
| `scale_consumer(replicas)` | idempotent |
| `pause_pipeline` | idempotent |
| `reset_consumer_offset` | destructive |

## Policy (deterministic, versioned)

| Action | staging | prod |
|---|---|---|
| `restart_consumer` | ALLOW | REQUIRE_APPROVAL |
| `scale_consumer` | ALLOW within 1..10 | ALLOW within 1..10 |
| `pause_pipeline` | REQUIRE_APPROVAL | REQUIRE_APPROVAL |
| `reset_consumer_offset` | REQUIRE_APPROVAL | DENY |

## Two-layer authorization

1. In-app action policy (above).
2. Keycard credential issuance per activity (`@grant`), re-checked on every attempt.

The LLM never sees credentials.

Until Keycard is available, a clearly labelled LOCAL-ONLY credential broker is used behind the same interface.

## RemediationWorkflow (id = incident_id)

1. `retrieve_context`
2. `propose_action` — Claude, Pydantic-validated, bounded retries -> `NEEDS_HUMAN`
3. `evaluate_policy`
4. approval via Update + timeout timer
5. `execute_action` — credential via broker, `Idempotency-Key = workflow_id:step`
6. `verify_outcome`
7. `record_audit`

## MongoDB (atlas-local Docker)

Collections: `runbook_chunks`, `retrieval_config`, `incidents`, `action_ledger`, `audit_events`.

Temporal history is authoritative for execution. Mongo holds domain state and the audit projection.

## Voyage

- Explicit embed/rerank calls inside activities via `VOYAGE_BASE_URL` (no automated embedding).
- Rerank behind a flag.

## Failure matrix

Each entry needs a repeatable demo and a test.

| Failure | 
|---|
| worker kill |
| fleet-api 5xx |
| response lost after commit |
| credential access denied (non-retryable) |
| credential transient error |
| policy DENY |
| approval timeout |
| LLM malformed output |
| Voyage 429 |
| Mongo down |
| duplicate incident start |

## MVP acceptance criteria

1. One command starts the local stack.
2. Ingest embeds the corpus and skips unchanged chunks.
3. Staging `restart_consumer` runs end to end; fleet state changes exactly once.
4. Prod `reset_consumer_offset` ends DENIED and fleet-api receives zero requests.
5. Credentials never appear in workflow history (a test scans history).
6. Credential denial is non-retryable; restoration makes a rerun succeed.
7. Every run writes an audit record (proposal, decision, policy version, result, verification).
8. Unit and integration tests pass without paid APIs using clearly labelled test doubles.

## Out of scope until M1 and M2 pass

Kafka, Kubernetes, MCP, multi-agent, frontend, cloud deployment.
