# Durable & Governed Agent Execution System

Status: M1. fleet-api and the credential broker run in LOCAL-ONLY mode until Keycard is available (docs/decisions/0002-keycard-integration.md). Observed results: docs/m0-findings.md, docs/m1-findings.md.

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\scripts\start-stack.ps1
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m gax.worker
```

In another terminal:

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
