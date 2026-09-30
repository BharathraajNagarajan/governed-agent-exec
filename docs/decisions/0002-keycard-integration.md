# 0002: Keycard integration

Status: PENDING PRIVATE-BETA ACCESS

## Context

Layer 2 of authorization (0001) is per-activity credential issuance. The intended provider is Keycard, using `@grant` on the activity so each attempt obtains its own short-lived credential, and fleet-api validates the resulting JWT.

Keycard is pending private-beta access (signup is currently a private-beta waitlist). `KEYCARD_ZONE_URL`, `KEYCARD_CLIENT_ID` and `KEYCARD_CLIENT_SECRET` exist in `.env` but are empty. No Keycard call has been made or verified (M0.5).

## Decision

Until private-beta access is granted, `execute_action` uses a **LOCAL-ONLY credential broker** behind the same interface Keycard will satisfy.

Interface (conceptual):

- `issue(action, target, environment, workflow_id, attempt) -> Credential`
- raises `CredentialDenied` (non-retryable) or `CredentialUnavailable` (retryable)

LOCAL-ONLY broker rules:

- Clearly labelled LOCAL-ONLY in its module name, its log lines and in every audit record it touches.
- Signs short-lived JWTs with a local key read from `.env`. fleet-api validates them with the matching key.
- Grants are held in local state that tests and demos can revoke and restore, to drive the "credential access denied" and "credential transient error" failure demos.
- Never used when `KEYCARD_ZONE_URL` is set.

The credential is fetched inside the activity on every attempt and is never returned to the workflow, logged, or shown to the LLM.

## What switching to Keycard requires

1. Private-beta access granted and a zone provisioned; zone URL, client ID and client secret filled in `.env`.
2. A Keycard-backed implementation of the same interface using `@grant` on `execute_action`.
3. Map Keycard errors onto `CredentialDenied` (non-retryable) and `CredentialUnavailable` (retryable).
4. fleet-api validates Keycard-issued JWTs (issuer, audience, signing keys from the zone) instead of the local key.
5. Grant revoke/restore for the failure demos done through Keycard instead of local state.
6. Re-run acceptance criteria 5 and 6 (no credentials in history; denial non-retryable, restoration makes a rerun succeed) against Keycard.
7. A Keycard spike recorded in `docs/m0-findings.md` with versions and observed behaviour, then this ADR moves to Accepted.

## Consequences

- M1 work can proceed without Keycard, and the failure matrix entries for credentials are demonstrable locally.
- Nothing about the LOCAL-ONLY broker counts as Keycard verification.
