from typing import Any, Literal, Optional
from pydantic import BaseModel
from gax.models import ActionProposal, Incident
from gax.retrieval.search import SearchHit

FinalStatus = Literal["VERIFIED", "VERIFY_FAILED", "DENIED", "REJECTED", "APPROVAL_TIMEOUT", "NEEDS_HUMAN", "CREDENTIAL_DENIED", "FAILED"]
FAILURE_STATUSES = {"VERIFY_FAILED", "APPROVAL_TIMEOUT", "NEEDS_HUMAN", "CREDENTIAL_DENIED", "FAILED"}


class RemediationInput(BaseModel):
    incident: Incident
    proposal_json: Optional[str] = None
    rerank: bool = True
    k: int = 5
    approval_timeout_seconds: int = 900


class RetrieveInput(BaseModel):
    query: str
    k: int
    rerank: bool


class ProposeInput(BaseModel):
    incident: Incident
    context: list[SearchHit]
    proposal_json: Optional[str] = None


class ProposeResult(BaseModel):
    proposal: ActionProposal
    source: Literal["llm", "file"]
    model: Optional[str] = None
    attempts: int
    malformed_attempts: list[str] = []
    input_tokens: int = 0
    output_tokens: int = 0


class ActionInput(BaseModel):
    workflow_id: str
    proposal: ActionProposal


class ExecuteResult(BaseModel):
    status_code: int
    replayed: bool
    attempt: int
    idempotency_key: str
    body: dict[str, Any]


class VerifyInput(BaseModel):
    workflow_id: str
    proposal: ActionProposal
    before: dict[str, Any]


class VerifyResult(BaseModel):
    ok: bool
    check: str
    expected: Any
    observed: Any
    after: dict[str, Any]


class ApprovalInput(BaseModel):
    by: str
    comment: str = ""


class AuditInput(BaseModel):
    workflow_id: str
    run_id: str
    incident_id: str
    status: str
    record: dict[str, Any]


class RemediationResult(BaseModel):
    incident_id: str
    status: FinalStatus
