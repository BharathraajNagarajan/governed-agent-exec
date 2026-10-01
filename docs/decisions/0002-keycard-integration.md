# 0002: Keycard integration

Status: PENDING VERIFICATION (K2 implemented; moves to Accepted when the K3 failure demos pass against Keycard)

## Context

Layer 2 of authorization (0001) is per-attempt credential issuance. fleet-api validates the credential. The intended provider is Keycard.

M1 and M2 ran on a **LOCAL-ONLY credential broker** behind this interface:

- `issue(action, target, environment, workflow_id, attempt) -> Credential`
- raises `CredentialDenied` (non-retryable) or `CredentialUnavailable` (retryable)

Keycard access has since been granted. K0 read the docs and SDK source, and K1 ran live calls against the zone (`docs/keycard-findings.md` §2, §4, §8). This ADR records the K2 decision based on those findings.

## Decision

### 1. A broker behind the existing interface, not `@grant`

`KeycardCredentialBroker` (`src/gax/credentials/keycard_broker.py`) implements the same `issue()` and is called **inside the activity body**, like the LOCAL-ONLY broker. It uses the sync `keycardai.oauth.Client` with `ClientSecret((KEYCARD_CLIENT_ID, KEYCARD_CLIENT_SECRET)).auth` and calls `client_credentials_grant(resource=...)` once per `issue()`. `keycardai-temporal` is not a dependency.

Reasons:

- `@grant` mints in a worker interceptor **before** the activity body runs (findings §2.1). A denied or transient mint would never reach `execute_action`, so the PENDING ledger row would never be finished as `CREDENTIAL_DENIED` / `CREDENTIAL_UNAVAILABLE`. Those rows are the evidence for the credential failure demos.
- `@grant` fixes resources at decoration time, so one `execute_action` could not choose a per-action resource from its input. Declaring every resource would mint all of them on every call (findings §5.1).
- On the app-as-itself path the SDK re-raises raw keycardai exceptions instead of a stable type (findings §2.3), and it treats `invalid_target` as retryable (§8.3). Our broker maps to our two types.

### 2. One URN resource per action

| Action | Resource |
|---|---|
| `get_state` (snapshot and verify reads) | `<prefix>:state` |
| `restart_consumer` | `<prefix>:restart` |
| `scale_consumer` | `<prefix>:scale` |
| `pause_pipeline` | `<prefix>:pause` |
| `reset_consumer_offset` | `<prefix>:reset-offset` |

`<prefix>` is `KEYCARD_RESOURCE_PREFIX`, default `urn:gax:fleet-api`. Each resource has a 1m Credential Lifetime (findings §7), which matches the LOCAL-ONLY 60 s TTL. Reasons: the token's `aud` is the only claim that can carry the action (only `sub`, `keycard_app_id` and `aud` are customizable, and client-credentials tokens have no `scope` or `target` claim, §8.2). Per-action resources also make per-action Cedar policy possible, which is how revocation works (§5 below).

### 3. Error mapping

| Keycard failure | Our type | Temporal |
|---|---|---|
| `OAuthProtocolError` with `access_denied`, `insufficient_authorization`, `invalid_client` or `invalid_target` | `CredentialDenied` | non-retryable |
| Any other `OAuthProtocolError` | `CredentialUnavailable` | retryable |
| `OAuthHttpError` 429 / 5xx | `CredentialUnavailable` | retryable |
| `OAuthHttpError` other 4xx | `CredentialDenied` | non-retryable |
| `NetworkError` (including timeouts; client timeout 10 s) and any other `OAuthError` | `CredentialUnavailable` | retryable |

`invalid_target` is denied on purpose. The SDK marks it retryable, but live it meant "unknown resource" (§8.3), which no retry can fix. Messages are compact: `KEYCARD <error> for <resource>: <error_description>`. The description names the denying policy and policy-set version, which goes into the ledger row as evidence. Exceptions are raised `from None`, so the SDK exception is not chained. The broker never keeps the `TokenResponse`, whose repr includes the token (§2.5). It copies only `access_token` into `Credential(token, expires_at from expires_in, mode="KEYCARD")`, and `Credential.token` has `repr=False`.

### 4. Explicit mode switch

`CREDENTIAL_MODE` is `local-only` (default) or `keycard`. `build_broker` selects by mode:

- `keycard`: requires `KEYCARD_ZONE_URL`, `KEYCARD_CLIENT_ID` and `KEYCARD_CLIENT_SECRET`, and fails fast (naming only the missing variables) if any is empty.
- `local-only`: the LOCAL-ONLY broker, unchanged.
- If `KEYCARD_ZONE_URL` is set and `CREDENTIAL_MODE` is not, `build_broker` refuses to start. This replaces the earlier rule "never used when `KEYCARD_ZONE_URL` is set": the LOCAL-ONLY broker is still never chosen silently when Keycard is configured, but it can be chosen explicitly.

fleet-api follows the same `CREDENTIAL_MODE`. In `keycard` mode it verifies RS256 tokens with PyJWT `PyJWKClient(<issuer>/openidconnect/jwks)` with an explicit `User-Agent` (the zone's JWKS returns 403 to the default urllib agent, §8.1). The issuer is pinned to `KEYCARD_ZONE_URL`, the audience to the route's resource (`GET` state → `:state`, `restart` → `:restart`, and so on), and `exp`, `iat`, `iss`, `aud`, `sub` and `jti` are required. A JWKS connection failure returns 503, and any other token error returns 401. `/healthz` reports `auth_mode`. LOCAL-ONLY verification is unchanged.

### 5. Revocation is a Keycard policy, not simulated

In `keycard` mode, `revoke()` and `restore()` raise `NotImplementedError` with a pointer to the console. Revoke = activate the `gax-zone-policies` policy set (an attribute-matching Cedar forbid on gax-worker x `urn:gax:fleet-api:restart`). Restore = re-activate `default-zone-policies` (§8.4, §8.5). Simulating revocation locally in front of Keycard would claim a Keycard behaviour we did not exercise. The flip is manual until a Management API service account exists (findings UNKNOWN 11).

### 6. Transient failures are simulated, and labelled

Keycard has no fault injection. In `keycard` mode the broker accepts the same grant store and honours `TRANSIENT` entries (`gax broker transient`, the `credential_transient` demo) **in front of** the real mint: it raises `CredentialUnavailable("SIMULATED transient failure in front of KEYCARD ...")` and logs `KEYCARD broker SIMULATED transient failure`, without calling Keycard. Once the count is used up, the next attempt mints for real. `REVOKED` entries in the store are ignored in this mode.

## Limitations

- **Environment and target binding is lost in `keycard` mode.** Keycard tokens carry no `environment`, `target` or `action` claims (§8.2), so fleet-api checks only the action, through `aud`. A `:restart` token is accepted for `prod/payments-consumer` as well as `staging/orders-consumer` (pinned by `test_no_environment_or_target_binding_in_keycard_mode`). Layer 1 policy still decides action, target, environment and params before `execute_action` runs. Restoring the binding would need resources per environment (8+ resources) or a token exchange with custom claims. Not done.
- Tokens already issued stay valid until `exp` after a revoke (docs, §1). With a 1m lifetime, that window is at most 60 s.
- Every `issue()` is one Keycard transaction. Snapshot, execute and verify mint separately, so one successful remediation costs at least 3 mints. Simulated transient attempts cost nothing.
- Time-to-effect of a policy-set flip is not measured (§8.5).

## Verification so far (K2)

- Unit tests with labelled test doubles (`KeycardClientTestDouble`, `KeycardZoneTestDouble`, `StubJWKSClientTestDouble`) cover resource mapping, every error mapping, the simulated transient, `build_broker` selection and fail-fast, and the fleet-api verifier (correct and wrong `aud`, wrong issuer, expired, foreign key, JWKS outage → 503).
- Live (`tests/live`, marker `keycard`, `KEYCARD_LIVE=1`): the real broker minted all 5 resources and the fleet-api verifier accepted each one against the live zone JWKS and rejected it for another action's audience. 5 passed, 5 mints (2026-10-01).
- **Not yet verified:** the failure demos (`credential_denied` via policy-set flip, `credential_transient`), the full remediation workflow against Keycard and fleet-api in `keycard` mode, and the no-credential-in-history scan with real tokens. These are K3. This ADR moves to Accepted only after they pass.

## Consequences

- Existing behaviour, tests and demos are unchanged in `local-only` mode. With `KEYCARD_ZONE_URL` filled in `.env`, local runs must now set `CREDENTIAL_MODE` explicitly.
- Ledger and audit records carry `credential_mode = KEYCARD` or `LOCAL-ONLY`. Nothing about the LOCAL-ONLY broker counts as Keycard verification, and simulated transients are labelled as such.
- Adding an action now needs a policy-table entry, a `RESOURCES` entry and a Keycard resource plus dependency.
