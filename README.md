# Durable & Governed Agent Execution System

Status: M1 (foundation). fleet-api and the credential broker run in LOCAL-ONLY mode until Keycard is available (docs/decisions/0002-keycard-integration.md).

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\scripts\start-stack.ps1
.venv\Scripts\python.exe -m pytest
.\scripts\stop-stack.ps1
```
