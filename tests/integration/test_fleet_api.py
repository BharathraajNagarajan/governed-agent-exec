import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from gax.credentials import CredentialDenied, CredentialUnavailable, LocalOnlyCredentialBroker
from gax.credentials.local_only_broker import GRANTS_COLLECTION, MongoGrantStore
from tests.integration.conftest import TEST_KEY

RESTART = "/v1/staging/consumers/orders-consumer/restart"
STATE = "/v1/staging/consumers/orders-consumer"


def key():
    return uuid.uuid4().hex


def state(fleet, auth):
    r = fleet.get(STATE, headers=auth("get_state"))
    assert r.status_code == 200
    return r.json()


def test_health_reports_local_only(fleet):
    assert fleet.get("/healthz").json()["auth_mode"] == "LOCAL-ONLY"


def test_auth_required(fleet, auth):
    idem = {"Idempotency-Key": key()}
    assert fleet.post(RESTART, headers=idem).status_code == 401
    assert fleet.post(RESTART, headers={**idem, "Authorization": "Bearer not-a-jwt"}).status_code == 401
    assert fleet.get(STATE).status_code == 401
    forged = LocalOnlyCredentialBroker("some-other-local-only-signing-key-0123456789").issue("restart_consumer", "orders-consumer", "staging", "INC-1", 1)
    assert fleet.post(RESTART, headers={**idem, "Authorization": f"Bearer {forged.token}"}).status_code == 401
    past = datetime.now(timezone.utc) - timedelta(minutes=10)
    expired = LocalOnlyCredentialBroker(TEST_KEY, clock=lambda: past).issue("restart_consumer", "orders-consumer", "staging", "INC-1", 1)
    assert fleet.post(RESTART, headers={**idem, "Authorization": f"Bearer {expired.token}"}).status_code == 401
    assert fleet.post(RESTART, headers={**idem, **auth("scale_consumer")}).status_code == 403
    assert fleet.post(RESTART, headers={**idem, **auth("restart_consumer", target="payments-consumer")}).status_code == 403
    assert fleet.post(RESTART, headers={**idem, **auth("restart_consumer", environment="prod")}).status_code == 403
    assert state(fleet, auth)["restart_count"] == 0
    assert fleet.post(RESTART, headers={**idem, **auth("restart_consumer")}).status_code == 200
    assert state(fleet, auth)["restart_count"] == 1


def test_idempotency_key_required(fleet, auth):
    r = fleet.post(RESTART, headers=auth("restart_consumer"))
    assert r.status_code == 400
    assert state(fleet, auth)["restart_count"] == 0


def test_all_actions_apply(fleet, auth):
    base = "/v1/staging/consumers/orders-consumer"
    assert fleet.post(f"{base}/scale", json={"replicas": 7}, headers={"Idempotency-Key": key(), **auth("scale_consumer")}).json()["state"]["replicas"] == 7
    assert fleet.post(f"{base}/pause", headers={"Idempotency-Key": key(), **auth("pause_pipeline")}).json()["state"]["paused"] is True
    assert fleet.post(f"{base}/reset-offset", headers={"Idempotency-Key": key(), **auth("reset_consumer_offset")}).json()["state"]["offset"] == 0
    s = state(fleet, auth)
    assert (s["replicas"], s["paused"], s["offset"], s["restart_count"]) == (7, True, 0, 0)


def test_unknown_consumer_is_404_and_not_stored(fleet, auth):
    h = {"Idempotency-Key": key(), **auth("restart_consumer", target="ghost")}
    assert fleet.post("/v1/staging/consumers/ghost/restart", headers=h).status_code == 404
    assert fleet.post("/v1/staging/consumers/ghost/restart", headers=h).status_code == 404


def test_idempotency_dedupe(fleet, auth):
    h = {"Idempotency-Key": key(), **auth("restart_consumer")}
    first = fleet.post(RESTART, headers=h)
    second = fleet.post(RESTART, headers={**h, **auth("restart_consumer", attempt=2)})
    assert first.status_code == second.status_code == 200
    assert "Idempotent-Replayed" not in first.headers
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json() == first.json()
    assert state(fleet, auth)["restart_count"] == 1
    assert fleet.post(RESTART, headers={"Idempotency-Key": key(), **auth("restart_consumer")}).status_code == 200
    assert state(fleet, auth)["restart_count"] == 2


def test_idempotency_key_reuse_with_different_payload_rejected(fleet, auth):
    url = "/v1/staging/consumers/orders-consumer/scale"
    h = {"Idempotency-Key": key(), **auth("scale_consumer")}
    assert fleet.post(url, json={"replicas": 3}, headers=h).status_code == 200
    assert fleet.post(url, json={"replicas": 5}, headers=h).status_code == 422
    assert state(fleet, auth)["replicas"] == 3


def test_concurrent_duplicates_apply_once(fleet, auth):
    h = {"Idempotency-Key": key(), **auth("restart_consumer")}
    with ThreadPoolExecutor(8) as pool:
        responses = list(pool.map(lambda _: fleet.post(RESTART, headers=h), range(8)))
    assert [r.status_code for r in responses] == [200] * 8
    assert len({r.json()["applied_at"] for r in responses}) == 1
    assert state(fleet, auth)["restart_count"] == 1


def test_fail_next_n(fleet, auth):
    fleet.post("/admin/faults", json={"action": "restart_consumer", "fail_next": 2})
    h = {"Idempotency-Key": key(), **auth("restart_consumer")}
    assert [fleet.post(RESTART, headers=h).status_code for _ in range(3)] == [500, 500, 200]
    assert state(fleet, auth)["restart_count"] == 1
    assert fleet.get("/admin/counters").json()["restart_consumer"] == 3


def test_fail_next_scoped_to_action(fleet, auth):
    fleet.post("/admin/faults", json={"action": "pause_pipeline", "fail_next": 1})
    assert fleet.post(RESTART, headers={"Idempotency-Key": key(), **auth("restart_consumer")}).status_code == 200


def test_latency(fleet, auth):
    fleet.post("/admin/faults", json={"latency_ms": 600})
    t = time.perf_counter()
    assert fleet.post(RESTART, headers={"Idempotency-Key": key(), **auth("restart_consumer")}).status_code == 200
    assert time.perf_counter() - t >= 0.6


def test_commit_then_drop_applies_exactly_once(fleet, auth):
    fleet.post("/admin/faults", json={"action": "restart_consumer", "drop_next": 1})
    h = {"Idempotency-Key": key(), **auth("restart_consumer")}
    with pytest.raises(httpx.RemoteProtocolError, match="without sending a response"):
        fleet.post(RESTART, headers=h)
    assert state(fleet, auth)["restart_count"] == 1
    retry = fleet.post(RESTART, headers=h)
    assert retry.status_code == 200
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert retry.json()["state"]["restart_count"] == 1
    assert state(fleet, auth)["restart_count"] == 1
    assert fleet.get("/admin/counters").json()["restart_consumer"] == 2


def test_counters_per_action(fleet, auth):
    assert fleet.get("/admin/counters").json() == {}
    reset = "/v1/prod/consumers/orders-consumer/reset-offset"
    fleet.post(reset, headers={"Idempotency-Key": key(), **auth("reset_consumer_offset", environment="prod")})
    fleet.post(reset, headers={"Idempotency-Key": key()})
    fleet.post(RESTART, headers={"Idempotency-Key": key(), **auth("restart_consumer")})
    counters = fleet.get("/admin/counters").json()
    assert counters == {"reset_consumer_offset": 2, "restart_consumer": 1}
    assert counters.get("pause_pipeline", 0) == 0
    fleet.delete("/admin/counters")
    assert fleet.get("/admin/counters").json() == {}


def test_mongo_grant_store_shared_across_broker_instances(mongo):
    coll = mongo[GRANTS_COLLECTION]
    coll.delete_many({})
    demo = LocalOnlyCredentialBroker(TEST_KEY, store=MongoGrantStore(coll))
    worker = LocalOnlyCredentialBroker(TEST_KEY, store=MongoGrantStore(coll))
    args = ("restart_consumer", "orders-consumer", "staging", "INC-1", 1)
    demo.revoke()
    with pytest.raises(CredentialDenied):
        worker.issue(*args)
    demo.restore()
    demo.set_transient(count=1)
    with pytest.raises(CredentialUnavailable):
        worker.issue(*args)
    assert worker.issue(*args).token
    assert coll.count_documents({}) == 0
