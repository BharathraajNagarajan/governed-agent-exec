import time
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowExecutionStatus
from gax.demos.harness import outcomes, pid_alive, running_worker, start_worker, worker_pids
from gax.remediation.types import ApprovalInput
from gax.workflows import RemediationWorkflow

NAME = "approval_worker_restart"
TITLE = "Real worker hard-killed during an approval wait; a new worker accepts the approval"


def update_handler(events) -> dict:
    identity, out = None, {}
    for e in events:
        if e.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_STARTED:
            identity = e.workflow_task_started_event_attributes.identity
        elif e.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_ACCEPTED:
            out = {"event_id": e.event_id, "update": e.workflow_execution_update_accepted_event_attributes.accepted_request.input.name,
                   "worker_identity": identity}
    return out


async def run(d):
    wid = d.incident_id()
    pid = running_worker()
    before = d.consumer()
    h = await d.start(wid, action="pause_pipeline")
    if not d.check("reached AWAITING_APPROVAL", await d.wait_status(h, "AWAITING_APPROVAL"), "AWAITING_APPROVAL", reproduce=True):
        return
    at_kill = {"fleet_counters": d.counters(), "launcher_pid": worker_pids()[0], "real_pid": pid}
    killed = time.monotonic()
    d.kill_worker(pid)
    d.check("real worker PID hard-killed during approval wait", not pid_alive(pid), at_kill, reproduce=True)
    running = (await h.describe()).status
    d.check("workflow still RUNNING with no worker", running == WorkflowExecutionStatus.RUNNING, running.name)
    new_pid = start_worker()
    d.evidence["worker"] = {"killed_pid": pid, "restarted_pid": new_pid, **at_kill}
    accepted = await h.execute_update(RemediationWorkflow.approve, ApprovalInput(by="demo-operator", comment="approved after worker restart"))
    d.evidence["kill_to_approval_s"] = round(time.monotonic() - killed, 1)
    status = await d.outcome(h, 120)
    events = (await h.fetch_history()).events
    handler, hist = update_handler(events), await d.history(h)
    rows, after, counters = d.ledger(wid), d.consumer(), d.counters()
    audit = d.audit(wid, h.run_id)
    detail = (audit or {}).get("detail", {})
    d.evidence.update(update=handler, history=hist, ledger=rows, counters=counters, audit_approval=detail.get("approval"))
    d.check("approve Update returned APPROVED", accepted == "APPROVED", accepted)
    d.check("Update accepted by a workflow task on the new worker", (handler.get("worker_identity") or "").startswith(f"{new_pid}@"),
            {**handler, "new_pid": new_pid, "killed_pid": pid})
    d.check("nothing re-proposed or re-evaluated", hist["scheduled"].count("propose_action") == 1 and hist["scheduled"].count("evaluate_policy") == 1,
            hist["scheduled"])
    d.check("pause applied once: ledger [APPLIED], pause_pipeline=1, paused", outcomes(rows) == ["APPLIED"] and counters.get("pause_pipeline") == 1
            and after["paused"] and not before["paused"], {"ledger": outcomes(rows), "counters": counters, "paused": [before["paused"], after["paused"]]})
    d.check("audit record present with approval", audit is not None and detail.get("status") == "VERIFIED"
            and (detail.get("approval") or {}).get("decision") == "APPROVED", {"audit_id": (audit or {}).get("_id"), "approval": detail.get("approval")})
    d.check("restarted worker logged its real pid", f"worker pid={new_pid}" in d.worker_log(), new_pid)
    d.check("status VERIFIED", status == "VERIFIED", status)
