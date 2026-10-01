import uuid
import jwt
import pytest
from fastapi.testclient import TestClient
from fleet_api.app import create_app
from gax.config import Settings
from gax.credentials.keycard_broker import KeycardVerifier
from tests.doubles import KeycardZoneTestDouble, StubJWKSClientTestDouble
from tests.integration.conftest import MONGO_URI, TEST_DB

STATE = "/v1/staging/consumers/orders-consumer"
ROUTES = [
    ("restart", "urn:gax:fleet-api:restart", None),
    ("scale", "urn:gax:fleet-api:scale", {"replicas": 4}),
    ("pause", "urn:gax:fleet-api:pause", None),
    ("reset-offset", "urn:gax:fleet-api:reset-offset", None),
]


@pytest.fixture(scope="module")
def zone():
    return KeycardZoneTestDouble()


@pytest.fixture
def api(mongo, zone):
    settings = Settings(mongo_uri=MONGO_URI, gax_db=TEST_DB, credential_mode="keycard", keycard_zone_url=zone.ISSUER)
    app = create_app(settings, verifier=KeycardVerifier(zone.ISSUER, jwks_client=StubJWKSClientTestDouble(zone)))
    with TestClient(app) as client:
        assert client.post("/admin/reset").status_code == 200
        yield client


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def post(api, path, token, body=None):
    return api.post(f"{STATE}/{path}", json=body, headers={**bearer(token), "Idempotency-Key": uuid.uuid4().hex})


def test_health_reports_keycard(api):
    assert api.get("/healthz").json()["auth_mode"] == "KEYCARD"


def test_keycard_mode_requires_zone_url(mongo):
    with pytest.raises(RuntimeError, match="KEYCARD_ZONE_URL"):
        create_app(Settings(mongo_uri=MONGO_URI, gax_db=TEST_DB, credential_mode="keycard"))


def test_state_requires_state_audience(api, zone):
    assert api.get(STATE, headers=bearer(zone.token("urn:gax:fleet-api:state"))).status_code == 200
    assert api.get(STATE, headers=bearer(zone.token("urn:gax:fleet-api:restart"))).status_code == 401
    assert api.get(STATE).status_code == 401


@pytest.mark.parametrize("path,aud,body", ROUTES)
def test_mutation_requires_route_audience(api, zone, path, aud, body):
    assert post(api, path, zone.token("urn:gax:fleet-api:state"), body).status_code == 401
    other = next(a for _, a, _ in ROUTES if a != aud)
    assert post(api, path, zone.token(other), body).status_code == 401
    r = post(api, path, zone.token(aud), body)
    assert r.status_code == 200
    assert r.json()["credential_jti"]


def test_wrong_issuer_expired_and_foreign_key_rejected(api, zone):
    aud = "urn:gax:fleet-api:restart"
    assert post(api, "restart", zone.token(aud, iss="https://other.keycard.example")).status_code == 401
    assert post(api, "restart", zone.token(aud, ttl=-120)).status_code == 401
    assert post(api, "restart", zone.token(aud, key=KeycardZoneTestDouble().key)).status_code == 401
    assert post(api, "restart", "not-a-jwt").status_code == 401
    state = api.get(STATE, headers=bearer(zone.token("urn:gax:fleet-api:state"))).json()
    assert state["restart_count"] == 0


def test_no_environment_or_target_binding_in_keycard_mode(api, zone):
    token = zone.token("urn:gax:fleet-api:restart")
    r = api.post("/v1/prod/consumers/payments-consumer/restart", headers={**bearer(token), "Idempotency-Key": uuid.uuid4().hex})
    assert r.status_code == 200


class UnreachableJWKSClientTestDouble(StubJWKSClientTestDouble):
    def fetch_data(self):
        raise jwt.PyJWKClientConnectionError("jwks test double unreachable")


def test_jwks_outage_is_503(mongo, zone):
    settings = Settings(mongo_uri=MONGO_URI, gax_db=TEST_DB, credential_mode="keycard", keycard_zone_url=zone.ISSUER)
    app = create_app(settings, verifier=KeycardVerifier(zone.ISSUER, jwks_client=UnreachableJWKSClientTestDouble(zone)))
    with TestClient(app) as client:
        assert client.get(STATE, headers=bearer(zone.token("urn:gax:fleet-api:state"))).status_code == 503
