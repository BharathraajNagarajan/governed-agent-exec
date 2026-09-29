from gax.config import Settings
from gax.credentials.base import Credential, CredentialBroker, CredentialDenied, CredentialError, CredentialUnavailable
from gax.credentials.local_only_broker import LocalOnlyCredentialBroker

__all__ = [
    "Credential",
    "CredentialBroker",
    "CredentialDenied",
    "CredentialError",
    "CredentialUnavailable",
    "LocalOnlyCredentialBroker",
    "build_broker",
]


def build_broker(settings: Settings, store=None) -> CredentialBroker:
    if settings.keycard_zone_url:
        raise NotImplementedError("KEYCARD_ZONE_URL is set but the Keycard broker is not implemented (ADR 0002)")
    return LocalOnlyCredentialBroker(settings.local_broker_signing_key.get_secret_value(), store=store)
