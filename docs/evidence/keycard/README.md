# Evidence: `gax demo run all` in KEYCARD mode (K3)

Source run: `gax demo run all` on 2026-10-01 (04:16 to 04:21 PDT, 11:16 to 11:21 UTC) with `CREDENTIAL_MODE=keycard`: the worker broker minted real Keycard tokens and fleet-api verified them against the zone JWKS (`/healthz` `auth_mode` `KEYCARD`). All 12 demos PASS, 310.9 s total as measured by the runner. The LOCAL-ONLY run is in [../](../README.md) and is unchanged.

These files are sanitized copies of `.gax/demo-results/`. `.gax/` is not committed; `gax demo run <name>` regenerates the originals there.

- `summary.json`: status and duration per demo.
- `<name>.json`: every check for one demo with its observed value, notes, incident ids and evidence.

Every demo in this mode has two extra checks: ledger rows carry `credential_mode` `KEYCARD` (where the demo wrote ledger rows), and fleet-api `/healthz` reports `auth_mode` `KEYCARD`. The secret scan matches any JWT (`eyJ<header>.eyJ<payload>.<signature>`), the `eyJhbGciOi` prefix, and `Bearer <token>`. It found 0 hits in every demo.

`credential_denied` was revoked and restored by the operator, who switched the active Keycard policy set in the console while the demo probed. `credential_transient`'s two failures are **SIMULATED** in front of the real mint. See [../../failure-semantics.md](../../failure-semantics.md#observed-run-keycard-mode).

Sanitization replaced only these strings:

| Original | Replaced with | Occurrences |
|---|---|---|
| Absolute repository path | `<repo>` | 0 |
| Home directory outside the repository | `<home>` | 0 |
| Machine name in worker identities (`<pid>@<machine>`) | `<pid>@<host>` | 2 (`approval_worker_restart.json` ×2) |
| Keycard zone host | `<zone>` | 0 |
| `KEYCARD_CLIENT_ID` value | `<client-id>` | 0 |

Before writing, each file was checked to contain neither the client secret nor any JWT or bearer token. Check names, statuses, observed values (including the Keycard denial text, which names the policy and the policy set), incident ids, run ids, PIDs and timings are unchanged. No console or worker log is included.
