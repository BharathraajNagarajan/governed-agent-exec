import asyncio
from datetime import timedelta
from typing import Optional
from temporalio import workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from gax.models import PolicyDecision
    from gax.remediation.activities import RemediationActivities
    from gax.remediation.types import (FAILURE_STATUSES, ActionInput, ApprovalInput, AuditInput, ProposeInput, RemediationInput,
                                       RemediationResult, RetrieveInput, VerifyInput)
    from gax.retrieval.chunking import batch_by_tokens
    from gax.retrieval.ingest import EmbedBatchInput, IngestActivities, IngestInput, IngestReport, PlanInput, StaleInput

TASK_QUEUE = "gax"
INCIDENT_ID_REUSE = WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY
QUICK = dict(start_to_close_timeout=timedelta(seconds=30),
             retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1), maximum_interval=timedelta(seconds=10), maximum_attempts=5))
RETRIEVE = dict(start_to_close_timeout=timedelta(seconds=150),
                retry_policy=RetryPolicy(initial_interval=timedelta(seconds=5), maximum_attempts=2,
                                         non_retryable_error_types=["VoyageAuthError", "VoyageRequestError", "RetrievalNotReady"]))
PROPOSE = dict(start_to_close_timeout=timedelta(seconds=180),
               retry_policy=RetryPolicy(initial_interval=timedelta(seconds=2), backoff_coefficient=2.0, maximum_interval=timedelta(seconds=30),
                                        maximum_attempts=3, non_retryable_error_types=["MalformedProposal", "ProposerRejected"]))
POLICY = dict(start_to_close_timeout=timedelta(seconds=10), retry_policy=RetryPolicy(maximum_attempts=3))
FLEET = dict(start_to_close_timeout=timedelta(seconds=20),
             retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1), backoff_coefficient=2.0, maximum_interval=timedelta(seconds=10),
                                      maximum_attempts=6, non_retryable_error_types=["CredentialDenied", "FleetRejected"]))
AUDIT = dict(start_to_close_timeout=timedelta(seconds=20),
             retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1), maximum_interval=timedelta(seconds=30), maximum_attempts=20))


@workflow.defn
class IngestWorkflow:
    @workflow.run
    async def run(self, inp: IngestInput) -> IngestReport:
        chunks = await workflow.execute_activity_method(IngestActivities.load_corpus, inp.corpus_dir, **QUICK)
        index = await workflow.execute_activity_method(
            IngestActivities.ensure_index, start_to_close_timeout=timedelta(seconds=240),
            retry_policy=RetryPolicy(initial_interval=timedelta(seconds=2), maximum_attempts=3))
        plan = await workflow.execute_activity_method(IngestActivities.plan_ingest, PlanInput(version=inp.version, chunks=chunks), **QUICK)
        batches = batch_by_tokens(plan.to_embed, inp.batch_tokens)
        embedded = 0
        for batch in batches:
            embedded += await workflow.execute_activity_method(
                IngestActivities.embed_batch, EmbedBatchInput(version=inp.version, chunks=batch),
                start_to_close_timeout=timedelta(minutes=5), heartbeat_timeout=timedelta(seconds=90),
                retry_policy=RetryPolicy(initial_interval=timedelta(seconds=20), backoff_coefficient=2.0,
                                         maximum_interval=timedelta(seconds=60), maximum_attempts=4,
                                         non_retryable_error_types=["VoyageAuthError", "VoyageRequestError"]))
        deleted = await workflow.execute_activity_method(IngestActivities.delete_stale, StaleInput(version=inp.version, chunk_ids=plan.stale), **QUICK)
        active = await workflow.execute_activity_method(IngestActivities.activate, inp.version, **QUICK)
        return IngestReport(version=inp.version, model=active["model"], total=len(chunks), embedded=embedded, skipped=plan.unchanged,
                            deleted=deleted, batches=len(batches), index=index)


def failure(e: ActivityError) -> dict:
    cause = e.cause
    return {"type": getattr(cause, "type", None) or type(cause).__name__, "message": str(cause),
            "details": [d for d in (getattr(cause, "details", None) or [])]}


@workflow.defn
class RemediationWorkflow:
    def __init__(self):
        self.status = "STARTED"
        self.approval: Optional[dict] = None
        self.record: dict = {}

    @workflow.run
    async def run(self, inp: RemediationInput) -> RemediationResult:
        info = workflow.info()
        self.record = {"incident": inp.incident.model_dump(mode="json"), "started_at": workflow.now().isoformat()}
        status = await self._remediate(inp)
        self.status = status
        self.record["finished_at"] = workflow.now().isoformat()
        await workflow.execute_activity_method(RemediationActivities.record_audit, AuditInput(
            workflow_id=info.workflow_id, run_id=info.run_id, incident_id=inp.incident.incident_id, status=status, record=self.record), **AUDIT)
        if status in FAILURE_STATUSES:
            raise ApplicationError(f"incident {inp.incident.incident_id} ended {status}", status, type=status, non_retryable=True)
        return RemediationResult(incident_id=inp.incident.incident_id, status=status)

    async def _remediate(self, inp: RemediationInput) -> str:
        wid, rec, incident = workflow.info().workflow_id, self.record, inp.incident
        self.status = "RETRIEVING"
        hits = []
        if not inp.retrieve:
            rec["retrieval"] = "skipped"
        else:
            try:
                hits = await workflow.execute_activity_method(RemediationActivities.retrieve_context,
                                                              RetrieveInput(query=incident.summary, k=inp.k, rerank=inp.rerank), **RETRIEVE)
            except ActivityError as e:
                rec["retrieval_error"] = failure(e)
        rec["context"] = [{"chunk_id": h.chunk_id, "vector_score": h.vector_score, "rerank_score": h.rerank_score} for h in hits]

        self.status = "PROPOSING"
        try:
            proposed = await workflow.execute_activity_method(
                RemediationActivities.propose_action, ProposeInput(incident=incident, context=hits, proposal_json=inp.proposal_json), **PROPOSE)
        except ActivityError as e:
            rec["proposal_error"] = failure(e)
            return "NEEDS_HUMAN" if rec["proposal_error"]["type"] == "MalformedProposal" else "FAILED"
        proposal = proposed.proposal
        rec["proposal"] = proposal.model_dump(mode="json")
        rec["proposer"] = proposed.model_dump(mode="json", exclude={"proposal"})

        self.status = "EVALUATING_POLICY"
        decision: PolicyDecision = await workflow.execute_activity_method(RemediationActivities.evaluate_policy, proposal, **POLICY)
        rec["decision"] = decision.model_dump(mode="json")
        rec["policy_version"] = decision.policy_version
        if decision.decision == "DENY":
            return "DENIED"
        if decision.decision == "REQUIRE_APPROVAL":
            self.status = "AWAITING_APPROVAL"
            try:
                await workflow.wait_condition(lambda: self.approval is not None, timeout=timedelta(seconds=inp.approval_timeout_seconds))
            except asyncio.TimeoutError:
                rec["approval"] = {"decision": "TIMEOUT", "timeout_seconds": inp.approval_timeout_seconds}
                return "APPROVAL_TIMEOUT"
            rec["approval"] = self.approval
            if self.approval["decision"] == "REJECTED":
                return "REJECTED"

        action = ActionInput(workflow_id=wid, proposal=proposal)
        self.status = "SNAPSHOTTING"
        try:
            before = await workflow.execute_activity_method(RemediationActivities.snapshot_state, action, **FLEET)
        except ActivityError as e:
            return self._fleet_failure("snapshot_error", e)
        rec["snapshot"] = before

        self.status = "EXECUTING"
        try:
            result = await workflow.execute_activity_method(RemediationActivities.execute_action, action, **FLEET)
        except ActivityError as e:
            return self._fleet_failure("execution_error", e)
        rec["execution"] = result.model_dump(mode="json")

        self.status = "VERIFYING"
        try:
            verification = await workflow.execute_activity_method(
                RemediationActivities.verify_outcome, VerifyInput(workflow_id=wid, proposal=proposal, before=before), **FLEET)
        except ActivityError as e:
            rec["verification_error"] = failure(e)
            return "VERIFY_FAILED"
        rec["verification"] = verification.model_dump(mode="json")
        return "VERIFIED" if verification.ok else "VERIFY_FAILED"

    def _fleet_failure(self, field: str, e: ActivityError) -> str:
        self.record[field] = failure(e)
        return "CREDENTIAL_DENIED" if self.record[field]["type"] == "CredentialDenied" else "FAILED"

    def _decide(self, decision: str, inp: ApprovalInput) -> str:
        self.approval = {"decision": decision, "by": inp.by, "comment": inp.comment, "at": workflow.now().isoformat()}
        return decision

    def _validate(self) -> None:
        if self.status != "AWAITING_APPROVAL" or self.approval is not None:
            raise ValueError(f"not awaiting approval (status={self.status})")

    @workflow.update
    def approve(self, inp: ApprovalInput) -> str:
        return self._decide("APPROVED", inp)

    @approve.validator
    def validate_approve(self, inp: ApprovalInput) -> None:
        self._validate()

    @workflow.update
    def reject(self, inp: ApprovalInput) -> str:
        return self._decide("REJECTED", inp)

    @reject.validator
    def validate_reject(self, inp: ApprovalInput) -> None:
        self._validate()

    @workflow.query
    def current_status(self) -> dict:
        return {"status": self.status, "proposal": self.record.get("proposal"), "decision": self.record.get("decision"),
                "approval": self.approval}
