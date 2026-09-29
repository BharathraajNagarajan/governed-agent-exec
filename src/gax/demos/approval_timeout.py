from temporalio.client import WorkflowUpdateFailedError
from temporalio.service import RPCError
from gax.demos.harness import outcomes
from gax.remediation.types import ApprovalInput
from gax.workflows import RemediationWorkflow

NAME = "approval_timeout"
TITLE = "pause_pipeline needs approval: timeout, reject and approve paths"
TIMEOUT_S = 5


async def run(d):
    wid = d.incident_id("-TIMEOUT")
    h = await d.start(wid, action="pause_pipeline", approval_timeout=TIMEOUT_S)
    d.check("reached AWAITING_APPROVAL", await d.wait_status(h, "AWAITING_APPROVAL"), "AWAITING_APPROVAL")
    status = await d.outcome(h, 60)
    hist, counters = await d.history(h), d.counters()
    approval = (d.audit(wid, h.run_id) or {}).get("detail", {}).get("approval")
    d.evidence["timeout"] = {"history": hist, "counters": counters, "approval": approval}
    d.check(f"approval timed out after {TIMEOUT_S}s", status == "APPROVAL_TIMEOUT", {"status": status, "approval": approval}, reproduce=True)
    d.check("not executed: no execute_action scheduled, pause counter 0",
            "execute_action" not in hist["scheduled"] and counters.get("pause_pipeline", 0) == 0, {"scheduled": hist["scheduled"], "counters": counters})
    try:
        late = await h.execute_update(RemediationWorkflow.approve, ApprovalInput(by="demo-operator", comment="late"))
    except (WorkflowUpdateFailedError, RPCError) as e:
        late = f"rejected: {type(e).__name__}: {getattr(e, 'cause', None) or e}"
    d.check("late approval after timeout is rejected", str(late).startswith("rejected"), late)

    wid = d.incident_id("-REJECT")
    h = await d.start(wid, action="pause_pipeline")
    await d.wait_status(h, "AWAITING_APPROVAL")
    await h.execute_update(RemediationWorkflow.reject, ApprovalInput(by="demo-operator", comment="not during business hours"))
    status = await d.outcome(h, 60)
    approval = (d.audit(wid, h.run_id) or {}).get("detail", {}).get("approval")
    d.evidence["reject"] = {"status": status, "approval": approval, "counters": d.counters()}
    d.check("reject path: REJECTED, not executed", status == "REJECTED" and d.counters().get("pause_pipeline", 0) == 0
            and not d.consumer()["paused"], {"status": status, "approval": approval})

    wid = d.incident_id("-APPROVE")
    h = await d.start(wid, action="pause_pipeline")
    await d.wait_status(h, "AWAITING_APPROVAL")
    await h.execute_update(RemediationWorkflow.approve, ApprovalInput(by="demo-operator", comment="approved"))
    status = await d.outcome(h, 60)
    rows, approval = d.ledger(wid), (d.audit(wid, h.run_id) or {}).get("detail", {}).get("approval")
    d.evidence["approve"] = {"status": status, "approval": approval, "ledger": rows, "counters": d.counters()}
    d.check("approve path: VERIFIED, paused once", status == "VERIFIED" and outcomes(rows) == ["APPLIED"]
            and d.counters().get("pause_pipeline") == 1 and d.consumer()["paused"], {"status": status, "ledger": outcomes(rows)})
