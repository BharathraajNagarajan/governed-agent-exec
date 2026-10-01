import logging
from datetime import datetime, timedelta, timezone
import httpx
import jwt
import pytest
from keycardai.oauth.exceptions import NetworkError, OAuthHttpError, OAuthProtocolError
from gax.config import Settings
from gax.credentials import CredentialDenied, CredentialUnavailable, KeycardCredentialBroker, LocalOnlyCredentialBroker, build_broker
from gax.credentials.keycard_broker import KeycardVerifier
from tests.doubles import KeycardClientTestDouble, KeycardZoneTestDouble, StubJWKSClientTestDouble

KEY = "unit-test-local-only-signing-key-0123456789"
SECRET = "unit-test-keycard-client-secret-value"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
ARGS = ("orders-consumer", "staging", "INC-1", 1)
EXPECTED = {
    "get_state": "urn:gax:fleet-api:state",
    "restart_consumer": "urn:gax:fleet-api:restart",
    "scale_consumer": "urn:gax:fleet-api:scale",
    "pause_pipeline": "urn:gax:fleet-api:pause",
    "reset_consumer_offset": "urn:gax:fleet-api:reset-offset",
}
KEYCARD = dict(credential_mode="keycard", keycard_zone_url="https://zone.example", keycard_client_id="client-id",
               keycard_client_secret=SECRET)


def broker(*outcomes, **kw):
    client = KeycardClientTestDouble(*outcomes)
    return KeycardCredentialBroker(client, clock=lambda: NOW, **kw), client


@pytest.mark.parametrize("action,resource", EXPECTED.items())
def test_resource_per_action(action, resource):
    b, client = broker()
    b.issue(action, *ARGS)
    assert client.resources == [resource]


def test_resource_prefix_is_configurable():
    b, client = broker(resource_prefix="urn:other:api")
    b.issue("restart_consumer", *ARGS)
    assert client.resources == ["urn:other:api:restart"]


def test_unknown_action_denied_without_mint():
    b, client = broker()
    with pytest.raises(CredentialDenied):
        b.issue("drop_database", *ARGS)
    assert client.resources == []


def test_credential_holds_only_token_expiry_and_mode(caplog):
    caplog.set_level(logging.DEBUG)
    b, _ = broker("keycard-test-double-secret-token")
    cred = b.issue("restart_consumer", *ARGS)
    assert (cred.token, cred.expires_at, cred.mode) == ("keycard-test-double-secret-token", NOW + timedelta(seconds=60), "KEYCARD")
    assert cred.token not in repr(cred)
    assert cred.token not in str(cred)
    assert cred.token not in caplog.text
    assert "KEYCARD broker issued credential" in caplog.text


@pytest.mark.parametrize("code", ["access_denied", "insufficient_authorization", "invalid_client", "invalid_target"])
def test_denied_codes_map_to_credential_denied(code, caplog):
    b, _ = broker(OAuthProtocolError(code, f"Access is denied by Policy {code}-policy"))
    with pytest.raises(CredentialDenied) as e:
        b.issue("restart_consumer", *ARGS)
    assert e.value.retryable is False
    assert str(e.value) == f"KEYCARD {code} for urn:gax:fleet-api:restart: Access is denied by Policy {code}-policy"
    assert e.value.__cause__ is None and e.value.__suppress_context__
    assert "CredentialDenied" in caplog.text


def test_invalid_target_denied_although_sdk_marks_it_retryable():
    error = OAuthProtocolError("invalid_target", "Requested authorization for unknown resource")
    assert error.retryable is True
    b, _ = broker(error)
    with pytest.raises(CredentialDenied):
        b.issue("restart_consumer", *ARGS)


@pytest.mark.parametrize("error", [
    OAuthProtocolError("server_error", "upstream failed"),
    OAuthProtocolError("invalid_response"),
    OAuthHttpError(429, "slow down", operation="POST /oauth/2/token"),
    OAuthHttpError(503, "unavailable", operation="POST /oauth/2/token"),
    NetworkError(cause=httpx.ConnectError("refused"), operation="POST /oauth/2/token"),
    NetworkError(cause=httpx.ReadTimeout("timed out"), operation="POST /oauth/2/token"),
])
def test_transient_errors_map_to_credential_unavailable(error):
    b, _ = broker(error)
    with pytest.raises(CredentialUnavailable) as e:
        b.issue("restart_consumer", *ARGS)
    assert e.value.retryable is True
    assert "urn:gax:fleet-api:restart" in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__


def test_protocol_error_without_description_is_compact():
    b, _ = broker(OAuthProtocolError("server_error"))
    with pytest.raises(CredentialUnavailable) as e:
        b.issue("restart_consumer", *ARGS)
    assert str(e.value) == "KEYCARD server_error for urn:gax:fleet-api:restart"


def test_http_client_error_is_denied():
    b, _ = broker(OAuthHttpError(400, "bad request", operation="POST /oauth/2/token"))
    with pytest.raises(CredentialDenied) as e:
        b.issue("restart_consumer", *ARGS)
    assert str(e.value) == "KEYCARD HTTP 400 for urn:gax:fleet-api:restart"


def test_simulated_transient_in_front_of_real_mint(caplog):
    b, client = broker()
    b.set_transient("restart_consumer", 2)
    for _ in range(2):
        with pytest.raises(CredentialUnavailable) as e:
            b.issue("restart_consumer", *ARGS)
        assert "SIMULATED" in str(e.value)
    assert client.resources == []
    assert b.issue("restart_consumer", *ARGS).token
    assert client.resources == ["urn:gax:fleet-api:restart"]
    assert "SIMULATED transient failure" in caplog.text


def test_simulated_transient_is_scoped_to_action():
    b, client = broker()
    b.set_transient("restart_consumer")
    assert b.issue("get_state", *ARGS).token
    assert client.resources == ["urn:gax:fleet-api:state"]


def test_revoke_and_restore_point_to_policy_set_flip():
    b, client = broker()
    for op in (b.revoke, b.restore):
        with pytest.raises(NotImplementedError, match="gax-zone-policies"):
            op("restart_consumer")
    assert client.resources == []


def test_build_broker_keycard_mode():
    b = build_broker(Settings(**KEYCARD))
    assert isinstance(b, KeycardCredentialBroker)
    assert b.mode == "KEYCARD"
    assert b.resource_prefix == "urn:gax:fleet-api"


@pytest.mark.parametrize("field,name", [("keycard_zone_url", "KEYCARD_ZONE_URL"), ("keycard_client_id", "KEYCARD_CLIENT_ID"),
                                        ("keycard_client_secret", "KEYCARD_CLIENT_SECRET")])
def test_build_broker_keycard_mode_fails_fast(field, name):
    with pytest.raises(ValueError, match=name) as e:
        build_broker(Settings(**{**KEYCARD, field: ""}))
    assert SECRET not in str(e.value)


def test_build_broker_explicit_local_only_ignores_keycard_settings():
    b = build_broker(Settings(**{**KEYCARD, "credential_mode": "local-only"}, local_broker_signing_key=KEY))
    assert isinstance(b, LocalOnlyCredentialBroker)


def test_build_broker_default_is_local_only():
    assert build_broker(Settings(local_broker_signing_key=KEY)).mode == "LOCAL-ONLY"


def test_invalid_credential_mode_rejected():
    with pytest.raises(ValueError):
        Settings(credential_mode="vault")


@pytest.fixture
def zone():
    return KeycardZoneTestDouble()


@pytest.fixture
def verifier(zone):
    return KeycardVerifier(zone.ISSUER, jwks_client=StubJWKSClientTestDouble(zone))


@pytest.mark.parametrize("action,resource", EXPECTED.items())
def test_verifier_accepts_matching_audience(verifier, zone, action, resource):
    assert verifier.verify(zone.token(resource), action)["aud"] == resource


def test_verifier_rejects_wrong_audience(verifier, zone):
    with pytest.raises(jwt.InvalidAudienceError):
        verifier.verify(zone.token("urn:gax:fleet-api:state"), "restart_consumer")


def test_verifier_rejects_wrong_issuer(verifier, zone):
    with pytest.raises(jwt.InvalidIssuerError):
        verifier.verify(zone.token("urn:gax:fleet-api:restart", iss="https://other.keycard.example"), "restart_consumer")


def test_verifier_rejects_expired(verifier, zone):
    with pytest.raises(jwt.ExpiredSignatureError):
        verifier.verify(zone.token("urn:gax:fleet-api:restart", ttl=-120), "restart_consumer")


def test_verifier_rejects_foreign_signing_key(verifier, zone):
    with pytest.raises(jwt.InvalidSignatureError):
        verifier.verify(zone.token("urn:gax:fleet-api:restart", key=KeycardZoneTestDouble().key), "restart_consumer")


def test_verifier_rejects_hs256_token(verifier):
    token = jwt.encode({"iss": KeycardZoneTestDouble.ISSUER, "aud": "urn:gax:fleet-api:restart", "exp": 4102444800},
                       KEY, algorithm="HS256", headers={"kid": "test-double-kid"})
    with pytest.raises(jwt.PyJWTError):
        verifier.verify(token, "restart_consumer")


def test_verifier_sends_explicit_user_agent_to_zone_jwks():
    v = KeycardVerifier("https://zone.example/")
    assert v.issuer == "https://zone.example"
    assert v.jwks.uri == "https://zone.example/openidconnect/jwks"
    assert v.jwks.headers["User-Agent"].startswith("gax-fleet-api/")
