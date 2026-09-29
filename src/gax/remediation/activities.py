import json
from typing import Callable, Optional
import anthropic
import httpx
from pymongo.database import Database
from temporalio import activity
from temporalio.exceptions import ApplicationError
from gax.credentials import CredentialBroker, CredentialDenied, CredentialUnavailable
from gax.llm import ProposalResult
from gax.models import ActionProposal, AuditEvent, Incident, LedgerEntry, MalformedProposal, PolicyDecision, parse_proposal, utcnow
from gax.policy import evaluate
from gax.remediation.types import (ActionInput, AuditInput, ExecuteResult, ProposeInput, ProposeResult, RetrieveInput, VerifyInput,
                                   VerifyResult)
from gax.retrieval.search import SearchHit

LEDGER = "action_ledger"
AUDIT = "audit_events"
INCIDENTS = "incidents"
MAX_PROPOSAL_ATTEMPTS = 3
STEP = "execute_action"
PATHS = {
    "restart_consumer": "restart",
    "scale_consumer": "scale",
    "pause_pipeline": "pause",
    "reset_consumer_offset": "reset-offset",
}


def idempotency_key(workflow_id: str) -> str:
    return f"{workflow_id}:{STEP}"


def malformed_summary(e: MalformedProposal) -> str:
    return json.dumps([{"type": x["type"], "loc": list(x["loc"])} for x in e.error.errors()])


class RemediationActivities:
    def __init__(self, db: Database, broker: CredentialBroker, fleet: httpx.Client,
                 proposer: Callable[[Incident, list[dict]], ProposalResult], search_fn: Callable[[str, int, bool], list[SearchHit]]):
        self.db = db
        self.broker = broker
        self.fleet = fleet
        self.proposer = proposer
        self.search_fn = search_fn

    def all(self) -> list:
        return [self.retrieve_context, self.propose_action, self.evaluate_policy, self.snapshot_state, self.execute_action,
                self.verify_outcome, self.record_audit]

    @activity.defn
    def retrieve_context(self, inp: RetrieveInput) -> list[SearchHit]:
        return self.search_fn(inp.query, inp.k, inp.rerank)

    @activity.defn
    def propose_action(self, inp: ProposeInput) -> ProposeResult:
        if inp.proposal_json is not None:
            try:
                return ProposeResult(proposal=parse_proposal(inp.proposal_json), source="file", attempts=1)
            except MalformedProposal as e:
                raise ApplicationError("injected proposal is malformed", malformed_summary(e), type="MalformedProposal", non_retryable=True)
        failures = []
        for attempt in range(1, MAX_PROPOSAL_ATTEMPTS + 1):
            try:
                r = self.proposer(inp.incident, [h.model_dump() for h in inp.context])
            except MalformedProposal as e:
                failures.append(malformed_summary(e))
                activity.logger.warning("malformed proposal attempt=%d errors=%s", attempt, failures[-1])
                continue
            except anthropic.APIStatusError as e:
                if e.status_code == 429 or e.status_code >= 500:
                    raise
                raise ApplicationError(f"proposer request rejected: HTTP {e.status_code}", type="ProposerRejected", non_retryable=True)
            return ProposeResult(proposal=r.proposal, source="llm", model=r.model, attempts=attempt, malformed_attempts=failures,
                                 input_tokens=r.input_tokens, output_tokens=r.output_tokens)
        raise ApplicationError(f"proposal malformed after {MAX_PROPOSAL_ATTEMPTS} attempts", failures, type="MalformedProposal", non_retryable=True)

    @activity.defn
    def evaluate_policy(self, proposal: ActionProposal) -> PolicyDecision:
        return evaluate(proposal)

    def _credential(self, action: str, target: str, environment: str, workflow_id: str):
        try:
            return self.broker.issue(action, target, environment, workflow_id, activity.info().attempt)
        except CredentialDenied as e:
            raise ApplicationError(str(e), type="CredentialDenied", non_retryable=True)
        except CredentialUnavailable as e:
            raise ApplicationError(str(e), type="CredentialUnavailable")

    def _read_state(self, workflow_id: str, p: ActionProposal) -> dict:
        cred = self._credential("get_state", p.target, p.environment, workflow_id)
        try:
            r = self.fleet.get(f"/v1/{p.environment}/consumers/{p.target}", headers={"Authorization": f"Bearer {cred.token}"})
        except httpx.TransportError as e:
            raise ApplicationError(f"fleet-api transport error: {type(e).__name__}", type="FleetUnavailable")
        if r.status_code >= 500:
            raise ApplicationError(f"fleet-api HTTP {r.status_code}", type="FleetUnavailable")
        if r.status_code >= 400:
            raise ApplicationError(f"fleet-api HTTP {r.status_code}: {r.text[:200]}", type="FleetRejected", non_retryable=True)
        return {k: r.json()[k] for k in ("replicas", "restart_count", "paused", "offset")}

    @activity.defn
    def snapshot_state(self, inp: ActionInput) -> dict:
        return self._read_state(inp.workflow_id, inp.proposal)

    @activity.defn
    def execute_action(self, inp: ActionInput) -> ExecuteResult:
        info = activity.info()
        p, key = inp.proposal, idempotency_key(inp.workflow_id)
        entry = LedgerEntry(workflow_id=inp.workflow_id, step=STEP, idempotency_key=key, action=p.action, target=p.target,
                            environment=p.environment, params=p.params, attempt=info.attempt, status="PENDING",
                            credential_mode=self.broker.mode, run_id=info.workflow_run_id)
        row = self.db[LEDGER].insert_one(entry.model_dump()).inserted_id

        def finish(status: str, outcome: str, error: Optional[str] = None, response: Optional[dict] = None):
            self.db[LEDGER].update_one({"_id": row}, {"$set": {"status": status, "outcome": outcome, "error": error,
                                                                "response": response, "finished_at": utcnow()}})

        try:
            cred = self._credential(p.action, p.target, p.environment, inp.workflow_id)
        except ApplicationError as e:
            finish("FAILED", "CREDENTIAL_DENIED" if e.type == "CredentialDenied" else "CREDENTIAL_UNAVAILABLE", str(e))
            raise
        body = {"replicas": p.params.replicas} if p.action == "scale_consumer" else None
        try:
            r = self.fleet.post(f"/v1/{p.environment}/consumers/{p.target}/{PATHS[p.action]}", json=body,
                                headers={"Authorization": f"Bearer {cred.token}", "Idempotency-Key": key})
        except httpx.TransportError as e:
            finish("FAILED", "TRANSPORT_ERROR", type(e).__name__)
            raise ApplicationError(f"fleet-api transport error: {type(e).__name__}", type="FleetUnavailable")
        if r.status_code >= 500:
            finish("FAILED", f"HTTP_{r.status_code}", r.text[:200])
            raise ApplicationError(f"fleet-api HTTP {r.status_code}", type="FleetUnavailable")
        if r.status_code >= 400:
            finish("FAILED", f"HTTP_{r.status_code}", r.text[:200])
            raise ApplicationError(f"fleet-api HTTP {r.status_code}: {r.text[:200]}", type="FleetRejected", non_retryable=True)
        replayed = r.headers.get("Idempotent-Replayed") == "true"
        finish("APPLIED", "REPLAYED" if replayed else "APPLIED", response=r.json())
        return ExecuteResult(status_code=r.status_code, replayed=replayed, attempt=info.attempt, idempotency_key=key, body=r.json())

    @activity.defn
    def verify_outcome(self, inp: VerifyInput) -> VerifyResult:
        after = self._read_state(inp.workflow_id, inp.proposal)
        p, before = inp.proposal, inp.before
        if p.action == "restart_consumer":
            check, expected, observed = "restart_count increased by exactly 1", before["restart_count"] + 1, after["restart_count"]
        elif p.action == "scale_consumer":
            check, expected, observed = "replicas equals requested", p.params.replicas, after["replicas"]
        elif p.action == "pause_pipeline":
            check, expected, observed = "paused is true", True, after["paused"]
        else:
            check, expected, observed = "offset reset to 0", 0, after["offset"]
        return VerifyResult(ok=expected == observed, check=check, expected=expected, observed=observed, after=after)

    @activity.defn
    def record_audit(self, inp: AuditInput) -> str:
        event = AuditEvent(workflow_id=inp.workflow_id, incident_id=inp.incident_id, step="remediation",
                           detail={"status": inp.status, "run_id": inp.run_id, "credential_mode": self.broker.mode, **inp.record})
        audit_id = f"{inp.workflow_id}:{inp.run_id}"
        self.db[AUDIT].replace_one({"_id": audit_id}, event.model_dump(), upsert=True)
        record = inp.record
        self.db[INCIDENTS].update_one({"_id": inp.incident_id}, {"$set": {
            "status": inp.status, "workflow_id": inp.workflow_id, "run_id": inp.run_id, "incident": record.get("incident"),
            "action": (record.get("proposal") or {}).get("action"), "decision": (record.get("decision") or {}).get("decision"),
            "audit_id": audit_id, "updated_at": utcnow()}}, upsert=True)
        return audit_id
