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
