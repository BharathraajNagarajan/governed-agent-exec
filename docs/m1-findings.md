# M1 Findings

Observed results only. Run date: 2026-09-29.

## Part 0

- `.env` had no `LOCAL_BROKER_SIGNING_KEY`; one was appended from `secrets.token_urlsafe(48)` (64 bytes). Value never printed.
- `start-stack.ps1` hung when its output was piped (`powershell -File start-stack.ps1 2>&1 | cat`): the script finished but the pipe never closed until temporal and fleet-api were killed. Cause: `Start-Process -RedirectStandardOutput` uses `CreateProcess` with handle inheritance, so the detached children inherited every inheritable handle of the caller, including the pipe. Clearing the inherit flag on the three std handles was not enough (Git Bash passes further inheritable copies of the pipe). Fix: launch through `Start-Process cmd.exe /c "... 1>out 2>err"` without redirection parameters, which uses ShellExecute and inherits no handles; `cmd` owns the log file handles. Piped run now returns in 5 s.
- Anthropic `messages.parse(output_format=ActionProposal)` with the default `ANTHROPIC_MODEL=claude-sonnet-5`: accepted, served model `claude-sonnet-5`, `stop_reason=end_turn`, 2812 ms, 880 input / 109 output tokens, valid `restart_consumer` proposal. The default was not changed.

## Part 1: retrieval

- Corpus: 14 synthetic runbooks, 54 chunks (split at `##`), ids `<stem>#<heading-slug>`, sha256 per chunk text.
- Batching estimates tokens as chars/3 and caps a batch at 3000 estimated tokens; a client-side sliding-window limiter enforces 3 RPM / 10K TPM and records the actual `usage.total_tokens` after each response. 429 backoff is 2/4/8/16/32 s, 6 attempts, then `VoyageRateLimited`.
- Real ingest 1 (`gax ingest`): 54 embedded, 0 skipped, 2 batches (actual 1702 + 663 tokens), index `runbook_chunks_vec` created (1024 dims, cosine, `version` filter), 4 s wall clock. No 429.
- Real ingest 2: 0 embedded, 54 skipped, 0 batches, index `exists`, 2 s.
- Real search "consumer group orders-consumer has zero active members and lag keeps climbing", k=5, rerank on: top 3 are `consumer-lag-no-active-members#symptoms` (vector 0.8268, rerank 0.8672), `#remediation` (0.7814 / 0.7695), `#verification` (0.8485 / 0.7266). Rerank moved `#symptoms` and `#remediation` above `#verification`, which had the highest vector score.

## Part 2: RemediationWorkflow

- Tests run against `WorkflowEnvironment.start_local` using the installed Temporal CLI 1.9.1. The time-skipping test server rejected Workflow Updates (`update ... not found`), so the approval-timeout test uses a real 3 s timer instead of skipped time.
- An update sent to a completed workflow fails with `RPCError: workflow execution already completed`, not `WorkflowUpdateFailedError`; both are treated as rejection by tests and CLI.
- Workflow id = incident id with `ALLOW_DUPLICATE_FAILED_ONLY`. VERIFIED, DENIED and REJECTED complete the workflow; VERIFY_FAILED, APPROVAL_TIMEOUT, NEEDS_HUMAN, CREDENTIAL_DENIED and FAILED fail it (after `record_audit`), so only those incident ids can be rerun.
- Real run INC-M1-001 (staging, `restart_consumer`, real Voyage retrieval + rerank, real `claude-sonnet-5` proposal, 1400 in / 202 out tokens, 1 proposal attempt): status VERIFIED. fleet `restart_count` 0 -> 1, fleet-api counter `restart_consumer=1`. One ledger row: attempt 1, APPLIED, Idempotency-Key `INC-M1-001:execute_action`, credential_mode LOCAL-ONLY. Audit record has proposal, cited chunks, decision ALLOW, `policy-v1`, snapshot, execution, verification (expected 1, observed 1). Live history: 16 payloads decoded, no `eyJhbGciOi` and no `Bearer`. Worker log: 0 matches for either.
- Duplicate `gax incident start --id INC-M1-001` after completion: rejected, exit 2.
- Real run INC-M1-002 (prod, `reset_consumer_offset` via `--proposal-file scripts/proposals/prod-reset-offset.json`): status DENIED, fleet-api counters `{}` after the run (0 requests of any kind), prod offset unchanged at 1000.
- Real run INC-M1-003 (staging, payments-consumer): with `gax broker revoke --action restart_consumer`, status CREDENTIAL_DENIED after one execute attempt (ledger: attempt 1 FAILED CREDENTIAL_DENIED; fleet `restart_consumer` counter 0). After `gax broker restore`, the same incident id was started again and ended VERIFIED (ledger: new run, attempt 1 APPLIED; counter `restart_consumer=1`).
- No Voyage 429 occurred during these runs, so the live 429 path was not exercised in M1 (unit-tested with a mock transport only).
