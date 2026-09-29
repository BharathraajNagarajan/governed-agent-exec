import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
import jwt
from gax.credentials.base import Credential, CredentialDenied, CredentialUnavailable

MODE = "LOCAL-ONLY"
ISSUER = "gax-local-only-broker"
AUDIENCE = "fleet-api"
ALGORITHM = "HS256"
MIN_KEY_BYTES = 32
ALL = "*"
REVOKED = "REVOKED"
TRANSIENT = "TRANSIENT"
GRANTS_COLLECTION = "local_only_broker_grants"

log = logging.getLogger(__name__)


class InMemoryGrantStore:
    def __init__(self):
        self._grants: dict[str, dict] = {}

    def get(self, scope: str) -> Optional[dict]:
        return self._grants.get(scope)

    def put(self, scope: str, state: str, remaining: Optional[int] = None) -> None:
        self._grants[scope] = {"state": state, "remaining": remaining}

    def delete(self, scope: str) -> None:
        self._grants.pop(scope, None)

    def consume_transient(self, scope: str) -> bool:
        grant = self._grants.get(scope)
        if not grant or grant["state"] != TRANSIENT:
            return False
        if grant["remaining"] is None:
            return True
        if grant["remaining"] > 0:
            grant["remaining"] -= 1
            return True
        self.delete(scope)
        return False


class MongoGrantStore:
    def __init__(self, collection):
        self._c = collection

    def get(self, scope: str) -> Optional[dict]:
        return self._c.find_one({"_id": scope})

    def put(self, scope: str, state: str, remaining: Optional[int] = None) -> None:
        self._c.replace_one({"_id": scope}, {"state": state, "remaining": remaining}, upsert=True)

    def delete(self, scope: str) -> None:
        self._c.delete_one({"_id": scope})

    def consume_transient(self, scope: str) -> bool:
        grant = self.get(scope)
        if not grant or grant["state"] != TRANSIENT:
            return False
        if grant["remaining"] is None:
            return True
        if self._c.find_one_and_update({"_id": scope, "state": TRANSIENT, "remaining": {"$gt": 0}}, {"$inc": {"remaining": -1}}):
            return True
        self._c.delete_one({"_id": scope, "state": TRANSIENT, "remaining": {"$lte": 0}})
        return False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LocalOnlyCredentialBroker:
    mode = MODE

    def __init__(self, signing_key: str, store=None, ttl_seconds: int = 60, clock: Callable[[], datetime] = _utcnow):
        if len(signing_key.encode()) < MIN_KEY_BYTES:
            raise ValueError(f"LOCAL-ONLY broker requires LOCAL_BROKER_SIGNING_KEY of at least {MIN_KEY_BYTES} bytes")
        self._key = signing_key
        self.store = store if store is not None else InMemoryGrantStore()
        self.ttl_seconds = ttl_seconds
        self._clock = clock

    def revoke(self, action: str = ALL) -> None:
        self.store.put(action, REVOKED)
        log.warning("LOCAL-ONLY broker grant revoked scope=%s", action)

    def restore(self, action: str = ALL) -> None:
        self.store.delete(action)
        log.warning("LOCAL-ONLY broker grant restored scope=%s", action)

    def set_transient(self, action: str = ALL, count: Optional[int] = None) -> None:
        self.store.put(action, TRANSIENT, count)
        log.warning("LOCAL-ONLY broker grant set transient scope=%s count=%s", action, count)

    def _check(self, action: str, context: str) -> None:
        scopes = (action, ALL)
        if any((g := self.store.get(s)) and g["state"] == REVOKED for s in scopes):
            log.warning("LOCAL-ONLY broker denied credential %s", context)
            raise CredentialDenied(f"LOCAL-ONLY grant revoked for {action}")
        if any(self.store.consume_transient(s) for s in scopes):
            log.warning("LOCAL-ONLY broker transient failure %s", context)
            raise CredentialUnavailable(f"LOCAL-ONLY broker transiently unavailable for {action}")

    def issue(self, action: str, target: str, environment: str, workflow_id: str, attempt: int) -> Credential:
        context = f"action={action} target={target} environment={environment} workflow_id={workflow_id} attempt={attempt}"
        self._check(action, context)
        now = self._clock()
        expires_at = now + timedelta(seconds=self.ttl_seconds)
        jti = uuid.uuid4().hex
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": f"workflow:{workflow_id}",
            "jti": jti,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "mode": MODE,
            "action": action,
            "target": target,
            "environment": environment,
            "workflow_id": workflow_id,
            "attempt": attempt,
        }
        token = jwt.encode(claims, self._key, algorithm=ALGORITHM)
        log.info("LOCAL-ONLY broker issued credential %s jti=%s exp=%s", context, jti, expires_at.isoformat())
        return Credential(token=token, expires_at=expires_at, mode=MODE)


def verify(token: str, signing_key: str) -> dict:
    claims = jwt.decode(
        token,
        signing_key,
        algorithms=[ALGORITHM],
        audience=AUDIENCE,
        issuer=ISSUER,
        options={"require": ["exp", "iat", "iss", "aud", "sub", "jti"]},
    )
    if claims.get("mode") != MODE:
        raise jwt.InvalidTokenError("token is not a LOCAL-ONLY credential")
    return claims
