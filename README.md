# Durable & Governed Agent Execution System

Status: M2. fleet-api and the credential broker run in LOCAL-ONLY mode until Keycard is available (docs/decisions/0002-keycard-integration.md). Observed results: docs/m0-findings.md, docs/m1-findings.md, docs/failure-semantics.md (failure demos).

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\scripts\start-stack.ps1 -WithWorker
.venv\Scripts\python.exe -m pytest
.venv\Scripts\gax.exe demo list
.venv\Scripts\gax.exe demo run all
```

`scripts\start-worker.ps1` / `scripts\stop-worker.ps1` run the worker detached (log `.gax\logs\worker.log`, launcher and real PID in `.gax\pids\worker.pid`).

Manual runs:

```powershell
.venv\Scripts\gax.exe ingest
.venv\Scripts\gax.exe search "consumer group has zero active members"
.venv\Scripts\gax.exe incident start --id INC-1 --env staging --target orders-consumer --summary "orders-consumer has 0 active members, lag climbing" --wait
.venv\Scripts\gax.exe incident start --id INC-2 --env prod --target orders-consumer --summary "poison message" --proposal-file scripts\proposals\prod-reset-offset.json --wait
.venv\Scripts\gax.exe status INC-1
.venv\Scripts\gax.exe audit INC-1
.venv\Scripts\gax.exe approve INC-3 --by alice
.venv\Scripts\gax.exe broker revoke --action restart_consumer
.venv\Scripts\gax.exe fleet faults --action restart_consumer --fail-next 2
.venv\Scripts\gax.exe fleet counters
.\scripts\stop-stack.ps1
```

`stop-stack.ps1` also stops the worker.
