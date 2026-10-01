import asyncio
import logging
import os
import threading
import time
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal, Optional
import jwt
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
from gax.config import Settings, get_settings
from gax.credentials.keycard_broker import MODE as KEYCARD_MODE, KeycardVerifier
from gax.credentials.local_only_broker import MIN_KEY_BYTES, MODE, verify

log = logging.getLogger("fleet_api")

Environment = Literal["staging", "prod"]
FaultScope = Literal["*", "restart_consumer", "scale_consumer", "pause_pipeline", "reset_consumer_offset"]
SEED_CONSUMERS = {"orders-consumer": 3, "payments-consumer": 2}
SEED_OFFSET = 1000


class ScaleBody(BaseModel):
    replicas: int = Field(ge=0)


class FaultSpec(BaseModel):
    action: FaultScope = "*"
    fail_next: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    drop_next: int = Field(default=0, ge=0)


class ConsumerNotFound(Exception):
    pass


class DropAfterCommit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        flag = {"drop": False}
        scope["gax.drop"] = flag

        async def guarded(message):
            if not flag["drop"]:
                await send(message)

        await self.app(scope, receive, guarded)
        if flag["drop"]:
            cycle = send.__self__
            cycle.transport.close()
            for _ in range(200):
                if cycle.disconnected:
                    break
                await asyncio.sleep(0.01)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_app(settings: Optional[Settings] = None, verifier: Optional[KeycardVerifier] = None):
    settings = settings or get_settings()
    if settings.keycard_zone_url and "credential_mode" not in settings.model_fields_set:
        raise NotImplementedError("KEYCARD_ZONE_URL is set but CREDENTIAL_MODE is not; set CREDENTIAL_MODE=local-only or keycard (ADR 0002)")
    keycard = settings.credential_mode == "keycard"
    auth_mode = KEYCARD_MODE if keycard else MODE
    key = settings.local_broker_signing_key.get_secret_value()
    if keycard:
        if not settings.keycard_zone_url:
            raise RuntimeError(f"fleet-api ({KEYCARD_MODE}) requires KEYCARD_ZONE_URL")
        verifier = verifier or KeycardVerifier(settings.keycard_zone_url, settings.keycard_resource_prefix)
    elif len(key.encode()) < MIN_KEY_BYTES:
        raise RuntimeError(f"fleet-api ({MODE}) requires LOCAL_BROKER_SIGNING_KEY of at least {MIN_KEY_BYTES} bytes")
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)
    db = client[settings.gax_db]
    state, idem = db["fleet_state"], db["fleet_idempotency"]
    lock = threading.Lock()
    counters: Counter = Counter()
    faults: dict[str, dict] = {}

    def seed():
        state.delete_many({})
        idem.delete_many({})
        state.insert_many([
            {"_id": f"{env}:{name}", "environment": env, "consumer": name, "replicas": replicas,
             "restart_count": 0, "paused": False, "offset": SEED_OFFSET, "updated_at": _now()}
            for env in ("staging", "prod") for name, replicas in SEED_CONSUMERS.items()
        ])

    @asynccontextmanager
    async def lifespan(_):
        client.admin.command("ping")
        if state.count_documents({}) == 0:
            seed()
        log.warning("fleet-api started auth_mode=%s pid=%s db=%s", auth_mode, os.getpid(), settings.gax_db)
        yield
        client.close()

    api = FastAPI(title=f"fleet-api ({auth_mode})", lifespan=lifespan)

    def authorize(authorization: Optional[str], environment: str, action: Optional[str] = None, consumer: Optional[str] = None) -> dict:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "missing bearer token")
        token = authorization.removeprefix("Bearer ")
        if keycard:
            try:
                return verifier.verify(token, action or "get_state")
            except jwt.PyJWKClientConnectionError:
                raise HTTPException(503, "keycard jwks unavailable")
            except jwt.PyJWTError as e:
                raise HTTPException(401, f"invalid token: {type(e).__name__}")
        try:
            claims = verify(token, key)
        except jwt.InvalidTokenError as e:
            raise HTTPException(401, f"invalid token: {type(e).__name__}")
        if claims.get("environment") != environment:
            raise HTTPException(403, "token environment does not match")
        if action and (claims.get("action") != action or claims.get("target") != consumer):
            raise HTTPException(403, "token action or target does not match")
        return claims

    def pre_faults(action: str) -> None:
        with lock:
            specs = [faults[s] for s in (action, "*") if s in faults]
            latency_ms = sum(s["latency_ms"] for s in specs)
            fail = next((s for s in specs if s["fail_next"] > 0), None)
            if fail:
                fail["fail_next"] -= 1
        if latency_ms:
            time.sleep(latency_ms / 1000)
        if fail:
            log.warning("FAULT injected 500 action=%s", action)
            raise HTTPException(500, "injected failure")

    def take_drop(action: str) -> bool:
        with lock:
            spec = next((faults[s] for s in (action, "*") if s in faults and faults[s]["drop_next"] > 0), None)
            if spec:
                spec["drop_next"] -= 1
            return spec is not None

    def replay(stored: dict, fingerprint: dict) -> JSONResponse:
        if stored["fingerprint"] != fingerprint:
            raise HTTPException(422, "Idempotency-Key reused with a different request")
        return JSONResponse(stored["body"], status_code=stored["status_code"], headers={"Idempotent-Replayed": "true"})

    def mutate(request: Request, action: str, environment: str, consumer: str, payload: dict, update: dict,
               authorization: Optional[str], idempotency_key: Optional[str]) -> JSONResponse:
        with lock:
            counters[action] += 1
        claims = authorize(authorization, environment, action, consumer)
        if not idempotency_key:
            raise HTTPException(400, "Idempotency-Key header required")
        pre_faults(action)
        fingerprint = {"action": action, "environment": environment, "consumer": consumer, "payload": payload}
        stored = idem.find_one({"_id": idempotency_key})
        if stored:
            return replay(stored, fingerprint)

        def apply(session):
            now = _now()
            doc = state.find_one_and_update(
                {"_id": f"{environment}:{consumer}"},
                {**update, "$set": {**update.get("$set", {}), "updated_at": now}},
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if doc is None:
                raise ConsumerNotFound()
            body = {
                "action": action,
                "environment": environment,
                "consumer": consumer,
                "state": {k: doc[k] for k in ("replicas", "restart_count", "paused", "offset")},
                "credential_jti": claims["jti"],
                "applied_at": now.isoformat(),
            }
            idem.insert_one({"_id": idempotency_key, "fingerprint": fingerprint, "status_code": 200, "body": body, "at": now}, session=session)
            return body

        try:
            with client.start_session() as session:
                body = session.with_transaction(apply)
        except ConsumerNotFound:
            raise HTTPException(404, f"consumer {environment}:{consumer} not found")
        except DuplicateKeyError:
            return replay(idem.find_one({"_id": idempotency_key}), fingerprint)
        if take_drop(action):
            log.warning("FAULT commit-then-drop action=%s idempotency_key=%s", action, idempotency_key)
            request.scope["gax.drop"]["drop"] = True
        return JSONResponse(body)

    @api.get("/healthz")
    def healthz():
        return {"status": "ok", "auth_mode": auth_mode, "pid": os.getpid()}

    @api.get("/v1/{environment}/consumers/{consumer}")
    def get_state(environment: Environment, consumer: str, authorization: Optional[str] = Header(None)):
        with lock:
            counters["get_state"] += 1
        authorize(authorization, environment)
        doc = state.find_one({"_id": f"{environment}:{consumer}"}, {"_id": 0})
        if doc is None:
            raise HTTPException(404, f"consumer {environment}:{consumer} not found")
        doc["updated_at"] = doc["updated_at"].isoformat()
        return doc

    @api.post("/v1/{environment}/consumers/{consumer}/restart")
    def restart_consumer(request: Request, environment: Environment, consumer: str,
                         authorization: Optional[str] = Header(None), idempotency_key: Optional[str] = Header(None)):
        return mutate(request, "restart_consumer", environment, consumer, {}, {"$inc": {"restart_count": 1}}, authorization, idempotency_key)

    @api.post("/v1/{environment}/consumers/{consumer}/scale")
    def scale_consumer(request: Request, environment: Environment, consumer: str, body: ScaleBody,
                       authorization: Optional[str] = Header(None), idempotency_key: Optional[str] = Header(None)):
        return mutate(request, "scale_consumer", environment, consumer, body.model_dump(), {"$set": {"replicas": body.replicas}}, authorization, idempotency_key)

    @api.post("/v1/{environment}/consumers/{consumer}/pause")
    def pause_pipeline(request: Request, environment: Environment, consumer: str,
                       authorization: Optional[str] = Header(None), idempotency_key: Optional[str] = Header(None)):
        return mutate(request, "pause_pipeline", environment, consumer, {}, {"$set": {"paused": True}}, authorization, idempotency_key)

    @api.post("/v1/{environment}/consumers/{consumer}/reset-offset")
    def reset_consumer_offset(request: Request, environment: Environment, consumer: str,
                              authorization: Optional[str] = Header(None), idempotency_key: Optional[str] = Header(None)):
        return mutate(request, "reset_consumer_offset", environment, consumer, {}, {"$set": {"offset": 0}}, authorization, idempotency_key)

    @api.post("/admin/faults")
    def set_faults(spec: FaultSpec):
        with lock:
            faults[spec.action] = spec.model_dump(exclude={"action"})
            return dict(faults)

    @api.get("/admin/faults")
    def get_faults():
        with lock:
            return dict(faults)

    @api.delete("/admin/faults")
    def clear_faults():
        with lock:
            faults.clear()
        return {}

    @api.get("/admin/counters")
    def get_counters():
        with lock:
            return dict(counters)

    @api.delete("/admin/counters")
    def clear_counters():
        with lock:
            counters.clear()
        return {}

    @api.post("/admin/reset")
    def reset():
        seed()
        with lock:
            faults.clear()
            counters.clear()
        return {"status": "reset"}

    return DropAfterCommit(api)
