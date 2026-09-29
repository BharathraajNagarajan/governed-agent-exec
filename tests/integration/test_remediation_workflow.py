import asyncio
import base64
import json
import uuid
import httpx
import pytest
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowFailureError, WorkflowUpdateFailedError
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError
from gax.credentials import LocalOnlyCredentialBroker
from gax.credentials.local_only_broker import GRANTS_COLLECTION, MongoGrantStore
from gax.demos.harness import final_attempt, summarize_history
from gax.models import ActionParams, ActionProposal, Incident
from gax.remediation.activities import AUDIT, INCIDENTS, LEDGER, RemediationActivities
from gax.remediation.types import ApprovalInput, RemediationInput
from gax.workflows import INCIDENT_ID_REUSE, RemediationWorkflow
from tests.doubles import FleetApiTestDouble, ProposerTestDouble, SearchTestDouble
from tests.integration.conftest import TEST_KEY
from tests.temporal_env import run_with_worker

JWT_PREFIX = "eyJhbGciOi"
UPDATE_REJECTED = (WorkflowUpdateFailedError, RPCError)


def proposal(action="restart_consumer", environment="staging", replicas=None) -> ActionProposal:
    return ActionProposal(action=action, target="orders-consumer", environment=environment, params=ActionParams(replicas=replicas),
                          justification="test double proposal", cited_chunk_ids=["consumer-lag-no-active-members#remediation"])


def remediation_input(environment="staging", injected=None, timeout=600) -> RemediationInput:
    iid = f"INC-{uuid.uuid4().hex[:8]}"
    incident = Incident(incident_id=iid, environment=environment, target="orders-consumer", summary="orders-consumer has 0 active members")
    return RemediationInput(incident=incident, proposal_json=injected, approval_timeout_seconds=timeout)


class Harness:
    def __init__(self, mongo, proposer=None):
        for name in (LEDGER, AUDIT, INCIDENTS, GRANTS_COLLECTION):
            mongo[name].delete_many({})
        self.db = mongo
        self.broker = LocalOnlyCredentialBroker(TEST_KEY, store=MongoGrantStore(mongo[GRANTS_COLLECTION]))
        self.fleet = FleetApiTestDouble(TEST_KEY)
        self.proposer = proposer or ProposerTestDouble(proposal())
        self.search = SearchTestDouble()
        http = httpx.Client(base_url="http://fleet.test", transport=self.fleet.transport())
        self.acts = RemediationActivities(mongo, self.broker, http, self.proposer, self.search)

    def run(self, body):
        return run_with_worker(body, [RemediationWorkflow], self.acts.all())

    def ledger(self, workflow_id):
        return list(self.db[LEDGER].find({"workflow_id": workflow_id}).sort([("run_id", 1), ("attempt", 1)]))

    def audit(self, workflow_id):
        return list(self.db[AUDIT].find({"workflow_id": workflow_id}))

    def orders(self, environment="staging"):
        return self.fleet.state[(environment, "orders-consumer")]


async def start(env, queue, inp):
    return await env.client.start_workflow(RemediationWorkflow.run, inp, id=inp.incident.incident_id, task_queue=queue,
                                           id_reuse_policy=INCIDENT_ID_REUSE)


async def outcome(handle) -> str:
    try:
        return (await handle.result()).status
    except WorkflowFailureError as e:
        return e.cause.type


async def wait_status(handle, status, timeout=20):
    for _ in range(timeout * 10):
        if (await handle.query(RemediationWorkflow.current_status))["status"] == status:
            return
        await asyncio.sleep(0.1)
    raise TimeoutError(status)


async def decoded_history(handle) -> str:
    raw = (await handle.fetch_history()).to_json()
    parts = [raw]

    def walk(node):
        if isinstance(node, dict):
            if "data" in node and "metadata" in node and isinstance(node["data"], str):
                parts.append(base64.b64decode(node["data"]).decode("utf-8", "replace"))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(json.loads(raw))
    return "\n".join(parts)


async def scheduled_activities(handle) -> list[str]:
    history = await handle.fetch_history()
    return [e.activity_task_scheduled_event_attributes.activity_type.name for e in history.events
            if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED]


def test_staging_restart_verified_once_and_no_credential_in_history(mongo):
    h = Harness(mongo)
    inp = remediation_input()

    async def body(env, queue):
        handle = await start(env, queue, inp)
        return await outcome(handle), await decoded_history(handle), await scheduled_activities(handle)

    status, history, scheduled = h.run(body)
    wid = inp.incident.incident_id
    assert status == "VERIFIED"
    assert scheduled == ["retrieve_context", "propose_action", "evaluate_policy", "snapshot_state", "execute_action", "verify_outcome", "record_audit"]
    assert h.orders()["restart_count"] == 1
    assert h.fleet.counters["restart_consumer"] == 1
    rows = h.ledger(wid)
    assert [(r["attempt"], r["status"], r["outcome"], r["idempotency_key"]) for r in rows] == [(1, "APPLIED", "APPLIED", f"{wid}:execute_action")]
    assert rows[0]["credential_mode"] == "LOCAL-ONLY"
    [audit] = h.audit(wid)
    d = audit["detail"]
    assert d["status"] == "VERIFIED"
    assert d["proposal"]["action"] == "restart_consumer"
    assert (d["decision"]["decision"], d["policy_version"]) == ("ALLOW", "policy-v1")
    assert d["execution"]["replayed"] is False
    assert (d["verification"]["ok"], d["verification"]["expected"], d["verification"]["observed"]) == (True, 1, 1)
    assert d["credential_mode"] == "LOCAL-ONLY"
    assert d["context"][0]["chunk_id"] == "consumer-lag-no-active-members#remediation"
    assert mongo[INCIDENTS].find_one({"_id": wid})["status"] == "VERIFIED"
    assert len(h.fleet.tokens_seen) == 3
    stored = json.dumps(rows, default=str) + json.dumps(audit, default=str)
    for token in h.fleet.tokens_seen:
        assert token and token not in history and token not in stored
    assert JWT_PREFIX not in history and "Bearer" not in history
    assert JWT_PREFIX not in stored


def test_policy_deny_schedules_no_execute_action(mongo):
    h = Harness(mongo)
    inp = remediation_input("prod", injected=proposal("reset_consumer_offset", "prod").model_dump_json())

    async def body(env, queue):
        handle = await start(env, queue, inp)
        return await outcome(handle), await scheduled_activities(handle)

    status, scheduled = h.run(body)
    assert status == "DENIED"
    assert "execute_action" not in scheduled and "snapshot_state" not in scheduled
    assert sum(h.fleet.counters.values()) == 0
    assert h.proposer.calls == 0
    d = h.audit(inp.incident.incident_id)[0]["detail"]
    assert (d["decision"]["decision"], d["proposer"]["source"]) == ("DENY", "file")


def test_approval_approve(mongo):
    h = Harness(mongo, ProposerTestDouble(proposal("pause_pipeline")))
    inp = remediation_input()

    async def body(env, queue):
        handle = await start(env, queue, inp)
        await wait_status(handle, "AWAITING_APPROVAL")
        assert h.fleet.counters["pause_pipeline"] == 0
        assert await handle.execute_update(RemediationWorkflow.approve, ApprovalInput(by="alice", comment="ok")) == "APPROVED"
        status = await outcome(handle)
        with pytest.raises(UPDATE_REJECTED):
            await handle.execute_update(RemediationWorkflow.approve, ApprovalInput(by="bob"))
        return status

    assert h.run(body) == "VERIFIED"
    assert h.orders()["paused"] is True
    d = h.audit(inp.incident.incident_id)[0]["detail"]
    assert (d["approval"]["decision"], d["approval"]["by"]) == ("APPROVED", "alice")


def test_approval_reject(mongo):
    h = Harness(mongo, ProposerTestDouble(proposal("pause_pipeline")))
    inp = remediation_input()

    async def body(env, queue):
        handle = await start(env, queue, inp)
        await wait_status(handle, "AWAITING_APPROVAL")
        await handle.execute_update(RemediationWorkflow.reject, ApprovalInput(by="alice", comment="not now"))
        return await outcome(handle), await scheduled_activities(handle)

    status, scheduled = h.run(body)
    assert status == "REJECTED"
    assert "execute_action" not in scheduled
    assert h.fleet.counters["pause_pipeline"] == 0


def test_approval_timeout(mongo):
    h = Harness(mongo, ProposerTestDouble(proposal("pause_pipeline")))
    inp = remediation_input(timeout=3)

    async def body(env, queue):
        handle = await start(env, queue, inp)
        return await outcome(handle), await scheduled_activities(handle)

    status, scheduled = h.run(body)
    assert status == "APPROVAL_TIMEOUT"
    assert "execute_action" not in scheduled
    assert h.orders()["paused"] is False
    assert h.audit(inp.incident.incident_id)[0]["detail"]["approval"]["decision"] == "TIMEOUT"


def test_update_rejected_when_not_awaiting_approval(mongo):
    h = Harness(mongo)
    inp = remediation_input()

    async def body(env, queue):
        handle = await start(env, queue, inp)
        await outcome(handle)
        with pytest.raises(UPDATE_REJECTED):
            await handle.execute_update(RemediationWorkflow.approve, ApprovalInput(by="alice"))

    h.run(body)


def test_malformed_proposal_needs_human_after_three_bounded_attempts(mongo):
    h = Harness(mongo, ProposerTestDouble("not json", json.dumps({**proposal().model_dump(), "action": "drop_topic"}), "{}"))
    inp = remediation_input()

    async def body(env, queue):
        handle = await start(env, queue, inp)
        return await outcome(handle), await scheduled_activities(handle)

    status, scheduled = h.run(body)
    assert status == "NEEDS_HUMAN"
    assert h.proposer.calls == 3
    assert scheduled == ["retrieve_context", "propose_action", "record_audit"]
    d = h.audit(inp.incident.incident_id)[0]["detail"]
    assert d["proposal_error"]["type"] == "MalformedProposal"
    assert len(d["proposal_error"]["details"][0]) == 3


def test_injected_malformed_proposal_needs_human(mongo):
    h = Harness(mongo)
    inp = remediation_input(injected='{"action": "restart_consumer"}')

    async def body(env, queue):
        return await outcome(await start(env, queue, inp))

    assert h.run(body) == "NEEDS_HUMAN"
    assert h.proposer.calls == 0
    assert sum(h.fleet.counters.values()) == 0


def test_credential_denied_is_non_retryable_and_restore_makes_rerun_succeed(mongo):
    h = Harness(mongo)
    inp = remediation_input()
    wid = inp.incident.incident_id
    h.broker.revoke("restart_consumer")

    async def body(env, queue):
        first = await outcome(await start(env, queue, inp))
        h.broker.restore("restart_consumer")
        second = await outcome(await start(env, queue, inp))
        return first, second

    first, second = h.run(body)
    assert (first, second) == ("CREDENTIAL_DENIED", "VERIFIED")
    rows = h.ledger(wid)
    runs = sorted({r["run_id"] for r in rows}, key=lambda run: min(r["at"] for r in rows if r["run_id"] == run))
    by_run = {run: [(r["attempt"], r["outcome"]) for r in rows if r["run_id"] == run] for run in runs}
    assert list(by_run.values()) == [[(1, "CREDENTIAL_DENIED")], [(1, "APPLIED")]]
    assert h.fleet.counters["restart_consumer"] == 1
    assert h.orders()["restart_count"] == 1
    assert [a["detail"]["status"] for a in sorted(h.audit(wid), key=lambda a: a["at"])] == ["CREDENTIAL_DENIED", "VERIFIED"]


def test_transient_credential_error_retries_then_succeeds(mongo):
    h = Harness(mongo)
    inp = remediation_input()
    h.broker.set_transient("restart_consumer", count=2)

    async def body(env, queue):
        return await outcome(await start(env, queue, inp))

    assert h.run(body) == "VERIFIED"
    rows = h.ledger(inp.incident.incident_id)
    assert [(r["attempt"], r["status"], r["outcome"]) for r in rows] == [
        (1, "FAILED", "CREDENTIAL_UNAVAILABLE"), (2, "FAILED", "CREDENTIAL_UNAVAILABLE"), (3, "APPLIED", "APPLIED")]
    assert h.orders()["restart_count"] == 1


def test_fleet_5xx_and_lost_response_apply_exactly_once(mongo):
    h = Harness(mongo)
    inp = remediation_input()
    h.fleet.fail_next = 1
    h.fleet.drop_next = 1

    async def body(env, queue):
        handle = await start(env, queue, inp)
        return await outcome(handle), summarize_history((await handle.fetch_history()).events)

    status, history = h.run(body)
    assert status == "VERIFIED"
    assert final_attempt(history, "execute_action")["last_failure"] == "fleet-api transport error: RemoteProtocolError"
    rows = h.ledger(inp.incident.incident_id)
    assert [(r["attempt"], r["outcome"]) for r in rows] == [(1, "HTTP_500"), (2, "TRANSPORT_ERROR"), (3, "REPLAYED")]
    assert h.orders()["restart_count"] == 1
    assert h.audit(inp.incident.incident_id)[0]["detail"]["verification"]["observed"] == 1


def test_duplicate_start_rejected(mongo):
    h = Harness(mongo, ProposerTestDouble(proposal("pause_pipeline")))
    inp = remediation_input()

    async def body(env, queue):
        handle = await start(env, queue, inp)
        with pytest.raises(WorkflowAlreadyStartedError):
            await start(env, queue, inp)
        await wait_status(handle, "AWAITING_APPROVAL")
        await handle.execute_update(RemediationWorkflow.approve, ApprovalInput(by="alice"))
        status = await outcome(handle)
        with pytest.raises(WorkflowAlreadyStartedError):
            await start(env, queue, inp)
        return status

    assert h.run(body) == "VERIFIED"
    assert h.fleet.counters["pause_pipeline"] == 1
