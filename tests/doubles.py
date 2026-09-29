import hashlib
import json
import math
import re
from collections import Counter
import httpx
import jwt
from gax.credentials.local_only_broker import verify
from gax.llm import ProposalResult
from gax.models import parse_proposal
from gax.retrieval.search import SearchHit
from gax.retrieval.voyage import EmbedResult

WORD = re.compile(r"[a-z0-9_]+")


def words(text: str) -> list[str]:
    return WORD.findall(text.lower())


class FakeEmbedderTestDouble:
    embed_model = "fake-embedder-test-double"
    dims = 64

    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    def _vector(self, text: str) -> list[float]:
        v = [0.0] * self.dims
        for w in words(text):
            v[int(hashlib.sha256(w.encode()).hexdigest(), 16) % self.dims] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed(self, texts, input_type, est_tokens, on_wait=None) -> EmbedResult:
        self.calls.append((input_type, len(texts)))
        return EmbedResult([self._vector(t) for t in texts], self.embed_model, sum(len(words(t)) for t in texts))

    def rerank(self, query, documents, top_k, est_tokens):
        q = set(words(query))
        scores = [(i, len(q & set(words(d))) / (len(q) or 1)) for i, d in enumerate(documents)]
        return sorted(scores, key=lambda s: (-s[1], s[0]))[:top_k]


class FleetApiTestDouble:
    SEED = {"replicas": 3, "restart_count": 0, "paused": False, "offset": 1000}
    UPDATES = {"restart": "restart_consumer", "scale": "scale_consumer", "pause": "pause_pipeline", "reset-offset": "reset_consumer_offset"}

    def __init__(self, signing_key: str):
        self.key = signing_key
        self.state = {(env, c): dict(self.SEED) for env in ("staging", "prod") for c in ("orders-consumer", "payments-consumer")}
        self.idem: dict[str, dict] = {}
        self.counters: Counter = Counter()
        self.tokens_seen: list[str] = []
        self.fail_next = 0
        self.drop_next = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")
        env, consumer = parts[1], parts[3]
        action = self.UPDATES.get(parts[4]) if len(parts) > 4 else "get_state"
        self.counters[action] += 1
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        self.tokens_seen.append(token)
        try:
            claims = verify(token, self.key)
        except jwt.InvalidTokenError:
            return httpx.Response(401)
        if claims["environment"] != env or (action != "get_state" and (claims["action"], claims["target"]) != (action, consumer)):
            return httpx.Response(403)
        if (env, consumer) not in self.state:
            return httpx.Response(404)
        doc = self.state[(env, consumer)]
        if action == "get_state":
            return httpx.Response(200, json={"environment": env, "consumer": consumer, **doc})
        if self.fail_next:
            self.fail_next -= 1
            return httpx.Response(500)
        key = request.headers["idempotency-key"]
        if key in self.idem:
            return httpx.Response(200, json=self.idem[key], headers={"Idempotent-Replayed": "true"})
        if action == "restart_consumer":
            doc["restart_count"] += 1
        elif action == "scale_consumer":
            doc["replicas"] = json.loads(request.content)["replicas"]
        elif action == "pause_pipeline":
            doc["paused"] = True
        else:
            doc["offset"] = 0
        self.idem[key] = {"action": action, "environment": env, "consumer": consumer, "state": dict(doc), "credential_jti": claims["jti"]}
        if self.drop_next:
            self.drop_next -= 1
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(200, json=self.idem[key])


class ProposerTestDouble:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, incident, chunks):
        self.calls += 1
        outcome = self.outcomes[min(self.calls, len(self.outcomes)) - 1]
        if isinstance(outcome, str):
            parse_proposal(outcome)
        return ProposalResult(outcome, "proposer-test-double", "end_turn", 10, 10)


class SearchTestDouble:
    def __init__(self):
        self.queries: list[str] = []

    def __call__(self, query, k, rerank):
        self.queries.append(query)
        return [SearchHit(chunk_id="consumer-lag-no-active-members#remediation", source="consumer-lag-no-active-members.md",
                          heading="Remediation", text="Restart the consumer with restart_consumer.", vector_score=0.9, rerank_score=0.8)]
