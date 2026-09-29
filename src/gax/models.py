from datetime import datetime, timezone
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, ValidationError

Action = Literal["restart_consumer", "scale_consumer", "pause_pipeline", "reset_consumer_offset"]
Environment = Literal["staging", "prod"]
Decision = Literal["ALLOW", "REQUIRE_APPROVAL", "DENY"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Incident(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incident_id: str
    environment: Environment
    target: str
    summary: str
    created_at: datetime = Field(default_factory=utcnow)


class ActionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replicas: Optional[int] = None


class ActionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Action
    target: str
    environment: Environment
    params: ActionParams
    justification: str
    cited_chunk_ids: list[str]


class MalformedProposal(Exception):
    def __init__(self, raw: str, error: ValidationError):
        super().__init__(f"{error.error_count()} validation error(s)")
        self.raw = raw
        self.error = error


def parse_proposal(raw: str) -> ActionProposal:
    try:
        return ActionProposal.model_validate_json(raw)
    except ValidationError as e:
        raise MalformedProposal(raw, e) from e


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    decision: Decision
    reason: str
    policy_version: str


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow_id: str
    incident_id: str
    step: str
    detail: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=utcnow)


class LedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow_id: str
    step: str
    idempotency_key: str
    action: Action
    target: str
    environment: Environment
    params: ActionParams
    attempt: int = Field(ge=1)
    status: Literal["PENDING", "APPLIED", "FAILED"]
    credential_mode: str
    run_id: Optional[str] = None
    outcome: Optional[str] = None
    error: Optional[str] = None
    response: Optional[dict[str, Any]] = None
    at: datetime = Field(default_factory=utcnow)
