# M0 Findings

Observed results only. Run date: 2026-09-29. Host: Windows 11 Pro, PowerShell, Docker Desktop.

## Versions

| Component | Version |
|---|---|
| Python | 3.12.10 |
| Temporal CLI | 1.9.1 (Server 1.32.0, UI 2.54.1) |
| temporalio | 1.33.0 |
| Docker | 29.0.1 |
| mongodb/mongodb-atlas-local | `preview`, digest `sha256:6782794e418af132694449a757a03f5ad3eac0761f1448df909ade23634410e2`, created 2026-09-25 |
| mongod (in container) | 8.3.11 |
| mongot (in container) | 1.70.4 |
| pymongo | 4.18.2 |
| httpx | 0.28.1 |
| python-dotenv | 1.2.3 |
| anthropic | 1.9.0 |
| pydantic | 2.13.5 |

## Summary

| Spike | Result |
|---|---|
| M0.1 Temporal worker kill | PASS |
| M0.2 atlas-local vector index + restart | PASS |
| M0.3 Voyage embed + rerank + $vectorSearch | PASS |
| M0.4 Anthropic ActionProposal + Pydantic | PASS |
| M0.5 Keycard | PENDING PRIVATE-BETA ACCESS |

## M0.1 Temporal (`spikes/m0_1_temporal`)

- Activity sleeps 20s, `start_to_close_timeout=30s`, RetryPolicy initial 1s, backoff 2.0, max interval 10s, max attempts 5.
- Dev server and worker started as background processes. Workflow `m0-1-kill-demo` started. Worker hard-killed with `Stop-Process -Force` 7s into attempt 1; worker log shows `attempt=1 start` with no `done`.
- Worker restarted. Log: `attempt=2 start`, `attempt=2 done`.
- `temporal workflow show`: status `COMPLETED`, result `"hello m0 (attempt 2)"`. `ActivityTaskStarted` carries `attempt: 2` and `lastFailure.message: "activity StartToClose timeout"`. Scheduled 17:56:23Z, started 17:56:54Z (about 31s later, i.e. after the 30s timeout plus 1s backoff).

## M0.2 atlas-local (`spikes/m0_2_atlas_local`)

- `docker run -d --name gae-atlas-local -p 27017:27017 -v gae-atlas-data:/data/db mongodb/mongodb-atlas-local:preview`.
- Connection string `mongodb://localhost:27017/?directConnection=true`.
- 5 docs with 4-dim vectors, `vectorSearch` index `vec_idx` (cosine) created via `create_search_index`; reached `READY`/`queryable` in seconds.
- `$vectorSearch` top 3: `1` (0.9969), `2` (0.9955), `3` (0.5552).
- After `docker restart gae-atlas-local`: count 5, same index id `6abbfd2045b55e2977e5976f`, `READY`/`queryable`, identical results and scores.

## M0.3 Voyage (`spikes/m0_3_voyage`)

- Client: plain `httpx` POST to `{VOYAGE_BASE_URL}/embeddings` and `{VOYAGE_BASE_URL}/rerank` with `Authorization: Bearer`. `VOYAGE_BASE_URL=https://ai.mongodb.com/v1`. The `voyageai` SDK was not used.
- Embedding model `voyage-4-lite`, 1024 dimensions. `voyage-4`, `voyage-3.5`, `voyage-3.5-lite` also returned 200 with 1024 dims (default output dimension).
- Rerank model `rerank-2.5-lite`.
- 5 incident-remediation runbook chunks embedded with `input_type=document` (91 tokens), 1 query with `input_type=query`, 1 rerank call (136 tokens).
- Latency per successful call: document embed 974 ms, query embed 626 ms, rerank 839 ms.
- Rerank top 3: `rb-1` 0.8828, `rb-2` 0.5898, `rb-5` 0.3066.
- Stored in `m0_spike.voyage_chunks`, `vectorSearch` index with `numDimensions: 1024`, cosine. `$vectorSearch` top 3: `rb-1` 0.846, `rb-2` 0.7763, `rb-4` 0.7118. Both rankers put the correct runbook chunk (`rb-1`) first.
- 429 occurred: the query embed call got 4 consecutive 429s and succeeded on attempt 5. Bounded retry: 5 attempts, exponential backoff 2/4/8/16s.
- The 429 response has no `Retry-After` header. Body: account without a payment method is limited to 3 RPM and 10K TPM.

## M0.4 Anthropic (`spikes/m0_4_anthropic`)

- Model `claude-opus-5-5`, `effort: low`, `client.messages.parse(output_format=ActionProposal)` (structured outputs).
- Prompt context was the 5 runbook chunks read from `m0_spike.voyage_chunks` plus a staging incident.
- Result: `stop_reason=end_turn`, 6370 ms, 906 input / 261 output tokens. Proposal: `restart_consumer`, target `orders-consumer`, `staging`, cited `rb-1`, `rb-5`.
- The raw response text was re-validated independently with `ActionProposal.model_validate_json` and equals `parsed_output`.
- Malformed cases raise a typed `MalformedProposal` wrapping the Pydantic `ValidationError`: unknown action -> `literal_error` on `action`; missing field -> `missing` on `environment`; non-JSON text -> `json_invalid`.

## M0.5 Keycard

Status: PENDING PRIVATE-BETA ACCESS. No Keycard integration was attempted. `KEYCARD_ZONE_URL`, `KEYCARD_CLIENT_ID`, `KEYCARD_CLIENT_SECRET` are empty. See `docs/decisions/0002-keycard-integration.md`.

## Findings

1. **venv launcher vs real worker PID.** `.venv\Scripts\python.exe` is a launcher; `Start-Process` returned PID 3272 while the worker printed `pid=2612`. Force-stopping the launcher also terminated the child in this run, but kill demos must target the PID the worker prints on startup.
2. **Temporal history records only the final attempt's `ActivityTaskStarted`.** The killed attempt 1 has no event of its own; it appears only as `lastFailure` on the attempt-2 started event. Per-attempt evidence (attempt number, start, outcome) must be logged by our own code.
3. **atlas-local vector index survives container restart** with a named volume: same index id, same data, same scores.
4. **Voyage keys created in MongoDB Atlas only work against `ai.mongodb.com`.** The same key against `https://api.voyageai.com/v1/embeddings` returned 403: "This API key cannot access this endpoint."
5. **Voyage free rate limit is 3 RPM / 10K TPM** without a payment method, and 429 responses carry no `Retry-After`. Ingest and demos must pace calls or back off on their own schedule.
6. **Structured outputs constrain decoding**, so the real model call produced valid JSON. The Pydantic validation remains the enforcement point.

## NOT VERIFIED

- A malformed response from the real model. The malformed cases in M0.4 are synthetic strings passed to the same validator.
- Voyage 429 handling under sustained load or with a payment method attached; only the reduced-limit behaviour was observed.
- Voyage non-default output dimensions and the `voyageai` Python SDK against `ai.mongodb.com`.
- `rerank-2.5` (non-lite) and embedding models other than those listed.
- Temporal behaviour when the dev server itself is killed, and activity heartbeating.
- atlas-local behaviour when the container is removed and recreated on the same volume (only `docker restart` was tested).
- Anthropic refusal handling and server-side fallbacks; the spike checks `stop_reason` only by printing it.
- Anything Keycard-related.
