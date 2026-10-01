import os
from datetime import datetime, timedelta, timezone
import jwt
import pytest
from gax.config import get_settings
from gax.credentials import KeycardCredentialBroker, build_broker
from gax.credentials.keycard_broker import RESOURCES, KeycardVerifier, resource_for

pytestmark = [pytest.mark.keycard, pytest.mark.skipif(os.environ.get("KEYCARD_LIVE") != "1", reason="live Keycard zone: set KEYCARD_LIVE=1")]


@pytest.fixture(scope="module")
def settings():
    s = get_settings()
    assert s.credential_mode == "keycard", "set CREDENTIAL_MODE=keycard"
    return s


@pytest.fixture(scope="module")
def broker(settings):
    b = build_broker(settings)
    assert isinstance(b, KeycardCredentialBroker)
    return b


@pytest.fixture(scope="module")
def verifier(settings):
    return KeycardVerifier(settings.keycard_zone_url, settings.keycard_resource_prefix)


@pytest.mark.parametrize("action", list(RESOURCES))
def test_live_mint_and_verify(broker, verifier, settings, action, caplog):
    before = datetime.now(timezone.utc)
    cred = broker.issue(action, "orders-consumer", "staging", "KEYCARD-LIVE-TEST", 1)
    assert cred.mode == "KEYCARD"
    assert timedelta(seconds=0) < cred.expires_at - before <= timedelta(seconds=65)
    claims = verifier.verify(cred.token, action)
    assert claims["aud"] == resource_for(action, settings.keycard_resource_prefix)
    assert claims["iss"] == settings.keycard_zone_url.rstrip("/")
    assert claims["exp"] - claims["iat"] <= 60
    assert claims["jti"]
    other = next(a for a in RESOURCES if a != action)
    with pytest.raises(jwt.InvalidAudienceError):
        verifier.verify(cred.token, other)
    assert cred.token not in repr(cred)
    assert cred.token not in caplog.text
