# Live demo script (5–10 minutes)

Every "expect" line is the result recorded in [m1-findings.md](m1-findings.md) or [failure-semantics.md](failure-semantics.md). If the live run shows something else, say so on camera. Do not paper over it.

Two windows: a PowerShell terminal in the repo root, and a browser on the Temporal UI (http://localhost:8233). Use a fresh incident id for every recording, because `INC-DEMO-01` cannot be reused after it completes.

## Before recording

1. Disable sleep. Finding 2 in failure-semantics records a run that stalled for 31 minutes while the host slept.
   ```powershell
   powercfg /change standby-timeout-ac 0
   ```
   Note the previous value first with `powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE`. It reports "Current AC Power Setting Index" in seconds, as hex, while `/change` takes minutes. `0x00000000` means sleep is already off. Restore it afterwards.
2. Start the stack and worker:
   ```powershell
   .\scripts\start-stack.ps1 -WithWorker
   ```
   Expect `atlas-local ready`, `temporal (7233) ready`, `fleet-api (8081) ready`, and `worker launcher pid=... real pid=...`.
3. Make sure ingest is done:
   ```powershell
   .venv\Scripts\gax.exe ingest
   ```
   Expect `"embedded": 0, "skipped": 54, "index": "exists"`.
4. Reset fleet state, counters, faults and the grant used below:
   ```powershell
   .venv\Scripts\gax.exe fleet reset
   .venv\Scripts\gax.exe broker restore --action restart_consumer
   .venv\Scripts\gax.exe fleet counters
   ```
   Expect `{"status": "reset"}` and counters `{}`.
5. Wait at least 60 s after the ingest check before step 2 of the script. The Voyage free tier allows 3 requests per minute, and the live incident makes one embed and one rerank call.
6. Increase the terminal font size and close anything that could show `.env`.

## Script

### 1. Architecture (≈1 min)

Show the README diagram.

> "Claude proposes one structured action. Everything after that is deterministic: a versioned policy decides allow, approval or deny; Temporal runs the steps durably; every execution attempt fetches its own short-lived credential and sends an Idempotency-Key; the result is verified against fleet-api's state and written to an audit record. Retrieval only gives the model context, it never authorizes anything."

### 2. Normal incident, real Claude + Voyage (≈1.5 min)

```powershell
.venv\Scripts\gax.exe incident start --id INC-DEMO-01 --env staging --target orders-consumer --summary "orders-consumer has 0 active members, lag climbing" --wait
```

> "This one is fully live: Voyage embeds the summary, MongoDB vector search plus rerank picks runbook chunks, Claude proposes an action."

Expect `started INC-DEMO-01 run_id=... proposal_source=llm`, then `final status VERIFIED`. The model's choice is not deterministic. In M1 run INC-M1-001 it proposed `restart_consumer`, which policy-v1 ALLOWs in staging. If it proposes something that needs approval, the run waits. Approve it with `.venv\Scripts\gax.exe approve INC-DEMO-01 --by <name> --wait` and explain the approval step on camera.

In the Temporal UI, open `INC-DEMO-01` and show the activity sequence `retrieve_context → propose_action → evaluate_policy → snapshot_state → execute_action → verify_outcome → record_audit`.

### 3. Audit record (≈1 min)

```powershell
.venv\Scripts\gax.exe audit INC-DEMO-01
```

Point at these fields (as recorded for INC-M1-001):

- `context`: the cited chunk ids with vector and rerank scores.
- `proposal`: action, target, environment.
- `decision`: `ALLOW`, with `policy_version: policy-v1`.
- `snapshot`: fleet state before the action.
- `execution`: `replayed: false`.
- `verification`: `expected 1, observed 1`.
- `credential_mode: LOCAL-ONLY`.
- `action_ledger`: one row, attempt 1, `APPLIED`, Idempotency-Key `INC-DEMO-01:execute_action`.

> "There is no token anywhere in here, and the tests and every demo scan the Temporal history and the worker log for credential material."

### 4. policy_deny (≈30 s)

```powershell
.venv\Scripts\gax.exe demo run policy_deny
```

> "Now the proposal is prod `reset_consumer_offset`. I inject it so it's deterministic, but it is exactly what a model could propose."

Expect 7 PASS lines. Decision `DENY` (`reset_consumer_offset in prod is DENY`, `policy-v1`). Scheduled activities are only `propose_action, evaluate_policy, record_audit`. Fleet counters `{}`. Prod offset stays at 1000. `== policy_deny: PASS`.

> "fleet-api received zero requests. The credential layer never even got asked."

### 5. credential_denied (≈30 s)

```powershell
.venv\Scripts\gax.exe demo run credential_denied
```

> "Here policy says ALLOW, but the grant for restart_consumer is revoked. That's the second authorization layer."

Expect ledger `[CREDENTIAL_DENIED]` and exactly 1 `execute_action` attempt with the failure marked non-retryable. The fleet restart counter is 0 (`{get_state: 1}`). After restore, a rerun of the same incident id reaches VERIFIED, and the state changes exactly once overall.

> "Denial is non-retryable on purpose: a revoked grant is a decision, not an outage. Because the run failed, the same incident id can be started again, and the credential is re-checked on every attempt."

### 6. worker_kill (≈1.5 min, the demo takes about 34 s)

```powershell
.venv\Scripts\gax.exe demo run worker_kill
```

> "fleet-api gets an 8-second latency fault. The workflow starts, and when the ledger shows attempt 1 in flight, the demo hard-kills the real worker process. That's the Python PID the worker prints, not the venv launcher. Then it starts a new worker."

Expect lines showing the killed real PID and launcher PID, `execute_action` attempt 2 with lastFailure `activity StartToClose timeout`, ledger `[PENDING, REPLAYED]`, `restart_count` 0 → 1, and VERIFIED.

In the Temporal UI, open the `DEMO-WORKER-KILL-...` workflow and expand the `execute_action` ActivityTaskStarted event: `attempt: 2`, `lastFailure: activity StartToClose timeout`.

> "Temporal history keeps only the final attempt. The killed attempt exists only as lastFailure. That's why every attempt writes its own ledger row: attempt 1 is stuck at PENDING forever. REPLAYED means attempt 1's request had already committed on fleet-api after the worker died. Attempt 2 sent the same Idempotency-Key and got the stored response back. The consumer restarted once, not twice."

### 7. response_lost (≈30 s)

```powershell
.venv\Scripts\gax.exe demo run response_lost
```

> "Worse than a crash: fleet-api commits the restart and then drops the connection. The worker can't tell whether it happened."

Expect the fleet log line `FAULT commit-then-drop`, ledger `[TRANSPORT_ERROR, REPLAYED]`, lastFailure `fleet-api transport error: RemoteProtocolError`, 2 restart requests, `restart_count` 0 → 1, and VERIFIED.

> "Mention the one finding only the live demo caught: originally this lastFailure read `Failure exceeds size limit.` because the SDK serialized the whole httpx exception chain. The mock test didn't reproduce it."

### 8. duplicate_start (≈45 s, the demo takes about 21 s)

```powershell
.venv\Scripts\gax.exe demo run duplicate_start
```

Expect the first start to exit 0. A second start while the first is running exits 2 with `rejected: incident ... is running or already completed`. A third after completion does the same. There is still one run id, ledger `[APPLIED]`, `restart_consumer=1`.

> "Workflow id equals incident id, with ALLOW_DUPLICATE_FAILED_ONLY. Only failed incidents can be retried by id."

### 9. Limitations (≈1 min)

Show the README "Limitations" section and say plainly:

- The credential broker is LOCAL-ONLY. Keycard is pending (ADR 0002), and nothing here is Keycard-verified.
- It runs on single-node atlas-local and the Temporal dev server.
- The fleet-api admin endpoints are unauthenticated (localhost only).
- The runbooks are synthetic.
- If retrieval fails, the run proceeds without context, but policy still gates the action.
- The idempotency key is per incident id, not per run.
- Not verified: malformed output from the real model, Mongo down after the fleet commit, a Temporal server crash, and a worker kill before the request reaches fleet-api.

## After recording

```powershell
.\scripts\stop-stack.ps1
powercfg /change standby-timeout-ac <previous minutes>
```

Expect `worker stopped`, `fleet-api stopped`, `temporal stopped` and `gae-atlas-local stopped`. The container exits with code 137 (failure-semantics finding 4). This is known and harmless for the demo.
