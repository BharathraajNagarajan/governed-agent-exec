# Evidence: recorded `gax demo run all`

Source run: `gax demo run all` on 2026-09-29 (16:55 to 16:58 PDT, 23:55 to 23:58 UTC), all 12 demos PASS, 169.8 s total as measured by the runner. The summary table is in [../images/demo-run-all.png](../images/demo-run-all.png).

`policy_deny.json` is from a standalone `gax demo run policy_deny` the same day (17:11 PDT, 2026-09-30 00:11 UTC), PASS 7/7 in 0.9 s. It replaced the run-all file for that demo; `summary.json` still records the run-all value (PASS, 0.4 s). Screenshot: [../images/policy-deny.png](../images/policy-deny.png).

These files are sanitized copies of `.gax/demo-results/`. `.gax/` is not committed; `gax demo run <name>` regenerates the originals there.

- `summary.json`: status and duration per demo.
- `<name>.json`: every check for one demo with its observed value, notes, incident ids and evidence.

Sanitization replaced only these strings:

| Original | Replaced with | Occurrences |
|---|---|---|
| Absolute repository path (`C:\Users\<user>\governed-agent-exec`) | `<repo>` | 0 |
| Home directory outside the repository | `<home>` | 0 |
| Machine name in worker identities (`<pid>@<machine>`) | `<pid>@<host>` | 2 (`approval_worker_restart.json` ×2) |

Check names, statuses, observed values, incident ids, run ids, PIDs in check values and timings are unchanged. No console log is included: the `run-all.log` in `.gax/demo-results/` belongs to an earlier run (257.9 s), not this one. `approval_worker_restart.log` from the same directory is not included either.
