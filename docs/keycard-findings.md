# Keycard findings (Phase K0: discovery)

Date: 2026-09-30. Status: discovery only. No Keycard zone has been called. `src/`, `tests/`, `fleet_api/` and `scripts/` were not changed. The LOCAL-ONLY broker is still the only credential path, and ADR 0002 stays at PENDING.

Scope: what the official docs and the installed SDK say about replacing `LocalOnlyCredentialBroker` with Keycard, mapped against our credential boundary. Every statement below cites a docs URL, a `file:line` in the installed SDK, or a command that was run. Anything else is listed under UNKNOWN.

## Installed for inspection (venv only, not in pyproject.toml)

`.venv\Scripts\python.exe -m pip install keycardai-temporal` installed:

| Package | Version | Why |
|---|---|---|
| keycardai-temporal | 0.4.1 | `@grant`, `access()`, `KeycardInterceptor` |
| keycardai-oauth | 0.31.3 | OAuth client, credentials, verifier (dependency) |
| joserfc | 1.7.5 | JOSE (dependency of keycardai-oauth) |
| cryptography | 50.0.2 | dependency of keycardai-oauth |
| cffi | 2.1.1 / pycparser 3.0 | dependencies of cryptography |

Declared requirements: keycardai-temporal requires `temporalio>=1.32.0` and `keycardai-oauth>=0.30.0`. keycardai-oauth requires `cryptography>=45.0.7`, `httpx>=0.28.1`, `joserfc>=1.6.8` and `pydantic>=2.11.7` (dist-info `METADATA`). The repo pins `temporalio==1.33.0`, which satisfies this, and pip did not change it. PyJWT stays at 2.15.1.

## Current boundary (for reference)

- Interface: `issue(action, target, environment, workflow_id, attempt) -> Credential` (`src/gax/credentials/base.py:28`). `CredentialDenied.retryable = False` and `CredentialUnavailable.retryable = True` (`base.py:10-15`). `Credential.token` has `repr=False` (`base.py:20`).
- The LOCAL-ONLY broker signs HS256 JWTs with `iss=gax-local-only-broker`, `aud=fleet-api`, `sub=workflow:<id>` and custom claims `action`, `target`, `environment`, `workflow_id` and `attempt`. TTL is 60 s (`src/gax/credentials/local_only_broker.py:79`, `issue` at `:108-130`). Revoke, restore and transient grants are local state keyed by action (`local_only_broker.py:87-106`).
- Activities call the broker inside the activity body, once per attempt. `_credential` maps errors to `ApplicationError(type="CredentialDenied", non_retryable=True)` or `type="CredentialUnavailable"` (`src/gax/remediation/activities.py:93-99`). `execute_action` inserts a PENDING ledger row before asking for a credential and finishes it as `CREDENTIAL_DENIED` or `CREDENTIAL_UNAVAILABLE` on failure (`activities.py:126-136`). `snapshot_state` and `verify_outcome` get a `get_state` credential (`activities.py:101-102`).
- fleet-api verifies the token with the shared key, then checks `environment` and, on mutations, `action` and `target` claims against the URL (`fleet_api/app.py:102-113`). It echoes `credential_jti` into the response body (`app.py:168`).
- The worker runs activities in a `ThreadPoolExecutor(16)` (`src/gax/worker.py:36-38`). All remediation activities are sync `def`.

---

## 1. VERIFIED FROM DOCS

Fetched 2026-09-30. Some pages were returned through a summarizing fetcher, so wording is close but not always exact. The URLs are the sources.

**Concepts**
- A zone groups "users, applications, and resources that share a common set of security controls and policies". It acts as an STS. The default domain is `<zone-id>.keycard.cloud`. https://docs.keycard.ai/concepts/zones.md
- Applications authenticate with a client ID and secret, workload identity (AWS, GCP, Vercel), public credentials (PKCE, user delegation only) or URL credentials (signed JWT assertions). Dependencies "declare service-to-service relationships" and permit autonomous access only. https://docs.keycard.ai/concepts/applications.md
- A resource identifier becomes the `aud` claim. There are three credential approaches: Keycard-issued JWTs (the default, verifiable with the zone's public JWKS), vaulted static credentials, and brokered external access. https://docs.keycard.ai/concepts/resources.md
- Policies are Cedar and default-deny: "every authorization request needs an explicit permit for the user, the application, and the resource". Forbid beats permit. On denial "Keycard does not issue the credential". https://docs.keycard.ai/concepts/policies.md
- The Cedar context has `on_behalf`, `impersonate`, `subject`, `resource`, `scopes`, `actor_claims` and `subject_claims`. A Resource has `identifier`, `name` and `scopes`. An Application has `dependencies` and `traits`. No free-form request attributes such as target or environment are listed. https://docs.keycard.ai/reference/policy-schema.md
- Autonomous access uses the client credentials flow. Impersonation requires the user to have previously authorized the application. https://docs.keycard.ai/concepts/credentials.md
- Providers cover identity federation, workload identity and access federation. https://docs.keycard.ai/concepts/providers.md

**Temporal integration** (https://docs.keycard.ai/sdk/temporal.md)
- The package "mints a fresh Keycard token for every activity execution" through a worker interceptor. It exports `@grant`, `access()`, `KeycardInterceptor`, `Subject`, `AccessContext`, `GrantConfigurationError` and `ResourceAccessError`.
- It has three identity modes: app-as-itself, on-behalf-of (RFC 8693) and impersonation.
- Credentials are discovered from `KEYCARD_CLIENT_ID` + `KEYCARD_CLIENT_SECRET`, workload identity token files, or `KEYCARD_APPLICATION_CREDENTIAL_TYPE`.
- Per the docs, transient failures (network, 5xx, 429) raise `KeycardMintFailed`. Permanent failures (4xx, denials) raise non-retryable `KeycardAccessDenied`. **The SDK source differs for the app-as-itself path; see §2.3.**
- "Tokens never touch workflow history."
- It requires Python 3.10+ and `temporalio` 1.32.0+.

**Validating tokens in our own API**
- Verify `iss` (zone URL), `aud` (the Resource identifier), the signature from the zone JWKS, and `exp`. https://docs.keycard.ai/guides/protect-any-api.md
- The issuer format is `https://<zone-id>.keycard.cloud`. The discovery document is at `<issuer>/.well-known/openid-configuration` and the keys at `<issuer>/openidconnect/jwks`. Claims are `iss`, `sub`, `sub_profile`, `keycard_app_id`, `client_id`, `aud`, `scope`, `sid`, `exp`, `iat` and `jti` ("used for audit correlation"). Only `sub`, `keycard_app_id` and `aud` are customizable. https://docs.keycard.ai/reference/token-claims.md
- The protect-an-API guide names `/.well-known/oauth-authorization-server` (RFC 8414) as the discovery source. That does not match the token-claims page (see UNKNOWN). https://docs.keycard.ai/guides/protect-any-api.md
- Signing is RS256 over TLS 1.3, and RFC 9068 JWT access tokens are supported. https://docs.keycard.ai/reference/security-architecture.md, https://docs.keycard.ai/reference/standards.md

**Lifetime and revocation**
- Access tokens last 1 hour, refresh tokens 30 days (rotated on use) and ID tokens 1 hour. "Revocation stops future issuance while already-issued credentials remain valid until expiration." https://docs.keycard.ai/reference/security-architecture.md
- A grant records user consent for application → resource. It can be revoked in the console, by API (`PATCH ... "status": "revoked"`) or with `keycard agent api /zones/<zone-id>/delegated-grants/<grant-id> -X PATCH`. "There is no per-token kill-switch today." Restoring access requires the user to re-authorize. https://docs.keycard.ai/admin/revoke-a-grant.md
- That revoke/restore flow is described for **user-delegated grants**. The docs do not describe revoking an application's autonomous dependency (see UNKNOWN).

**Errors and audit**
- A hard denial issues no token and returns a body with `error: "access_denied"`, `error_description` and a `requestId`. A soft denial on a dependency resource returns HTTP 200, and the resource is missing from the token's `target` claim. https://docs.keycard.ai/admin/access-policies/troubleshooting.md
- Audit events include `credentials:issue`, `users:authorize` and `delegated_grants:create`. Failure codes are `access_denied` (policy) and `insufficient_authorization` (no grant or revoked grant). Logs export to S3 as OCSF Parquet with a support-assisted setup. https://docs.keycard.ai/admin/audit-log-and-sessions.md

**Plan and transactions**
- A transaction is recorded when Keycard issues credentials, validates requests or handles step-up approvals. A credential exchange costs 1. A tool call on a protected resource costs 1. https://docs.keycard.ai/admin/usage.md
- Starter (free) has "5,000 transactions/mo (hard cap)". Team ($500/mo) includes 100,000, with overage at $1 per 1,000. Enterprise is custom. https://www.keycard.ai/pricing (the pricing page does not mention private beta).

**Tooling**
- The CLI has `keycard credential ...` and `keycard agent api <endpoint> [-X METHOD] [-d JSON]`, a passthrough to the Management API. The CLI page lists no dedicated commands for creating applications, resources or policies. https://docs.keycard.ai/cli.md

## 2. VERIFIED FROM SDK SOURCE

Paths are relative to `.venv/Lib/site-packages/`. Items marked **[executed]** were run on 2026-09-30 by a scratch probe outside the repo (§2.8). The others come from reading the source.

### 2.1 `@grant` and obtaining the credential
- Signature: `grant(*resources: str, subject_from: str | Callable | None = None, impersonate: bool = False)` (`keycardai/temporal/__init__.py:265-269`). It needs at least one resource and rejects duplicates at decoration time (`:291-297`). It must be applied "outermost, above `@activity.defn`" (`:283`). It stores a `_Grant` on the function (`:317`).
- **Resources are fixed at decoration time.** The tuple is captured once (`:130-131`, `:317`). An activity cannot choose a resource per call from its input.
- The body obtains the token with `access()` or `access(resource)`, which returns a `TokenResponse` (`:323-352`). The docstring warns: never put the response or `.access_token` in return values, arguments or signals (`:330-331`).
- App-as-itself calls `client_credentials_grant(resource=resource)` once per declared resource. **No `scope` is passed** (`:448-455`), although the client accepts one (`keycardai/oauth/client.py:890-901`, `types/models.py` `ClientCredentialsRequest.scope`).
- App-as-itself and impersonation both **require a `ClientSecret` credential**. With workload or web identity, the interceptor raises `GrantConfigurationError` (`:440-445`, `:462-467`).
- Minting is all-or-nothing and happens **before the activity body runs** (module docstring `:6-8`; `execute_activity` `:504-512`).

### 2.2 `KeycardInterceptor` setup
- `KeycardInterceptor(zone_url, credential=None, subject_token_provider=None)` (`:397-402`). `zone_url` is the issuer, `https://<zone-id>.keycard.cloud`, and endpoints are discovered from it (`:373-374`).
- If `credential` is omitted, `discover_credential()` reads `KEYCARD_CLIENT_ID` and `KEYCARD_CLIENT_SECRET` (`keycardai/oauth/server/credentials.py:791-792`), workload identity files such as `KEYCARD_EKS_WORKLOAD_IDENTITY_TOKEN_FILE` (`:328-335`), `KEYCARD_WEB_IDENTITY_KEY_STORAGE_DIR` (`:744`) and `KEYCARD_APPLICATION_CREDENTIAL_TYPE` (`:807`). Discovery failure is re-raised as `GrantConfigurationError` (`keycardai/temporal/__init__.py:355-366`).
- An explicit credential is `ClientSecret((client_id, client_secret))` (`credentials.py:92-120`).
- One `AsyncClient` is created per worker and endpoint discovery is cached. Tokens are not cached (`:405-408`). **[executed]** Constructing the interceptor with a non-resolving zone URL made no network call.
- **The zone URL is not read from an env var by the SDK.** The caller passes it. Our `Settings.keycard_zone_url` (`src/gax/config.py:20`) would supply it.
- Registration: `Worker(..., interceptors=[KeycardInterceptor(...)])`. **[executed]**

### 2.3 Exception types
- `GrantConfigurationError(RuntimeError)` is retryable by default. The docstring recommends adding it to `non_retryable_error_types` to fail fast (`:91-97`).
- Permanent failure: `ApplicationError(type="KeycardAccessDenied", non_retryable=True)` (`:521-527`, `:549-555`).
- Exchange-path transient failure: `ApplicationError(type="KeycardMintFailed")`, which is retryable (`:556-559`).
- **App-as-itself transient failure: the original keycardai exception is re-raised as-is** (`raise e`, `:528`). It is not wrapped in `KeycardMintFailed`. **[executed]** The Temporal failure type was `NetworkError`.
- Classification: `PERMANENT_ERROR_CODES = {"access_denied", "insufficient_authorization", "invalid_client"}` (`keycardai/oauth/exceptions.py:20-22`). `OAuthProtocolError.retryable` returns false only for those codes (`:134-150`). An HTTP error is retryable for 429 and 5xx (`:91-93`). `NetworkError` is always retryable (`:209`). An exception with no `retryable` attribute is treated as retryable (`keycardai/oauth/server/token_exchange.py:18-26`).
- `access()` raises `ResourceAccessError` if the resource errored or is missing (`keycardai/oauth/server/access_context.py:92-130`). It raises `RuntimeError` when there is no `@grant` or interceptor (`keycardai/temporal/__init__.py:333-339`).

### 2.4 Sync activities in a ThreadPoolExecutor: works
- The interceptor stores the minted `AccessContext` in a `contextvars.ContextVar` (`keycardai/temporal/__init__.py:260-262`, `:508`), then awaits `next.execute_activity`.
- For a sync activity with a `ThreadPoolExecutor`, temporalio 1.33.0 runs `contextvars.copy_context()`, then `run_in_executor(..., current_context.run, ...)` (`temporalio/worker/_activity.py:878-884`). The ContextVar therefore reaches the worker thread. The comment at `_activity.py:806-809` says contextvars are *not* propagated, but the code at `:878-882` does propagate them for thread pools. A process-pool executor would not.
- **[executed]** A sync `def` method activity with `@grant` / `@activity.defn` / a `functools.wraps` wrapper, the same stacking as `@mongo_unavailable`, ran on thread `ThreadPoolExecutor-0_0` (not main). `access().access_token` there equalled the minted value.

### 2.5 Can tokens reach workflow history or logs?
- By design, nothing is written to headers, arguments or return values (`keycardai/temporal/__init__.py:32-35`). **[executed]** The probe's token sentinel was absent from the full workflow history JSON in all three cases: ok, transient and denied.
- **Repr risk:** `TokenResponse` is a plain `@dataclass` with `access_token: str` (`keycardai/oauth/types/models.py:79-88`). Its default repr includes the token and the `raw` dict. Logging or formatting a `TokenResponse` would leak it. Our `Credential(repr=False)` wrapper avoids this if only `.access_token` is copied in.
- Error messages include the OAuth error code and description, not the token (`keycardai/temporal/__init__.py:521-527`, `:546-559`). **[executed]** The worker logged failure tracebacks with `access_denied` and `NetworkError` text. The token did not appear.
- The only token-related log line in keycardai-oauth is `"Authorization code received; exchanging for token"` (`keycardai/oauth/pkce/client.py:161`), which has no value.

### 2.6 On-behalf-of / subject options
- `subject_from` takes a parameter name, a dotted path (`"inp.approver_id"`) or a callable. Alternatively, `Subject()` can be put on a **dataclass** field (`keycardai/temporal/__init__.py:110-116`, `:165-229`). Our activity inputs are pydantic `BaseModel`s (`src/gax/remediation/types.py:41`), so only `subject_from` would apply.
- On-behalf-of requires a `subject_token_provider` that returns the user's current session token (`:385-388`, `:494-502`).
- `impersonate=True` sends a stable user identifier. The docstring calls it "privileged and policy-gated; forbidden by default in the zone" (`:26-30`).
- None of this is needed for our current model, where the worker acts as itself.

### 2.7 Workflow sandbox import
- `keycardai.temporal` wraps its own `keycardai.oauth` imports in `workflow.unsafe.imports_passed_through()` (`:62-78`). **[executed]** Importing `keycardai.oauth.*` directly at the top of a module that defines a workflow failed sandbox validation (`RestrictedWorkflowAccessError` via `httpx` → `urllib.request.Request`). It worked once wrapped in `imports_passed_through()`. Our `src/gax/workflows.py:8` already uses that pattern.

### 2.8 Probe
- The probe is a scratch file outside the repo, run with `.venv\Scripts\python.exe` against `WorkflowEnvironment.start_local` (temporal CLI 1.9.1, server 1.32.0). It used a real `Worker`, a `ThreadPoolExecutor(4)` and the real `KeycardInterceptor`. **Only the HTTP mint (`client_credentials_grant`) was stubbed**, so this proves the SDK/Temporal plumbing, **not** Keycard server behaviour.

| Case | Stub behaviour | Observed |
|---|---|---|
| ok | returns token | success, attempt 1, 1 mint, runs on pool thread, `token_ok=True`, token not in history |
| transient | `NetworkError` ×2 then token | succeeded on attempt 3, 3 mints, token not in history |
| deny | `OAuthProtocolError("access_denied")` | `ApplicationError type=KeycardAccessDenied non_retryable=True`, 1 mint, token not in history |

## 3. UNKNOWN / NEEDS CONSOLE OR LIVE TEST

1. **Revoking or denying an autonomous (client-credentials) application.** The revoke docs cover user-delegated grants only. It is unknown whether removing a dependency, or adding a Cedar forbid, takes effect for the next `client_credentials_grant` immediately, and which error code it returns (`access_denied` vs `insufficient_authorization`).
2. **The error the zone returns for a client-credentials request to an undeclared or forbidden resource.** It could be a hard `access_denied` or a soft denial (HTTP 200 with the resource missing from `target`). A soft denial would *not* raise in the interceptor.
3. **Discovery endpoint path.** The token-claims page says `/.well-known/openid-configuration` + `/openidconnect/jwks`, and the protect-an-API guide says `/.well-known/oauth-authorization-server`. Confirm which one the zone serves.
4. **Actual token lifetime for client-credentials tokens**, and whether a Resource can shorten it. The docs say 1 h for access tokens and don't say whether it is configurable. Ours is 60 s.
5. **Claim contents of a client-credentials token**: the `sub` value, whether `scope` is present when none is requested, and the `target` claim format.
6. **Whether Cedar can see the requested `scope`** (`context.scopes`) on client credentials, so that per-action scopes on one resource could be policy-gated.
7. **Rate limits on the token endpoint.** None are documented. A `run-all` mints many tokens in short bursts.
8. **What "validating requests" bills.** It is unclear whether local JWKS validation in fleet-api counts as a transaction.
9. **Private-beta plan terms.** It is unknown whether the account is on Starter's 5,000/mo hard cap or a beta allocation, and what happens at the cap (presumably mint failures; it is not documented what code they return).
10. **Whether `http://localhost:<port>` is accepted as a Resource identifier** for a non-public API. The token-claims page shows `http://localhost:9090` as an example `aud`, but it is not tested.
11. **Service accounts for the Management API.** The revoke page says "authenticate using service account credentials". Creating one, and its permissions for scripted revoke/restore in demos, is not documented on the pages fetched.
12. **The audit event fields for client-credentials issuance**, and whether `jti` there matches the token `jti` so that fleet-api's `credential_jti` can be correlated.

## 4. Mapping: our interface → Keycard

| Our element | Keycard equivalent | Evidence | Gap |
|---|---|---|---|
| `issue(action, target, environment, workflow_id, attempt)` | `@grant(resource)` + `access()` inside the activity | `temporal/__init__.py:265-352` | Resource fixed at decoration time. `action`, `target`, `environment`, `workflow_id` and `attempt` are not inputs to minting and cannot be put in token claims (only `sub`, `keycard_app_id` and `aud` are customizable, per token-claims.md). |
| Mint called in the body after the PENDING ledger row | Interceptor mints **before** the body | `temporal/__init__.py:504-510` | A denied or transient mint never reaches the body, so no `CREDENTIAL_DENIED` / `CREDENTIAL_UNAVAILABLE` ledger row is written. This breaks the evidence checks in `credential_denied` and `credential_transient` unless the ledger write moves or the SDK's `KeycardInterceptor` is bypassed. |
| `CredentialDenied` (non-retryable) | `ApplicationError type=KeycardAccessDenied, non_retryable=True` | `:521-527` [executed] | Different type string. `workflows.py:29` lists `"CredentialDenied"`, but the `non_retryable=True` flag already stops retries. Demo checks and the ledger use our names. |
| `CredentialUnavailable` (retryable) | `KeycardMintFailed` (exchange path) or **the raw keycardai exception** (app-as-itself) | `:528`, `:556-559` [executed: `NetworkError`] | The type name is unstable across paths. The `credential_transient` demo asserts `last_failure_type == "CredentialUnavailable"`. |
| `Credential.token` (`repr=False`) | `TokenResponse.access_token` | `models.py:79-88` | `TokenResponse` repr leaks the token. Copy it into our `Credential`. |
| `Credential.expires_at` (60 s) | `TokenResponse.expires_in` | `models.py:91` | The docs say 1 h. A 60 s lifetime is not known to be available (UNKNOWN 4). |
| `Credential.mode` = `LOCAL-ONLY` | none | n/a | Our label only, so we would need a `KEYCARD` mode for ledger and audit. |
| Broker `revoke(action)` / `restore(action)` | Cedar forbid or dependency removal (application, resource), or grant PATCH (user-delegated only) | concepts/policies.md, admin/revoke-a-grant.md | There is no documented per-application revoke and restore API. It is not per-action unless there are per-action resources. Policy changes are versioned policy sets. UNKNOWN 1, 11. |
| Broker `set_transient(action, n)` | none | n/a | Keycard has no fault injection. The transient demo needs another driver (UNKNOWN). |
| fleet-api HS256 `verify(token, key)` | RS256 + zone JWKS, pin `iss` + `aud` | token-claims.md; `oauth/server/verifier.py:95`, `:200`, `:420` | `TokenVerifier.verify_token` is `async` (`:363`) and fleet-api routes are sync, so we would need PyJWT `PyJWKClient` (needs `cryptography`, not in pyproject) or an async bridge. |
| fleet-api `environment` / `action` / `target` claim checks | `aud` (resource) and `scope` | token-claims.md, policy-schema.md | There are no custom claims, so the per-action/target binding must come from separate resources (`aud`) or scopes. The SDK sends no scope (`:450-452`). |
| `credential_jti` in the fleet-api response | `jti` ("used for audit correlation") | token-claims.md | Correlating it with Keycard audit is UNKNOWN 12. |

## 5. Design questions

1. **One resource per action, or one fleet-api resource?** Per-action resources (e.g. `http://localhost:8080/restart`) give per-action `aud` binding and per-action policy, but `@grant` is static, so `execute_action` would need one activity per action or a multi-resource grant (which mints every action's token on every call: all-or-nothing, 4× transactions). One resource keeps it simple but loses fleet-api's `action` check unless scopes are used, and scopes need a custom mint (UNKNOWN 6).
2. **SDK interceptor or a Keycard broker behind our interface?** Our `issue()` could call `keycardai.oauth.Client.client_credentials_grant(resource=..., scope=...)` directly inside the body. That keeps the ledger semantics, our error types, per-action choice and the `get_state` credential, at the cost of not using `@grant`. ADR 0002 currently names `@grant`.
3. **How do the revoke and restore demos map?** The candidates are a Cedar `forbid` on (worker application, restart resource) published and then rolled back, removing and re-adding a dependency, or `keycard agent api` calls from the demo harness. Each needs a live test for latency and error code. Already-issued tokens stay valid until `exp`, which matters for the "restore then rerun" check.
4. **What drives the transient demo?** Keycard cannot be told to fail transiently. Options: keep a fault shim in the Keycard broker (clearly labelled), or point at an unreachable zone URL for N attempts.
5. **Can fleet-api validate locally via JWKS?** Yes, per the docs (RS256, zone JWKS). It needs `cryptography` and a JWKS fetch at startup, with a cache. Do we keep the LOCAL-ONLY verifier as a fallback mode, selected by config?
6. **What do the `environment` and `target` checks become?** With no custom claims, either resource per environment and action (`staging` and `prod` give 8 resources), or drop the claim checks and rely on policy plus the URL.
7. **Transaction budget per run-all.** Each fresh credential is at least 1 transaction. Fleet-api counters in `docs/evidence/*.json` show 25 credentialed requests across the 7 demos that record counters, plus 3 failed mints. 3 demos (`mongo_down`, `voyage_429`, `worker_kill`) do not record counters. A rough estimate is 40–60 mints per run-all, which is **not measured**. On a 5,000/mo hard cap that is about 80–120 full runs per month, before tests and multi-resource grants.
8. **Token TTL mismatch.** 1 h tokens vs our 60 s. Does that weaken the "short-lived per-attempt credential" claim in the README and claims audit?
9. **Credential type.** The SDK's app-as-itself path supports only `ClientSecret`. That is acceptable locally, since the secret stays in `.env`, but rules out workload identity for the `@grant` path.
10. **Snapshot and verify reads.** Do `get_state` calls use a separate read resource with its own policy, or share the action resource?

## 6. PROPOSED console setup checklist (proposal only; nothing has been created)

Each step needs to be checked against the console when access is available. Names are placeholders.

1. **Zone**: use the organization's default zone or create a dev zone. Record the issuer `https://<zone-id>.keycard.cloud` as `KEYCARD_ZONE_URL` in `.env` only.
2. **Application** `gax-worker`: confidential, client ID + secret (the SDK app-as-itself path requires `ClientSecret`). Put `KEYCARD_CLIENT_ID` and `KEYCARD_CLIENT_SECRET` in `.env` only, never in the repo or logs.
3. **Resources** (bring your own, Keycard-issued tokens), depending on design question 1. Either:
   - (a) one resource `http://localhost:8080` (fleet-api), or
   - (b) per-action resources `http://localhost:8080/restart`, `/scale`, `/pause`, `/reset-offset`, plus a read resource `/state`.
   First confirm localhost identifiers are accepted (UNKNOWN 10).
4. **Dependencies**: `gax-worker` → each resource above (autonomous access).
5. **Policies**: a permit for `gax-worker` on the resources. Prepare a separate forbid (`gax-worker`, restart resource) to publish and roll back for the denial demo. Test it in the policy tester (https://docs.keycard.ai/admin/access-policies/testing.md) before running it live.
6. **Service account** for the Management API, if the demo harness is to toggle policy or dependencies by script (UNKNOWN 11).
7. **Verify by hand before writing code**: one `client_credentials_grant` from a scratch script, then decode the token header and claims (never print the token). Confirm `iss`, `aud`, `exp - iat`, `jti`, `scope` and `target`. Fetch the JWKS and confirm the discovery path (UNKNOWN 3, 4, 5).
8. **Record** the results in `docs/m0-findings.md`, per ADR 0002 step 7.
