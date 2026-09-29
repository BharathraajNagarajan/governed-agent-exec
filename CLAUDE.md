# Project Rules

- Thesis: governed, durable execution of AI-proposed actions. Retrieval is supporting context only.
- Current phase: M0. Spike code lives in spikes/ only. Do not create src/.
- Frozen until M1 and M2 pass: Kafka, Kubernetes, MCP, multi-agent frameworks, frontend, cloud deployment.
- Failure demonstrations are core functionality.
- Never claim something works unless it was executed. Report failures accurately.
- Minimal code, no comments. Do not restructure working code unless necessary.
- Never read, print, or commit secrets. Keys live in .env only.
- Environment is Windows + PowerShell; give PowerShell commands.
- You cannot activate the venv. Always call .venv\Scripts\python.exe directly.
- Long-running processes (Temporal server, workers) and kill demos are run by me in my own terminal.
- Git: show each command before running it. Never force-push.
- Commits: the repository owner is the only author. Never add Co-Authored-By or any Claude/AI attribution to commit messages.
- Small increments; explain what was built and what I should observe.
