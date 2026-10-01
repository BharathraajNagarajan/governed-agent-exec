# Failure semantics

One demo per failure-matrix row (docs/design.md). Every demo runs against the live stack (atlas-local, Temporal dev server, fleet-api, the real `gax.worker` process), resets fleet state, counters, faults and broker grants first, checks evidence, and cleans up faults, grants, running workflows and processes afterwards.

```powershell
.\scripts\start-stack.ps1
.venv\Scripts\gax.exe demo list
.venv\Scripts\gax.exe demo run fleet_5xx
.venv\Scripts\gax.exe demo run all
```

`demo run` starts a worker via `scripts\start-worker.ps1` when none is tracked and stops it at the end. Results: `.gax/demo-results/<name>.json` (every check with its observed value, notes, incident ids, evidence) and `.gax/demo-results/summary.json`. Sanitized copies from the observed run below are in [docs/evidence/](evidence/).

A demo is **PASS** only if every check passes, **NOT REPRODUCED** if any check marked as reproducing the failure fails (for example no 429 occurred), otherwise **FAIL**. Every demo also scans all its workflow histories and its slice of the worker log for any JWT (`eyJ<header>.eyJ<payload>.<signature>`), the `eyJhbGciOi` prefix, and `Bearer <token>`.

Except `voyage_429`, demos inject the proposal (`proposal_json`, the `--proposal-file` path) and skip retrieval (`retrieve=False`, CLI `--no-retrieval`). No LLM or Voyage calls are made, so runs are deterministic and cost nothing.

## Retry configuration (src/gax/workflows.py)

| Activities | start_to_close | Retry | Non-retryable types |
|---|---|---|---|
| `snapshot_state`, `execute_action`, `verify_outcome` | 20 s | 1 s initial, x2, max 10 s, 6 attempts | `CredentialDenied`, `FleetRejected` |
| `propose_action` | 180 s | 2 s initial, x2, max 30 s, 3 attempts | `MalformedProposal`, `ProposerRejected` |
| `retrieve_context` | 150 s | 5 s initial, 2 attempts (Voyage client retries 429 internally 6x, 2/4/8/16/32 s) | `VoyageAuthError`, `VoyageRequestError`, `RetrievalNotReady` |
| `record_audit` | 20 s | 1 s initial, max 30 s, 20 attempts | none |

## Idempotency

`execute_action` sends `Idempotency-Key: <workflow_id>:execute_action`. fleet-api applies the state change and stores the response under that key in one Mongo transaction (`fleet_idempotency`); a repeat returns the stored body with `Idempotent-Replayed: true`, and the ledger records `REPLAYED`. The key is per incident id, not per run: a rerun of a failed incident id that had already applied would replay, not apply again. Each attempt writes its own `action_ledger` row (`PENDING` first, then outcome), because Temporal history keeps only the final attempt of a retried activity.

## Evidence locations

- Temporal history: `temporal workflow show --workflow-id <id>`; the final `ActivityTaskStarted` carries `attempt` and `lastFailure`. Pending retries: `describe` → `pending_activities`.
- Mongo `gax.action_ledger` (per attempt), `gax.audit_events` (`_id = workflow_id:run_id`), `gax.incidents`, `gax.fleet_state`.
- fleet-api `GET /admin/counters` (requests per action since reset), `.gax/logs/fleet-api.err.log` (fault lines).
- `.gax/logs/worker.log` (appended across worker restarts; demos read from their start offset).

## Observed run

`gax demo run all`, 2026-09-29 16:55 to 16:58 PDT, host Windows 11, Temporal CLI 1.9.1, temporalio 1.33.0, atlas-local `preview`. All 12 demos passed in one run. Total 169.8 s as measured by the runner. Summary: [docs/evidence/summary.json](evidence/summary.json). `policy_deny` was re-run on its own the same day (PASS 7/7, 0.9 s); its §6 values and [docs/evidence/policy_deny.json](evidence/policy_deny.json) come from that rerun, and the table keeps the run-all value.

| Demo | Result | Seconds | Checks |
|---|---|---|---|
| worker_kill | PASS | 29.9 | 9/9 |
| fleet_5xx | PASS | 3.8 | 7/7 |
| response_lost | PASS | 1.7 | 7/7 |
| credential_denied | PASS | 0.9 | 9/9 |
| credential_transient | PASS | 3.7 | 8/8 |
| policy_deny | PASS | 0.4 | 7/7 |
| approval_timeout | PASS | 6.7 | 7/7 |
| approval_worker_restart | PASS | 16.8 | 11/11 |
| llm_malformed | PASS | 0.3 | 6/6 |
| voyage_429 | PASS | 64.0 | 7/7 |
| mongo_down | PASS | 19.9 | 10/10 |
| duplicate_start | PASS | 16.5 | 7/7 |

## Observed run: KEYCARD mode

`gax demo run all` with `CREDENTIAL_MODE=keycard`, 2026-10-01 04:16 to 04:21 PDT (11:16 to 11:21 UTC). Host Windows 11, Python 3.12.10, Temporal CLI 1.9.1 (Server 1.32.0), temporalio 1.33.0, keycardai-oauth 0.31.3, PyJWT 2.15.1, atlas-local `preview`. The worker printed `credential_mode=KEYCARD`, and fleet-api `/healthz` reported `auth_mode` `KEYCARD`. Every credential was a real Keycard client-credentials token, verified by fleet-api against the zone JWKS. All 12 demos passed in one run, 310.9 s total as measured by the runner. Sanitized results: [docs/evidence/keycard/](evidence/keycard/).

In this mode every demo has two extra checks: all of its ledger rows carry `credential_mode` `KEYCARD` (when it wrote any rows), and `/healthz` reports `auth_mode` `KEYCARD`. The secret scan (any JWT, the `eyJhbGciOi` prefix, or `Bearer <token>`) found 0 hits in every demo. Keycard RS256 headers do not have to start with `eyJhbGciOi`, so the scan matches the JWT shape, not that prefix.

| Demo | Result | Seconds | Checks |
|---|---|---|---|
| worker_kill | PASS | 35.2 | 11/11 |
| fleet_5xx | PASS | 6.2 | 9/9 |
| response_lost | PASS | 3.9 | 9/9 |
| credential_denied | PASS | 91.7 | 14/14 |
| credential_transient | PASS | 5.4 | 11/11 |
| policy_deny | PASS | 0.5 | 8/8 |
| approval_timeout | PASS | 8.8 | 9/9 |
| approval_worker_restart | PASS | 22.0 | 13/13 |
| llm_malformed | PASS | 0.5 | 7/7 |
| voyage_429 | PASS | 67.9 | 9/9 |
| mongo_down | PASS | 28.6 | 12/12 |
| duplicate_start | PASS | 27.6 | 9/9 |

**credential_denied (KEYCARD).** Revocation is a Keycard policy, not simulated (ADR 0002 §5). The demo printed `ACTIVATE gax-zone-policies in the Keycard console now` and then probed with a real mint (`restart_consumer`) every 10 s. The operator activated the set. Probes 1-3 were issued and probe 4 was denied, 33.7 s after the prompt. The incident then ran:

- Ledger `[CREDENTIAL_DENIED]`, error `KEYCARD access_denied for urn:gax:fleet-api:restart: Access to "fleet-api restart" is denied by Policy "gax-forbid-restart" in version 3 of Policy Set "gax-zone-policies".`
- History: exactly 1 `execute_action` attempt, `CredentialDenied`, `non_retryable=true`, `RETRY_STATE_NON_RETRYABLE_FAILURE`.
- Counters `{get_state: 1}`, so fleet-api received 0 restart requests. The worker log shows `KEYCARD broker CredentialDenied` for this incident.

The demo then printed `RE-ACTIVATE default-zone-policies now`. Probes 1-5 were denied and probe 6 was issued, 53.4 s after the prompt. The rerun of the same incident id was VERIFIED with ledger `[APPLIED]`, and `restart_count` went 0 → 1 with `restart_consumer=1` overall. The operator's click times were not recorded, so these elapsed times run from the prompt to the first changed probe. They are upper bounds on time-to-effect and do not measure it.

**credential_transient (KEYCARD).** The transient failure is **SIMULATED**: Keycard has no fault injection (ADR 0002 §6). Both failed attempts raised `CredentialUnavailable` in front of the mint without calling Keycard. History lastFailure was `SIMULATED transient failure in front of KEYCARD for restart_consumer`, and an added check confirms the word SIMULATED. The ledger was `[CREDENTIAL_UNAVAILABLE, CREDENTIAL_UNAVAILABLE, APPLIED]`, and attempt 3 minted a real Keycard token. fleet-api received 1 restart, and the result was VERIFIED. The demo records a NOTE saying the same.

**Keycard mints.** The run made 46 mints: 36 by the worker (35 issued and 1 denied, the `credential_denied` incident) and 10 probe mints by the demo (4 issued, 6 denied). The 2 simulated transients made no Keycard call. Counts come from `KEYCARD broker issued credential` and `KEYCARD broker CredentialDenied` in the worker log, plus the probe lines in the demo console. Separately, the real-LLM incident `INC-K3-001` made 3 mints (snapshot, execute, verify). It was VERIFIED with `credential_mode` `KEYCARD` in both ledger and audit, and its 47 history events had 0 JWT-scan hits.

## 1. worker_kill

- **Trigger:** 8 s latency fault on `restart_consumer`; staging restart incident; wait until the ledger shows attempt 1 `PENDING`, wait 2 s more so the request is in flight, `taskkill /F` the real worker PID (not the launcher), start a new worker.
- **Expected:** Temporal times the attempt out after `start_to_close` (20 s) and retries on the new worker; attempt 2 is `REPLAYED` if attempt 1's request committed, else `APPLIED`; `restart_count` +1 exactly once.
- **Survives:** workflow state and the pending activity (Temporal server); the in-flight HTTP request (fleet-api finishes and commits it after the client dies); ledger row of the dead attempt.
- **Retried:** `execute_action` (timeout is retryable). **Not retried:** nothing is re-proposed or re-evaluated; completed activities are replayed from history.
- **Idempotency:** attempt 2 reuses the same key, so fleet-api replays the committed result.
- **Observed:** killed real pid 10540 (launcher 29444) with fleet counters `restart_consumer=1` already. New worker pid 25300. History: `execute_action` attempt 2, lastFailure `activity StartToClose timeout`. Ledger `[PENDING, REPLAYED]`: attempt 1 never finished. `restart_count` 0 → 1. VERIFIED 27.1 s after the kill. Incident `DEMO-WORKER-KILL-20260929165525`. Temporal UI screenshot: [images/temporal-worker-kill.png](images/temporal-worker-kill.png).

## 2. fleet_5xx

- **Trigger:** `fail_next=2` on `restart_consumer` (500 before commit).
- **Expected:** attempts `HTTP_500, HTTP_500, APPLIED`; one state change; VERIFIED.
- **Retried:** 5xx and transport errors map to `FleetUnavailable` (retryable). **Not retried:** 4xx maps to `FleetRejected` (non-retryable): the request itself is wrong and repeating it cannot help.
- **Idempotency:** the injected 500s happen before the state change, so nothing is stored; attempt 3 applies.
- **Observed:** ledger `[HTTP_500, HTTP_500, APPLIED]`; history attempt 3, lastFailure `fleet-api HTTP 500` (type `FleetUnavailable`); counters `restart_consumer=3, get_state=2`; `restart_count` 0 → 1; VERIFIED.

## 3. response_lost

- **Trigger:** `drop_next=1`: fleet-api commits the restart, then closes the connection without a response.
- **Expected:** attempt 1 `TRANSPORT_ERROR`, attempt 2 `REPLAYED`; one state change.
- **Survives:** the committed state change and its stored response.
- **Retried:** transport error (`FleetUnavailable`). The worker cannot know whether the request committed, so it must retry, and it relies on the key to make that safe.
- **Observed:** fleet log `FAULT commit-then-drop action=restart_consumer idempotency_key=DEMO-RESPONSE-LOST-20260929165600:execute_action`; ledger `[TRANSPORT_ERROR, REPLAYED]`; history attempt 2, lastFailure `fleet-api transport error: RemoteProtocolError`; counters `restart_consumer=2`; `restart_count` 0 → 1; VERIFIED.

## 4. credential_denied

- **Trigger:** `revoke restart_consumer` on the LOCAL-ONLY broker; staging restart; then `restore` and start the same incident id again.
- **Expected:** CREDENTIAL_DENIED after exactly one attempt, zero restart requests; rerun VERIFIED.
- **Not retried:** `CredentialDenied` is non-retryable: a revoked grant is a decision, not an outage, and retrying would only hammer the broker. The workflow fails (after `record_audit`), so `ALLOW_DUPLICATE_FAILED_ONLY` lets the same incident id be started again.
- **Observed:** run 1 ledger `[CREDENTIAL_DENIED]`, history failure `CredentialDenied` `non_retryable=true`, `RETRY_STATE_NON_RETRYABLE_FAILURE`; counters `{get_state: 1}` (restart 0); worker log shows `LOCAL-ONLY broker denied credential` for the incident. Rerun (new run id) ledger `[APPLIED]`, VERIFIED; overall `restart_count` 0 → 1, `restart_consumer=1`.

## 5. credential_transient

- **Trigger:** `transient restart_consumer --count 2`.
- **Expected:** `CREDENTIAL_UNAVAILABLE` twice, then `APPLIED`; VERIFIED.
- **Retried:** `CredentialUnavailable` is retryable; the credential is requested again on every attempt and never cached.
- **Observed:** ledger `[CREDENTIAL_UNAVAILABLE, CREDENTIAL_UNAVAILABLE, APPLIED]`; history attempt 3, lastFailure `LOCAL-ONLY broker transiently unavailable for restart_consumer` (type `CredentialUnavailable`); counters `restart_consumer=1` (no request without a credential); transient grant consumed and removed; `restart_count` 0 → 1; VERIFIED.

## 6. policy_deny

- **Trigger:** `scripts/proposals/prod-reset-offset.json` (prod `reset_consumer_offset`).
- **Expected:** DENIED; fleet-api receives nothing.
- **Not retried:** policy is deterministic and versioned; DENY is a completed outcome, not a failure, so the incident id cannot be reused.
- **Observed:** decision `DENY` (`reset_consumer_offset in prod is DENY`, `policy-v1`); scheduled activities `[propose_action, evaluate_policy, record_audit]`; counters `{}`; prod offset 1000 → 1000; no ledger rows; DENIED.

## 7. approval_timeout

- **Trigger:** staging `pause_pipeline` (REQUIRE_APPROVAL) with a 5 s approval timeout; then a reject path and an approve path.
- **Expected:** APPROVAL_TIMEOUT, nothing executed, late approval rejected; REJECTED path executes nothing; APPROVED path executes once.
- **Survives:** the approval wait is a durable timer in Temporal. A worker crash during the wait is demonstrated in 12.
- **Observed:** timeout: `APPROVAL_TIMEOUT`, audit approval `{decision: TIMEOUT, timeout_seconds: 5}`, scheduled `[propose_action, evaluate_policy, record_audit]`, counters `{}`; late approve → `RPCError: workflow execution already completed`. Reject: `REJECTED` by `demo-operator`, `pause_pipeline=0`, not paused. Approve: `VERIFIED`, ledger `[APPLIED]`, `pause_pipeline=1`, paused.

## 8. llm_malformed

- **Trigger:** injected proposal with `action: drop_topic`.
- **Expected:** NEEDS_HUMAN, zero fleet requests.
- **Not retried:** an injected proposal is rejected once (`MalformedProposal`, non-retryable). For LLM proposals, `propose_action` retries malformed output up to 3 times inside the activity, then fails non-retryably. Temporal does not retry it again, because re-asking forever would not converge.
- **Observed:** audit `proposal_error` `MalformedProposal` with `literal_error` on `action`; one `propose_action` failure, non-retryable; scheduled `[propose_action, record_audit]`; counters `{}`; NEEDS_HUMAN.
- **Not reproduced with the real model:** the proposer uses structured outputs, which constrain decoding to the `ActionProposal` schema (docs/m0-findings.md finding 6). The bounded 3-attempt path is covered by `tests/integration/test_remediation_workflow.py::test_malformed_proposal_needs_human_after_three_bounded_attempts` with `ProposerTestDouble`.

## 9. voyage_429

- **Trigger:** direct `embeddings` calls that bypass the client limiter until the first 429, then an incident with real retrieval (Voyage embed + rerank against `ai.mongodb.com`) and an injected proposal.
- **Expected:** real 429s in the worker log, backoff, retrieval succeeding. No 429 would be NOT REPRODUCED.
- **Retried:** 429 inside `VoyageClient` (2/4/8/16/32 s, 6 attempts) with a sliding-window limiter (3 RPM / 10K TPM); then the activity retry. **Not retried:** 401/403/other 4xx. If retrieval fails anyway, the workflow records `retrieval_error` and proceeds without context, because retrieval is supporting context only. That path is covered by `tests/integration/test_remediation_workflow.py::test_retrieval_error_proposes_with_empty_context_and_policy_still_gates`: `retrieve_context` raises `RetrievalNotReady` (non-retryable by the retry policy, one attempt, `RETRY_STATE_NON_RETRYABLE_FAILURE`), the audit has `retrieval_error` and `context: []`, `ProposerTestDouble` receives an empty context, policy returns ALLOW (`policy-v1`), and the run ends VERIFIED.
- **Observed:** direct probes `[200, 200, 200, 429]`. Worker: `voyage 429 path=embeddings` attempts 1, 2, 3 with backoff 2.0, 4.0, 8.0 s (16:56:34, :36, :40, worker log local time). The 4th attempt was held by the client limiter, which counts the 429'd attempts, until 60 s after the first 429, then `embeddings 200` at 16:57:34 and `rerank 200` at 16:57:36. Five chunks retrieved (top: `consumer-lag-no-active-members#symptoms`, rerank 0.8359), no `retrieval_error`; VERIFIED.

## 10. mongo_down

- **Trigger:** staging `pause_pipeline` incident waits for approval; `docker stop gae-atlas-local`; approve; watch pending activities; `docker start`.
- **Expected:** retryable failures while Mongo is down; workflow completes after it returns; audit record exists.
- **Survives:** everything in Temporal (the approval Update is accepted while Mongo is down). Mongo data is on the `gae-atlas-data` volume.
- **Retried:** every Mongo error in `snapshot_state`, `execute_action`, `verify_outcome`, `record_audit` is mapped to `MongoUnavailable` (retryable). The first Mongo call is the broker's grant lookup, so `snapshot_state` fails first. The budget for fleet activities is 6 attempts (about 55 s with the 5 s server-selection timeout per attempt). Mongo down for longer than that would end `FAILED`; `record_audit` has 20 attempts up to 30 s apart (several minutes).
- **Observed:** `docker stop` returned 0 after 2.0 s, container `ExitCode` 137 (128 + SIGKILL) although `docker stop` returned well inside its 10 s grace period; the container was also `Exited (137)` at the start of this session after the M1 `stop-stack.ps1`. Cause not investigated. While down: `snapshot_state` attempt 3, lastFailure `mongo unavailable: ServerSelectionTimeoutError`. Mongo ready 2.6 s after `docker start`, 14.8 s after approval. History: `snapshot_state` attempt 3, type `MongoUnavailable`. Audit record `DEMO-MONGO-DOWN-20260929165737:01a0ef9a-3d11-7c09-86ed-03a4e4a2e561` status VERIFIED; `pause_pipeline=1`; VERIFIED. fleet-api and `record_audit` did not see an outage in this run: Mongo was back before they ran.

## 11. duplicate_start

- **Trigger:** `gax incident start` (CLI subprocess) with a 6 s latency fault; the same command again while it is executing and again after it completes.
- **Expected:** both duplicates rejected; one run; one state change.
- **Mechanism:** workflow id = incident id with `ALLOW_DUPLICATE_FAILED_ONLY`; Temporal rejects a start while a run is open and after a successful close.
- **Observed:** first start exit 0; while running exit 2 `rejected: incident DEMO-DUPLICATE-START-20260929165758 is running or already completed`; after completion the same, exit 2; latest run id equals the first; ledger `[APPLIED]`, `restart_consumer=1`, `restart_count` 1; VERIFIED.

## 12. approval_worker_restart

- **Trigger:** staging `pause_pipeline` (REQUIRE_APPROVAL, 900 s timeout); wait for `AWAITING_APPROVAL`; `taskkill /F` the real worker PID (not the launcher); start a new worker; send the `approve` Update.
- **Expected:** the workflow stays RUNNING with no worker; the new worker replays history, rebuilds `AWAITING_APPROVAL`, and its validator accepts the Update; nothing is re-proposed or re-evaluated; pause applied once; audit record present; VERIFIED.
- **Survives:** the workflow, its completed `propose_action` / `evaluate_policy` results and the approval timer (Temporal server). The in-memory workflow state on the dead worker is lost and rebuilt by replay.
- **Retried:** nothing. No activity was in flight at the kill.
- **Observed:** killed real pid 25300 (launcher 28840) at `AWAITING_APPROVAL`, fleet counters `{}`. `describe` status RUNNING with no worker. New worker pid 13720. `approve` returned `APPROVED` 15.9 s after the kill (this includes the new worker's startup). History: `WorkflowExecutionUpdateAccepted` (event 23, `approve`) follows a `WorkflowTaskStarted` with identity `13720@<host>`, so the new worker accepted it. Scheduled `[propose_action, evaluate_policy, snapshot_state, execute_action, verify_outcome, record_audit]` (each once). Ledger `[APPLIED]`; counters `{get_state: 2, pause_pipeline: 1}`; paused false → true. Audit `DEMO-APPROVAL-WORKER-RESTART-20260929165615:01a0ef98-fbe2-7256-a698-61c068e68f53`, status VERIFIED, approval `{decision: APPROVED, by: demo-operator}`. VERIFIED.

## Findings

1. **Temporal drops oversized activity failures.** In the first response_lost run, history showed lastFailure `"Failure exceeds size limit."` instead of the transport error. The server (string found in `temporal.exe`) replaces a retry's lastFailure when the serialized failure is too large. The Python SDK serializes the implicit `__context__` chain, so the httpcore → httpx → `ApplicationError` chain (three stack traces) was too large. The same happened with pymongo `ServerSelectionTimeoutError` in mongo_down. Fix: transport errors are raised `from None`, and Mongo errors in fleet/audit activities become a compact `MongoUnavailable`. Both demos now show the real cause. The integration test with the mock transport did not reproduce the truncation (shorter chain); only the live demo did.
2. **Host sleep stalls the stack.** One response_lost attempt took 1888 s and failed with fleet-api 500 on `/admin/reset`: Windows was asleep 12:50–13:21 (Kernel-Power events 42/107). Not a code defect; the rerun passed.
3. **The Voyage client limiter counts rejected attempts.** After three 429s the limiter itself held the 4th attempt for about 46 s. That is conservative, and it is what made the call succeed on its first unthrottled try.
4. **atlas-local stop exits 137** even though `docker stop` returns after about 3 s.

## NOT VERIFIED

- Real-model malformed output (see 8).
- Mongo down during `execute_action` after the fleet commit, or during `record_audit`; only the pre-execute window was exercised.
- Mongo down longer than the fleet retry budget (expected `FAILED`, not run).
- Worker kill before the request reaches fleet-api (expected attempt 2 `APPLIED`); the demo kills after it is in flight.
- Temporal server crash.
- Keycard time-to-effect of a policy-set flip (only upper bounds, see Observed run: KEYCARD mode).
