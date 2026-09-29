import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional
import httpx
from pydantic import SecretStr

EMBED_MODEL = "voyage-4-lite"
RERANK_MODEL = "rerank-2.5-lite"
EMBED_DIMS = 1024
FREE_TIER_RPM = 3
FREE_TIER_TPM = 10_000

log = logging.getLogger(__name__)


class VoyageError(Exception):
    retryable = False


class VoyageRateLimited(VoyageError):
    retryable = True


class VoyageUnavailable(VoyageError):
    retryable = True


class VoyageAuthError(VoyageError):
    pass


class VoyageRequestError(VoyageError):
    pass


class RateLimiter:
    def __init__(self, rpm: int = FREE_TIER_RPM, tpm: int = FREE_TIER_TPM, window: float = 60.0,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.rpm, self.tpm, self.window = rpm, tpm, window
        self._clock, self._sleep = clock, sleep
        self._events: list[list[float]] = []
        self._lock = threading.Lock()

    def acquire(self, tokens: int, on_wait: Optional[Callable[[float], None]] = None) -> list[float]:
        with self._lock:
            while True:
                now = self._clock()
                self._events = [e for e in self._events if now - e[0] < self.window]
                used = sum(e[1] for e in self._events)
                if not self._events or (len(self._events) < self.rpm and used + tokens <= self.tpm):
                    event = [now, float(tokens)]
                    self._events.append(event)
                    return event
                wait = self._events[0][0] + self.window - now
                if on_wait:
                    on_wait(wait)
                self._sleep(max(wait, 0.0))


@dataclass
class EmbedResult:
    vectors: list[list[float]]
    model: str
    total_tokens: int


class VoyageClient:
    def __init__(self, base_url: str, api_key: SecretStr | str, embed_model: str = EMBED_MODEL, rerank_model: str = RERANK_MODEL,
                 limiter: Optional[RateLimiter] = None, max_attempts: int = 6, base_delay: float = 2.0, max_delay: float = 32.0,
                 sleep: Callable[[float], None] = time.sleep, transport: Optional[httpx.BaseTransport] = None, timeout: float = 30.0):
        key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        if not key:
            raise VoyageAuthError("VOYAGE_API_KEY is not set")
        self.embed_model, self.rerank_model, self.dims = embed_model, rerank_model, EMBED_DIMS
        self.limiter = limiter or RateLimiter()
        self.max_attempts, self.base_delay, self.max_delay = max_attempts, base_delay, max_delay
        self._sleep = sleep
        self._http = httpx.Client(base_url=base_url.rstrip("/") + "/", headers={"Authorization": f"Bearer {key}"},
                                  timeout=timeout, transport=transport)
        self.rate_limited = 0

    def _post(self, path: str, body: dict, est_tokens: int, on_wait: Optional[Callable[[float], None]] = None) -> dict:
        for attempt in range(1, self.max_attempts + 1):
            event = self.limiter.acquire(est_tokens, on_wait)
            try:
                r = self._http.post(path, json=body)
            except httpx.TransportError as e:
                raise VoyageUnavailable(f"{path}: {type(e).__name__}") from e
            if r.status_code == 429:
                self.rate_limited += 1
                if attempt == self.max_attempts:
                    raise VoyageRateLimited(f"{path}: still 429 after {attempt} attempts")
                delay = min(self.base_delay * 2 ** (attempt - 1), self.max_delay)
                log.warning("voyage 429 path=%s attempt=%d backoff_s=%.1f", path, attempt, delay)
                if on_wait:
                    on_wait(delay)
                self._sleep(delay)
                continue
            if r.status_code in (401, 403):
                raise VoyageAuthError(f"{path}: HTTP {r.status_code}")
            if r.status_code >= 500:
                raise VoyageUnavailable(f"{path}: HTTP {r.status_code}")
            if r.status_code >= 400:
                raise VoyageRequestError(f"{path}: HTTP {r.status_code} {r.text[:200]}")
            data = r.json()
            event[1] = float(data.get("usage", {}).get("total_tokens", event[1]))
            return data
        raise VoyageRateLimited(f"{path}: no attempts made")

    def embed(self, texts: list[str], input_type: str, est_tokens: int, on_wait: Optional[Callable[[float], None]] = None) -> EmbedResult:
        data = self._post("embeddings", {"input": texts, "model": self.embed_model, "input_type": input_type}, est_tokens, on_wait)
        vectors = [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]
        if len(vectors) != len(texts):
            raise VoyageRequestError(f"embeddings: expected {len(texts)} vectors, got {len(vectors)}")
        return EmbedResult(vectors, data.get("model", self.embed_model), int(data.get("usage", {}).get("total_tokens", 0)))

    def rerank(self, query: str, documents: list[str], top_k: int, est_tokens: int) -> list[tuple[int, float]]:
        data = self._post("rerank", {"query": query, "documents": documents, "model": self.rerank_model, "top_k": top_k}, est_tokens)
        return [(d["index"], float(d["relevance_score"])) for d in data["data"]]

    def close(self) -> None:
        self._http.close()
