import pytest
from pymongo.errors import ServerSelectionTimeoutError
from temporalio.api.enums.v1 import EventType, RetryState
from temporalio.api.history.v1 import HistoryEvent
from temporalio.exceptions import ApplicationError
from gax.demos import DEMOS
from gax.demos.harness import (FAIL, NOT_REPRODUCED, PASS, final_attempt, outcomes, proposal_json, secret_hits, summarize_history,
                               summary_table, verdict)
from gax.models import parse_proposal
from gax.remediation.activities import mongo_unavailable


def check(ok, reproduce=False):
    return {"name": "c", "ok": ok, "observed": None, "reproduce": reproduce}


@pytest.mark.parametrize("checks, expected", [
    ([], FAIL),
    ([check(True, True), check(True)], PASS),
    ([check(True, True), check(False)], FAIL),
    ([check(False, True), check(False)], NOT_REPRODUCED),
    ([check(True), check(False, True)], NOT_REPRODUCED),
])
def test_verdict(checks, expected):
    assert verdict(checks) == expected


def scheduled(event_id, name):
    e = HistoryEvent(event_id=event_id, event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED)
    e.activity_task_scheduled_event_attributes.activity_type.name = name
    return e


def started(scheduled_id, attempt, last_failure="", failure_type=""):
    e = HistoryEvent(event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED)
    a = e.activity_task_started_event_attributes
    a.scheduled_event_id, a.attempt = scheduled_id, attempt
    a.last_failure.message = last_failure
    a.last_failure.application_failure_info.type = failure_type
    return e


def test_summarize_history():
    completed = HistoryEvent(event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED)
    completed.activity_task_completed_event_attributes.scheduled_event_id = 5
    failed = HistoryEvent(event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED)
    fa = failed.activity_task_failed_event_attributes
    fa.scheduled_event_id, fa.retry_state = 7, RetryState.RETRY_STATE_NON_RETRYABLE_FAILURE
    fa.failure.message = "denied"
    fa.failure.application_failure_info.type, fa.failure.application_failure_info.non_retryable = "CredentialDenied", True
    timed_out = HistoryEvent(event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT)
    timed_out.activity_task_timed_out_event_attributes.scheduled_event_id = 9
    timed_out.activity_task_timed_out_event_attributes.failure.message = "activity StartToClose timeout"
    events = [scheduled(5, "execute_action"), started(5, 3, "fleet-api HTTP 500", "FleetUnavailable"), completed,
              scheduled(7, "snapshot_state"), started(7, 1), failed, scheduled(9, "record_audit"), timed_out]
    h = summarize_history(events)
    assert h["scheduled"] == ["execute_action", "snapshot_state", "record_audit"]
    assert h["attempts"] == [
        {"activity": "execute_action", "attempt": 3, "last_failure": "fleet-api HTTP 500", "last_failure_type": "FleetUnavailable"},
        {"activity": "snapshot_state", "attempt": 1, "last_failure": None, "last_failure_type": None}]
    assert h["completed"] == ["execute_action"]
    assert h["failures"] == [
        {"activity": "snapshot_state", "type": "CredentialDenied", "message": "denied", "non_retryable": True,
         "retry_state": "RETRY_STATE_NON_RETRYABLE_FAILURE"},
        {"activity": "record_audit", "type": None, "message": "activity StartToClose timeout", "non_retryable": False,
         "retry_state": "RETRY_STATE_UNSPECIFIED"}]
    assert final_attempt(h, "execute_action")["attempt"] == 3
    assert final_attempt(h, "verify_outcome") is None


def test_secret_hits():
    assert secret_hits([b"clean", b"x eyJhbGciOiJIUzI1NiJ9 Bearer y"]) == 2
    assert secret_hits([]) == 0


def test_outcomes_fall_back_to_status_for_unfinished_attempts():
    assert outcomes([{"status": "PENDING"}, {"status": "APPLIED", "outcome": "REPLAYED"}]) == ["PENDING", "REPLAYED"]


def test_proposal_json_is_a_valid_proposal():
    p = parse_proposal(proposal_json("scale_consumer", "prod", "payments-consumer", 4))
    assert (p.action, p.environment, p.target, p.params.replicas) == ("scale_consumer", "prod", "payments-consumer", 4)


def test_summary_table():
    table = summary_table([{"name": "fleet_5xx", "status": PASS, "duration_s": 6.94, "checks": [check(True), check(False)]}])
    assert table.splitlines()[1].split() == ["fleet_5xx", "PASS", "6.9", "1/2"]


def test_every_failure_matrix_row_has_a_demo():
    assert list(DEMOS) == ["worker_kill", "fleet_5xx", "response_lost", "credential_denied", "credential_transient", "policy_deny",
                           "approval_timeout", "approval_worker_restart", "llm_malformed", "voyage_429", "mongo_down",
                           "duplicate_start"]
    assert all(m.TITLE and callable(m.run) for m in DEMOS.values())


def test_mongo_errors_become_compact_retryable_failures():
    @mongo_unavailable
    def boom():
        raise ServerSelectionTimeoutError("localhost:27017: [WinError 10061] " + "topology " * 200)

    with pytest.raises(ApplicationError) as e:
        boom()
    assert (e.value.type, e.value.non_retryable, e.value.message) == ("MongoUnavailable", False, "mongo unavailable: ServerSelectionTimeoutError")
    assert e.value.__suppress_context__ and e.value.__cause__ is None
    assert mongo_unavailable(lambda x: x + 1)(1) == 2
