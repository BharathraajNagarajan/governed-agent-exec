import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
import jwt
from keycardai.oauth import Client
from keycardai.oauth.exceptions import OAuthError, OAuthHttpError, OAuthProtocolError
from keycardai.oauth.server.credentials import ClientSecret
from keycardai.oauth.types.models import ClientConfig
from gax.credentials.base import Credential, CredentialDenied, CredentialUnavailable
from gax.credentials.local_only_broker import ALL, TRANSIENT, InMemoryGrantStore

MODE = "KEYCARD"
DEFAULT_PREFIX = "urn:gax:fleet-api"
RESOURCES = {
    "get_state": "state",
    "restart_consumer": "restart",
    "scale_consumer": "scale",
    "pause_pipeline": "pause",
    "reset_consumer_offset": "reset-offset",
}
DENIED_CODES = frozenset({"access_denied", "insufficient_authorization", "invalid_client", "invalid_target"})
ALGORITHM = "RS256"
USER_AGENT = "gax-fleet-api/0.1"
REVOCATION = ("KEYCARD mode does not simulate revoke/restore: revoke by activating the gax-zone-policies policy set "
              "(forbid on the action's resource) in the Keycard console, restore by re-activating default-zone-policies")

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def resource_for(action: str, prefix: str = DEFAULT_PREFIX) -> str:
    return f"{prefix}:{RESOURCES[action]}"


def build_client(zone_url: str, client_id: str, client_secret: str, timeout: float = 10.0) -> Client:
    return Client(zone_url.rstrip("/"), auth=ClientSecret((client_id, client_secret)).auth, config=ClientConfig(timeout=timeout))


class KeycardCredentialBroker:
    mode = MODE

    def __init__(self, client, resource_prefix: str = DEFAULT_PREFIX, store=None, clock: Callable[[], datetime] = _utcnow):
        self._client = client
        self.resource_prefix = resource_prefix
        self.store = store if store is not None else InMemoryGrantStore()
        self._clock = clock

    def revoke(self, action: str = ALL) -> None:
        raise NotImplementedError(REVOCATION)

    def restore(self, action: str = ALL) -> None:
        raise NotImplementedError(REVOCATION)

    def set_transient(self, action: str = ALL, count: Optional[int] = None) -> None:
        self.store.put(action, TRANSIENT, count)
        log.warning("KEYCARD broker SIMULATED transient set scope=%s count=%s", action, count)

    def _mint(self, resource: str):
        try:
            return self._client.client_credentials_grant(resource=resource)
        except OAuthProtocolError as e:
            message = f"KEYCARD {e.error} for {resource}" + (f": {e.error_description}" if e.error_description else "")
            if e.error in DENIED_CODES:
                raise CredentialDenied(message) from None
            raise CredentialUnavailable(message) from None
        except OAuthHttpError as e:
            message = f"KEYCARD HTTP {e.status_code} for {resource}"
            if e.retryable:
                raise CredentialUnavailable(message) from None
            raise CredentialDenied(message) from None
        except OAuthError as e:
            raise CredentialUnavailable(f"KEYCARD {type(e).__name__} for {resource}") from None

    def issue(self, action: str, target: str, environment: str, workflow_id: str, attempt: int) -> Credential:
        context = f"action={action} target={target} environment={environment} workflow_id={workflow_id} attempt={attempt}"
        if action not in RESOURCES:
            raise CredentialDenied(f"KEYCARD has no resource for action {action}")
        resource = resource_for(action, self.resource_prefix)
        if any(self.store.consume_transient(s) for s in (action, ALL)):
            log.warning("KEYCARD broker SIMULATED transient failure %s", context)
            raise CredentialUnavailable(f"SIMULATED transient failure in front of KEYCARD for {action}")
        now = self._clock()
        try:
            response = self._mint(resource)
        except (CredentialDenied, CredentialUnavailable) as e:
            log.warning("KEYCARD broker %s %s: %s", type(e).__name__, context, e)
            raise
        expires_at = now + timedelta(seconds=response.expires_in or 0)
        credential = Credential(token=response.access_token, expires_at=expires_at, mode=MODE)
        log.info("KEYCARD broker issued credential %s resource=%s exp=%s", context, resource, expires_at.isoformat())
        return credential


class KeycardVerifier:
    def __init__(self, issuer: str, resource_prefix: str = DEFAULT_PREFIX, jwks_client: Optional[jwt.PyJWKClient] = None):
        self.issuer = issuer.rstrip("/")
        self.resource_prefix = resource_prefix
        self.jwks = jwks_client or jwt.PyJWKClient(f"{self.issuer}/openidconnect/jwks", headers={"User-Agent": USER_AGENT})

    def verify(self, token: str, action: str) -> dict:
        key = self.jwks.get_signing_key_from_jwt(token).key
        return jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            audience=resource_for(action, self.resource_prefix),
            issuer=self.issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "jti"]},
        )
