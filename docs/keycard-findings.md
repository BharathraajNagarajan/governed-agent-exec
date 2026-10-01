# Keycard findings (Phase K0: discovery)

Date: 2026-09-30. Status: sections 1-6 are K0 discovery, written before any Keycard zone was called. §7 covers the console setup, and §8 the live spike results (2026-10-01) from `spikes/m0_5_keycard/spike.py`. `src/`, `tests/`, `fleet_api/` and `scripts/` were not changed. The LOCAL-ONLY broker is still the only credential path, and ADR 0002 stays at PENDING.

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

1. **Revoking or denying an autonomous (client-credentials) application.** The revoke docs cover user-delegated grants only. It is unknown whether removing a dependency, or adding a Cedar forbid, takes effect for the next `client_credentials_grant` immediately, and which error code it returns (`access_denied` vs `insufficient_authorization`). **Resolved live (§8.3, §8.5):** a Cedar forbid inside an activated policy set denies the next `client_credentials_grant` with a hard `access_denied` (not `insufficient_authorization`), and re-activating the default set restores access. Propagation latency was not measured. Removing a dependency was not tested.
2. **The error the zone returns for a client-credentials request to an undeclared or forbidden resource.** It could be a hard `access_denied` or a soft denial (HTTP 200 with the resource missing from `target`). A soft denial would *not* raise in the interceptor. **Resolved live (§8.3):** all denials were hard. An existing resource with no dependency gives `access_denied` (retryable False). A nonexistent resource gives `invalid_target`, which the SDK marks retryable True. No soft denial was observed.
3. **Discovery endpoint path.** The token-claims page says `/.well-known/openid-configuration` + `/openidconnect/jwks`, and the protect-an-API guide says `/.well-known/oauth-authorization-server`. Confirm which one the zone serves. **Resolved live (§8.1):** both return 200 with identical documents. The JWKS needs an explicit User-Agent (403 otherwise).
4. **Actual token lifetime for client-credentials tokens**, and whether a Resource can shorten it. The docs say 1 h for access tokens and don't say whether it is configurable. Ours is 60 s. **Resolved by the console (§7):** lifetime is configurable per resource, 1m-24h.
5. **Claim contents of a client-credentials token**: the `sub` value, whether `scope` is present when none is requested, and the `target` claim format. **Resolved live (§8.2):** `sub` = `keycard_app_id` = `urn:app:gax-worker`, `aud` = the exact resource URN, and there is no `scope` claim and no `target` claim. `jti` is present.
6. **Whether Cedar can see the requested `scope`** (`context.scopes`) on client credentials, so that per-action scopes on one resource could be policy-gated.
7. **Rate limits on the token endpoint.** None are documented. A `run-all` mints many tokens in short bursts.
8. **What "validating requests" bills.** It is unclear whether local JWKS validation in fleet-api counts as a transaction.
9. **Private-beta plan terms.** It is unknown whether the account is on Starter's 5,000/mo hard cap or a beta allocation, and what happens at the cap (presumably mint failures; it is not documented what code they return).
10. **Whether `http://localhost:<port>` is accepted as a Resource identifier** for a non-public API. The token-claims page shows `http://localhost:9090` as an example `aud`, but it is not tested. **Avoided (§7):** URN identifiers are used instead of localhost URLs.
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
| `Credential.expires_at` (60 s) | `TokenResponse.expires_in` | `models.py:91`; §7, §8.2 | Credential Lifetime is configured at 1m per resource (VERIFIED-CONSOLE). `expires_in` = 60 and `exp - iat` = 60 were observed (VERIFIED-LIVE), matching our 60 s. |
| `Credential.mode` = `LOCAL-ONLY` | none | n/a | Our label only, so we would need a `KEYCARD` mode for ledger and audit. |
| Broker `revoke(action)` / `restore(action)` | Policy-set activation flip: activate `gax-zone-policies` (attribute-matching Cedar forbid on gax-worker x `urn:gax:fleet-api:restart`) to revoke, re-activate `default-zone-policies` to restore | §8.4 (VERIFIED-CONSOLE), §8.5 (VERIFIED-LIVE) | It is per-action because resources are per-action. Revoke gives a hard `access_denied`, and pause stays issued. The flip is done by hand in the console. Scripting it needs the Management API (UNKNOWN 11). Propagation latency was not measured. |
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

## 6. Console setup checklist (written as a proposal in K0; steps 1-5 and 7 now exist, see §7 and §8)

This was written before console access. The zone, application, URN resources, dependencies and policy set have since been created, and step 7 was run live (§8). Steps 6 and 8 remain open (step 8 is in `docs/m0-findings.md`).

1. **Zone**: use the organization's default zone or create a dev zone. Record the issuer `https://<zone-id>.keycard.cloud` as `KEYCARD_ZONE_URL` in `.env` only.
2. **Application** `gax-worker`: confidential, client ID + secret (the SDK app-as-itself path requires `ClientSecret`). Put `KEYCARD_CLIENT_ID` and `KEYCARD_CLIENT_SECRET` in `.env` only, never in the repo or logs.
3. **Resources** (Zone Provider / Keycard STS, Credential Lifetime 1m): per-action URN resources `urn:gax:fleet-api:restart`, `urn:gax:fleet-api:scale`, `urn:gax:fleet-api:pause` and `urn:gax:fleet-api:reset-offset`, plus the read resource `urn:gax:fleet-api:state`. URNs avoid localhost identifiers (UNKNOWN 10).
4. **Dependencies**: `gax-worker` → each resource above (autonomous access).
5. **Policies**: a permit for `gax-worker` on the resources. Prepare a separate forbid (`gax-worker`, restart resource) to publish and roll back for the denial demo. Test it in the policy tester (https://docs.keycard.ai/admin/access-policies/testing.md) before running it live.
6. **Service account** for the Management API, if the demo harness is to toggle policy or dependencies by script (UNKNOWN 11).
7. **Verify by hand before writing code**: one `client_credentials_grant` from a scratch script, then decode the token header and claims (never print the token). Confirm `iss`, `aud`, `exp - iat`, `jti`, `scope` and `target`. Fetch the JWKS and confirm the discovery path (UNKNOWN 3, 4, 5).
8. **Record** the results in `docs/m0-findings.md`, per ADR 0002 step 7.

## 7. K1 console setup (in progress, 2026-09-30)

Observed in the Keycard console:

- Application `gax-worker` created, identifier `urn:app:gax-worker`, consent Implicit, credential type "Client ID & Secret" (the type the SDK app-as-itself path requires, see §2.1). Values are stored in `.env` only.
- The resource creation form ("Add manually") accepts URN identifiers (e.g. `urn:service:name`). Resources use the "Zone Provider (Keycard STS)" credential provider.
- The resource form has "Credential Lifetime" under Advanced options: default 24h, range 1m-24h. This resolves UNKNOWN 4 (lifetime is configurable per resource). A 1m lifetime matches the LOCAL-ONLY broker's 60 s TTL.
- Planned resources (one per action, the decision from design question 1): `urn:gax:fleet-api:state`, `urn:gax:fleet-api:restart`, `urn:gax:fleet-api:scale`, `urn:gax:fleet-api:pause` and `urn:gax:fleet-api:reset-offset`, each with a 1m lifetime. Status: being created; not yet verified by a live mint.

## 8. K1 live results (2026-10-01)

Labels: **VERIFIED-LIVE** means observed from a real call to the zone using `spikes/m0_5_keycard/spike.py` (Part A ran 2026-09-30, Part B on 2026-10-01). **VERIFIED-CONSOLE** means observed in the Keycard console. The spike prints decoded claims and error fields only. No client secret or access token was printed, logged or committed. Total mints: 16 (11 in Part A, 5 in Part B).

### 8.1 Discovery and JWKS (VERIFIED-LIVE)

- `<issuer>/.well-known/openid-configuration` and `<issuer>/.well-known/oauth-authorization-server` both returned 200 with identical documents. `issuer` equals `KEYCARD_ZONE_URL`. `token_endpoint` is `<issuer>/oauth/2/token` and `jwks_uri` is `<issuer>/openidconnect/jwks`. RS256 is the only signing algorithm. The grant types include `client_credentials` and token exchange.
- The JWKS endpoint returned **HTTP 403** to PyJWT's default `Python-urllib` User-Agent, and 200 when an explicit `User-Agent` header was sent (`PyJWKClient(..., headers={"User-Agent": ...})`). A fleet-api verifier must set this header.

### 8.2 Minting and claims (VERIFIED-LIVE)

- All 5 resources (`urn:gax:fleet-api:{state,restart,scale,pause,reset-offset}`) were minted twice, for 10 mints. Latency was about 316-382 ms warm and about 1.2-2.4 s on the first call.
- `expires_in` was 60 and `exp - iat` was 60, matching the 1m Credential Lifetime configured per resource.
- Claims: `iss` is the zone and `aud` is the exact resource URN. `sub` and `keycard_app_id` are both `urn:app:gax-worker`, and `client_id` is `KEYCARD_CLIENT_ID`. There was no `scope` claim and no `target` claim. `jti` was present. The header `alg` was RS256.
- `PyJWKClient` (with the User-Agent), RS256, with issuer and audience pinned: all 5 tokens were valid. Verifying against the wrong audience raised `InvalidAudienceError`.

### 8.3 Denials (VERIFIED-LIVE)

| Request | Exception | `error` | `retryable` (SDK) | Notes |
|---|---|---|---|---|
| `urn:gax:fleet-api:undeclared` (resource does not exist) | `OAuthProtocolError` | `invalid_target` | True | "Requested authorization for unknown resource". Also seen for a malformed request to `/events` without the zone prefix (1 wasted mint). |
| `<issuer>/events` (Events API resource exists, no dependency from gax-worker) | `OAuthProtocolError` | `access_denied` | False | "Application "gax-worker" is not allowed to access "Events API"." 1790 ms. |
| `urn:gax:fleet-api:restart` with the forbid active | `OAuthProtocolError` | `access_denied` | False | "Access to "fleet-api restart" is denied by Policy "gax-forbid-restart" in version 3 of Policy Set "gax-zone-policies"." 2431 ms. |

- **No soft denial was observed.** Every denial raised an exception, and no token was issued with a narrowed audience or a `target` claim.
- The SDK's `PERMANENT_ERROR_CODES` (§2.3) does not include `invalid_target`, so the SDK reports it as retryable. A Keycard broker behind our interface must map `invalid_target` to `CredentialDenied`, as well as `access_denied`.
- The denial description names the policy and the policy set version. That is useful as ledger evidence.

### 8.4 Console policy behaviour (VERIFIED-CONSOLE)

- The active default set, `default-zone-policies`, is read-only. Changing it requires Duplicate, then editing the copy, then Activate. The copy is `gax-zone-policies` (schema 2026-06-18). A standalone policy created on schema 2026-03-16 could not be added to it (the picker showed a warning), so the forbid was created inside the set.
- Cedar entity references such as `Keycard::Application::"urn:app:gax-worker"` did not match, and the test stayed Allow. Entity IDs are internal IDs, not identifiers. A forbid that matches on attributes works:

  ```cedar
  forbid(principal is Keycard::Application, action, resource is Keycard::Resource)
  when { principal.identifier == "urn:app:gax-worker" && resource.identifier == "urn:gax:fleet-api:restart" };
  ```

- The set-level Run test gave gax-worker x restart = Deny (determining policy `<set-id>::policy0`, the forbid) and gax-worker x pause = Allow (`default-app-direct-access`). Policy tests evaluate the **saved** set, not unsaved edits.
- Revoke = activate `gax-zone-policies`. Restore = re-activate `default-zone-policies`.

### 8.5 Revoke and restore timing (VERIFIED-LIVE, partial)

- With `gax-zone-policies` active, `poll restart --until denied` was denied on try 1 (2.4 s). The set had been activated before the poll started, so **time-to-effect was not measured**. This only shows that the denial was in effect.
- With the forbid active, `mint pause` was still issued (1951 ms, `aud` = pause, 60 s, signature valid). The forbid blocks restart only.
- After `default-zone-policies` was re-activated in the console, `poll restart --until allowed` was issued on try 1 (3875 ms, `aud` = restart, signature valid). The click time was not recorded, so **time-to-restore was not measured**. It is at most the gap between activation and the first poll.
- Not tested: whether a restart token issued before the forbid stays valid until `exp`. The docs say it does (§1).

## 9. K3 results (2026-10-01)

Labels follow §8. **VERIFIED-LIVE** here means observed while the full stack ran with `CREDENTIAL_MODE=keycard`: the worker minted with `KeycardCredentialBroker`, and fleet-api verified against the zone JWKS. No client secret or token was printed, logged or committed. Total mints: 54 (46 in the demo run, 3 for `INC-K3-001`, 5 for the live tests).

### 9.1 Failure demos (VERIFIED-LIVE)

- `gax demo run all`: **12/12 PASS**, 310.9 s. Every demo checked that its ledger rows carry `credential_mode` `KEYCARD` and that `/healthz` reports `auth_mode` `KEYCARD`. The full table is in `docs/failure-semantics.md` (Observed run: KEYCARD mode), and sanitized results are in `docs/evidence/keycard/`.
- `credential_denied`: the operator activated `gax-zone-policies` while the demo probed `restart` every 10 s. The workflow's mint then failed with `access_denied`, and the description `Access to "fleet-api restart" is denied by Policy "gax-forbid-restart" in version 3 of Policy Set "gax-zone-policies".` went into the ledger row. The attempt was non-retryable, made 1 `execute_action` attempt and sent 0 restart requests to fleet-api. After `default-zone-policies` was re-activated, the same incident id reran VERIFIED.
- `credential_transient`: SIMULATED in front of the mint (ADR 0002 §6), as labelled. Only attempt 3 called Keycard.

### 9.2 Policy-set flip timing (VERIFIED-LIVE, upper bounds only)

| Flip | Probes | Prompt to first changed probe |
|---|---|---|
| Activate `gax-zone-policies` (revoke) | ISSUED ×3, then DENIED | 33.7 s |
| Re-activate `default-zone-policies` (restore) | DENIED ×5, then ISSUED | 53.4 s |

The click times were not recorded, so these times include the operator's reaction time. This run does not separate propagation delay from click time. **Time-to-effect is still UNKNOWN.**

### 9.3 Real-LLM incident (VERIFIED-LIVE)

`INC-K3-001` (staging `orders-consumer`, "0 active members, lag climbing"): the real LLM proposed `restart_consumer` with cited runbook chunks, and policy-v1 returned ALLOW. It made 3 Keycard mints (`state`, `restart`, `state`) and was VERIFIED with `restart_count` +1. The ledger and the audit record both carry `credential_mode` `KEYCARD`. The JWT-shape scan found 0 hits in its 47 history events.

### 9.4 Fixes found in K3

- The secret scan matched only `eyJhbGciOi`. A Keycard RS256 header can serialize `kid` first, so it would not start with that prefix. The demo scan and the integration-test history scan now match any JWT shape and `Bearer <token>`. A unit test covers a `{"kid":...}`-first RS256 token.
- fleet-api now refuses to start when `KEYCARD_ZONE_URL` is set and `CREDENTIAL_MODE` is not, the same rule as `build_broker`. Before this fix it silently ran LOCAL-ONLY.
- `start-stack.ps1` printed `(LOCAL-ONLY auth)` unconditionally. It now prints the `auth_mode` it reads from `/healthz`.

### 9.5 Still not tested

- Whether a restart token issued before the forbid stays valid until `exp` (§8.5).
- Precise time-to-effect of a policy-set flip.
