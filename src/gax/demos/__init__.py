from gax.demos import (approval_timeout, credential_denied, credential_transient, duplicate_start, fleet_5xx, llm_malformed, mongo_down,
                       policy_deny, response_lost, voyage_429, worker_kill)

DEMOS = {m.NAME: m for m in (worker_kill, fleet_5xx, response_lost, credential_denied, credential_transient, policy_deny, approval_timeout,
                             llm_malformed, voyage_429, mongo_down, duplicate_start)}
