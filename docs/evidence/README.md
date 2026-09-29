# Evidence: recorded `gax demo run all`

Source run: `gax demo run all` on 2026-09-29 (23:11 to 23:15 UTC), all 12 demos PASS, 257.9 s total.

These files are sanitized copies of `.gax/demo-results/` from that run. `.gax/` is not committed; `gax demo run <name>` regenerates the originals there.

- `summary.json`: status and duration per demo.
- `<name>.json`: every check for one demo with its observed value, notes, incident ids and evidence.
- `run-all.log`: console output of the run.

Sanitization replaced only these strings:

| Original | Replaced with | Occurrences |
|---|---|---|
| Absolute repository path (`C:\Users\<user>\governed-agent-exec`) | `<repo>` | 15 (all in `run-all.log`) |
| Home directory outside the repository | `<home>` | 0 |
| Machine name in worker identities (`<pid>@<machine>`) | `<pid>@<host>` | 3 (`approval_worker_restart.json` ×2, `run-all.log` ×1) |

Check names, statuses, observed values, incident ids, run ids, PIDs in check values and timings are unchanged. `approval_worker_restart.log` from the same directory is not included.
