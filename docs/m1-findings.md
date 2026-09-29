# M1 Findings

Observed results only. Run date: 2026-09-29.

## Part 0

- `.env` had no `LOCAL_BROKER_SIGNING_KEY`; one was appended from `secrets.token_urlsafe(48)` (64 bytes). Value never printed.
- `start-stack.ps1` hung when its output was piped (`powershell -File start-stack.ps1 2>&1 | cat`): the script finished but the pipe never closed until temporal and fleet-api were killed. Cause: `Start-Process -RedirectStandardOutput` uses `CreateProcess` with handle inheritance, so the detached children inherited every inheritable handle of the caller, including the pipe. Clearing the inherit flag on the three std handles was not enough (Git Bash passes further inheritable copies of the pipe). Fix: launch through `Start-Process cmd.exe /c "... 1>out 2>err"` without redirection parameters, which uses ShellExecute and inherits no handles; `cmd` owns the log file handles. Piped run now returns in 5 s.
- Anthropic `messages.parse(output_format=ActionProposal)` with the default `ANTHROPIC_MODEL=claude-sonnet-5`: accepted, served model `claude-sonnet-5`, `stop_reason=end_turn`, 2812 ms, 880 input / 109 output tokens, valid `restart_consumer` proposal. The default was not changed.
