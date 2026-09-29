import logging
import time
from datetime import datetime, timedelta, timezone
import jwt
import pytest
from gax.config import Settings
from gax.credentials import CredentialDenied, CredentialUnavailable, LocalOnlyCredentialBroker, build_broker
from gax.credentials.local_only_broker import verify

KEY = "unit-test-local-only-signing-key-0123456789"
ARGS = ("restart_consumer", "orders-consumer", "staging", "INC-1", 1)


@pytest.fixture
def broker():
    return LocalOnlyCredentialBroker(KEY)


def test_issue_signs_scoped_local_only_token(broker):
    cred = broker.issue(*ARGS)
    claims = verify(cred.token, KEY)
    assert cred.mode == "LOCAL-ONLY"
    assert claims["mode"] == "LOCAL-ONLY"
    assert claims["iss"] == "gax-local-only-broker"
    assert (claims["action"], claims["target"], claims["environment"], claims["workflow_id"], claims["attempt"]) == ARGS
    assert claims["exp"] - claims["iat"] == 60


def test_token_not_in_repr_or_logs(broker, caplog):
    caplog.set_level(logging.INFO)
    cred = broker.issue(*ARGS)
    assert cred.token not in repr(cred)
    assert cred.token not in str(cred)
    assert cred.token not in caplog.text
    assert KEY not in caplog.text
    assert "LOCAL-ONLY broker issued credential" in caplog.text


def test_wrong_key_rejected(broker):
    with pytest.raises(jwt.InvalidSignatureError):
        verify(broker.issue(*ARGS).token, "another-local-only-signing-key-0123456789")


def test_revoke_denies_and_restore_allows(broker):
    broker.revoke()
    with pytest.raises(CredentialDenied) as e:
        broker.issue(*ARGS)
    assert e.value.retryable is False
    broker.restore()
    assert broker.issue(*ARGS).token


def test_revoke_is_scoped_to_action(broker):
    broker.revoke("reset_consumer_offset")
    assert broker.issue(*ARGS).token
    with pytest.raises(CredentialDenied):
        broker.issue("reset_consumer_offset", "orders-consumer", "staging", "INC-1", 1)


def test_transient_count_then_recovers(broker):
    broker.set_transient(count=2)
    for _ in range(2):
        with pytest.raises(CredentialUnavailable) as e:
            broker.issue(*ARGS)
        assert e.value.retryable is True
    assert broker.issue(*ARGS).token


def test_transient_until_restored(broker):
    broker.set_transient()
    for _ in range(3):
        with pytest.raises(CredentialUnavailable):
            broker.issue(*ARGS)
    broker.restore()
    assert broker.issue(*ARGS).token


def test_revoke_takes_precedence_over_transient(broker):
    broker.set_transient("restart_consumer")
    broker.revoke()
    with pytest.raises(CredentialDenied):
        broker.issue(*ARGS)


def test_expired_token_rejected():
    past = datetime.now(timezone.utc) - timedelta(minutes=10)
    token = LocalOnlyCredentialBroker(KEY, ttl_seconds=60, clock=lambda: past).issue(*ARGS).token
    with pytest.raises(jwt.ExpiredSignatureError):
        verify(token, KEY)


def test_short_ttl_expires_in_real_time():
    token = LocalOnlyCredentialBroker(KEY, ttl_seconds=1).issue(*ARGS).token
    assert verify(token, KEY)
    time.sleep(2.1)
    with pytest.raises(jwt.ExpiredSignatureError):
        verify(token, KEY)


def test_non_local_only_token_rejected():
    token = jwt.encode({"iss": "gax-local-only-broker", "aud": "fleet-api", "sub": "x", "jti": "j", "iat": 0,
                        "exp": 4102444800, "mode": "KEYCARD"}, KEY, algorithm="HS256")
    with pytest.raises(jwt.InvalidTokenError):
        verify(token, KEY)


def test_short_key_rejected():
    with pytest.raises(ValueError):
        LocalOnlyCredentialBroker("short")


def test_build_broker_local_only():
    b = build_broker(Settings(local_broker_signing_key=KEY))
    assert b.mode == "LOCAL-ONLY"


def test_build_broker_refuses_when_keycard_configured():
    with pytest.raises(NotImplementedError):
        build_broker(Settings(local_broker_signing_key=KEY, keycard_zone_url="https://zone.example"))
