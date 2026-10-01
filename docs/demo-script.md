# Live demo script (5–10 minutes)

Every "expect" line is the result recorded in [m1-findings.md](m1-findings.md) or [failure-semantics.md](failure-semantics.md). If the live run shows something else, say so on camera. Do not paper over it.

Two windows: a PowerShell terminal in the repo root, and a browser on the Temporal UI (http://localhost:8233). Use a fresh incident id for every recording, because `INC-DEMO-01` cannot be reused after it completes.

The script runs in either credential mode. LOCAL-ONLY needs nothing outside the machine except the Claude and Voyage APIs. KEYCARD mints every credential from the Keycard zone and needs a third window, the Keycard console, for `credential_denied`. The KEYCARD expectations below come from the K3 run in [failure-semantics.md "Observed run: KEYCARD mode"](failure-semantics.md#observed-run-keycard-mode) and [keycard-findings.md §9](keycard-findings.md#9-k3-results-2026-10-01).

## Running in KEYCARD mode

1. Keycard setup must already exist: the `gax-worker` application, the five `urn:gax:fleet-api:*` resources with a 1m lifetime as its dependencies, and the `gax-zone-policies` set with the restart forbid ([keycard-findings §7](keycard-findings.md#7-k1-console-setup-in-progress-2026-09-30), [§8.4](keycard-findings.md#84-console-policy-behaviour-verified-console)).
2. In `.env`, set `CREDENTIAL_MODE=keycard` and fill in `KEYCARD_ZONE_URL`, `KEYCARD_CLIENT_ID` and `KEYCARD_CLIENT_SECRET`. Do not show `.env` on screen.
3. In the Keycard console, make sure `default-zone-policies` is the active policy set. `gax broker revoke|restore` refuse in this mode, so the console is the only place to restore access.
4. Everything else in "Before recording" is the same, except that step 4 skips `broker restore`.

To switch back, set `CREDENTIAL_MODE=local-only` (or empty `KEYCARD_ZONE_URL` and `CREDENTIAL_MODE`) and restart the stack.

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
   Expect `atlas-local ready`, `temporal (7233) ready`, `fleet-api (8081) ready`, `stack up. ... (LOCAL-ONLY auth)` or `(KEYCARD auth)` (the `auth_mode` fleet-api reports), and `worker launcher pid=... real pid=...`. The worker log line shows `credential_mode=LOCAL-ONLY` or `credential_mode=KEYCARD`.
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
   Expect `{"status": "reset"}` and counters `{}`. In KEYCARD mode, skip `broker restore` (it refuses and points to the console) and check the active policy set in the console instead.
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

Expect `started INC-DEMO-01 run_id=... proposal_source=llm`, then `final status VERIFIED`. In KEYCARD mode this run makes 3 Keycard mints (snapshot, execute, verify), as `INC-K3-001` did in K3. The model's choice is not deterministic. In M1 run INC-M1-001 it proposed `restart_consumer`, which policy-v1 ALLOWs in staging. If it proposes something that needs approval, the run waits. Approve it with `.venv\Scripts\gax.exe approve INC-DEMO-01 --by <name> --wait` and explain the approval step on camera.

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
- `credential_mode: LOCAL-ONLY`, or `KEYCARD` in KEYCARD mode (`INC-K3-001` in K3).
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

**In KEYCARD mode** the revoke is a real Keycard policy, flipped by hand in the console. Keep the console open on Policy Sets. The demo took 91.7 s in K3.

1. The demo prints `>>> OPERATOR: ACTIVATE gax-zone-policies in the Keycard console now`, then mints a real `restart` token every 10 s and prints `probe n: ISSUED`. Activate `gax-zone-policies` in the console. Wait for `probe n: DENIED`. In K3 that was probe 4, 33.7 s after the prompt, including the time to click.

   > "That set adds one Cedar forbid: gax-worker may not get a token for the restart resource. Nothing on this machine changed."

2. The incident runs. Expect ledger `[CREDENTIAL_DENIED]` with the error `KEYCARD access_denied for urn:gax:fleet-api:restart: Access to "fleet-api restart" is denied by Policy "gax-forbid-restart" in version 3 of Policy Set "gax-zone-policies".` (the version number is whatever the console shows), 1 non-retryable attempt and `{get_state: 1}`.

   > "The denial reason comes from Keycard and names the policy. It is stored in the ledger row as evidence."

3. The demo prints `>>> OPERATOR: RE-ACTIVATE default-zone-policies now` and probes again. Re-activate `default-zone-policies`. Wait for `probe n: ISSUED`. In K3 that was probe 6, 53.4 s after the prompt. The rerun of the same incident id then reaches VERIFIED with ledger `[APPLIED]`.

Say on camera that these times include your own click time, so they are not Keycard's time-to-effect. If you do not flip within 30 probes (about 5 minutes), the demo stops as NOT REPRODUCED. If the run ends before step 3, re-activate `default-zone-policies` by hand.

If you also show `credential_transient` in KEYCARD mode, say that its two failures are SIMULATED in front of the real mint. Keycard has no fault injection, and the lastFailure says `SIMULATED transient failure in front of KEYCARD`.

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

- Keycard tokens carry only the action (`aud`), not the environment or target. Policy decides those before execution.
- Revocation is a manual policy-set flip in the Keycard console, and its time-to-effect is not measured.
- The Keycard transient failure is simulated, and every mint is a billed Keycard transaction.
- LOCAL-ONLY mode signs credentials with a local key and is not Keycard verification.
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
