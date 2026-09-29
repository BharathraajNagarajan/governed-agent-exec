# Project Rules

- Thesis: governed, durable execution of AI-proposed actions. Retrieval is supporting context only.
- Current phase: M1. Code lives in src/, tests/, fleet_api/, scripts/, corpus/. spikes/ is frozen reference; do not modify it.
- Frozen until M1 and M2 pass: Kafka, Kubernetes, MCP, multi-agent frameworks, frontend, cloud deployment.
- Failure demonstrations are core functionality.
- Never claim something works unless it was executed. Report failures accurately.
- Minimal code, no comments. Do not restructure working code unless necessary.
- Never read, print, or commit secrets. Keys live in .env only.
- Environment is Windows + PowerShell; give PowerShell commands.
- You cannot activate the venv. Always call .venv\Scripts\python.exe directly.
- Claude Code may run the Temporal server, workers and kill demos as background processes and must stop them afterward. Kill demos target the real worker PID printed on startup, not the venv launcher PID.
- Git: show each command before running it. Never force-push.
- Commits: the repository owner is the only author. Never add Co-Authored-By or any Claude/AI attribution to commit messages.
- Small increments; explain what was built and what I should observe.
