from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


class CredentialError(Exception):
    retryable: bool


class CredentialDenied(CredentialError):
    retryable = False


class CredentialUnavailable(CredentialError):
    retryable = True


@dataclass(frozen=True)
class Credential:
    token: str = field(repr=False)
    expires_at: datetime
    mode: str


class CredentialBroker(Protocol):
    mode: str

    def issue(self, action: str, target: str, environment: str, workflow_id: str, attempt: int) -> Credential: ...
