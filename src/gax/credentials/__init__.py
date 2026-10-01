from gax.config import Settings
from gax.credentials.base import Credential, CredentialBroker, CredentialDenied, CredentialError, CredentialUnavailable
from gax.credentials.keycard_broker import KeycardCredentialBroker, build_client
from gax.credentials.local_only_broker import LocalOnlyCredentialBroker

__all__ = [
    "Credential",
    "CredentialBroker",
    "CredentialDenied",
    "CredentialError",
    "CredentialUnavailable",
    "KeycardCredentialBroker",
    "LocalOnlyCredentialBroker",
    "build_broker",
]


def build_broker(settings: Settings, store=None) -> CredentialBroker:
    if settings.credential_mode == "keycard":
        secret = settings.keycard_client_secret.get_secret_value()
        required = {"KEYCARD_ZONE_URL": settings.keycard_zone_url, "KEYCARD_CLIENT_ID": settings.keycard_client_id, "KEYCARD_CLIENT_SECRET": secret}
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"CREDENTIAL_MODE=keycard requires {', '.join(missing)}")
        client = build_client(settings.keycard_zone_url, settings.keycard_client_id, secret)
        return KeycardCredentialBroker(client, settings.keycard_resource_prefix, store=store)
    if settings.keycard_zone_url and "credential_mode" not in settings.model_fields_set:
        raise NotImplementedError("KEYCARD_ZONE_URL is set but CREDENTIAL_MODE is not; set CREDENTIAL_MODE=local-only or keycard (ADR 0002)")
    return LocalOnlyCredentialBroker(settings.local_broker_signing_key.get_secret_value(), store=store)
