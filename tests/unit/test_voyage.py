import json
import httpx
import pytest
from gax.retrieval.voyage import (RateLimiter, VoyageAuthError, VoyageClient, VoyageRateLimited, VoyageRequestError,
                                  VoyageUnavailable)

KEY = "voyage-test-key-not-real"


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


def embed_ok(request):
    n = len(json.loads(request.content)["input"])
    return httpx.Response(200, json={"data": [{"index": i, "embedding": [float(i)]} for i in reversed(range(n))],
                                     "model": "voyage-4-lite", "usage": {"total_tokens": 7}})


def client(handler, clock=None):
    clock = clock or Clock()
    limiter = RateLimiter(rpm=1000, tpm=10**9, clock=clock, sleep=clock.sleep)
    return VoyageClient("https://voyage.test/v1", KEY, limiter=limiter, sleep=clock.sleep, transport=httpx.MockTransport(handler)), clock


def test_embed_sends_auth_and_orders_by_index():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["url"] = str(request.url)
        return embed_ok(request)

    c, _ = client(handler)
    r = c.embed(["a", "b", "c"], "document", 3)
    assert r.vectors == [[0.0], [1.0], [2.0]]
    assert r.total_tokens == 7
    assert seen == {"auth": f"Bearer {KEY}", "url": "https://voyage.test/v1/embeddings"}


def test_429_backs_off_exponentially_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, json={"detail": "rate limited"}) if len(calls) <= 3 else embed_ok(request)

    c, clock = client(handler)
    c.embed(["a"], "query", 1)
    assert clock.sleeps == [2.0, 4.0, 8.0]
    assert c.rate_limited == 3


def test_429_is_bounded():
    c, clock = client(lambda r: httpx.Response(429))
    with pytest.raises(VoyageRateLimited) as e:
        c.embed(["a"], "query", 1)
    assert e.value.retryable is True
    assert clock.sleeps == [2.0, 4.0, 8.0, 16.0, 32.0]
    assert c.rate_limited == 6


@pytest.mark.parametrize("status,error,retryable", [
    (401, VoyageAuthError, False), (403, VoyageAuthError, False), (400, VoyageRequestError, False),
    (500, VoyageUnavailable, True), (503, VoyageUnavailable, True),
])
def test_typed_errors(status, error, retryable):
    c, _ = client(lambda r: httpx.Response(status, text="nope"))
    with pytest.raises(error) as e:
        c.embed(["a"], "query", 1)
    assert e.value.retryable is retryable
    assert KEY not in str(e.value)


def test_transport_error_is_unavailable():
    def handler(request):
        raise httpx.ConnectError("refused")

    c, _ = client(handler)
    with pytest.raises(VoyageUnavailable):
        c.embed(["a"], "query", 1)


def test_missing_key_rejected():
    with pytest.raises(VoyageAuthError):
        VoyageClient("https://voyage.test/v1", "")


def test_rerank_parses_scores():
    c, _ = client(lambda r: httpx.Response(200, json={"data": [{"index": 2, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]}))
    assert c.rerank("q", ["a", "b", "c"], 2, 5) == [(2, 0.9), (0, 0.1)]


def test_limiter_enforces_rpm():
    clock = Clock()
    limiter = RateLimiter(rpm=3, tpm=10**6, clock=clock, sleep=clock.sleep)
    for _ in range(4):
        limiter.acquire(10)
    assert clock.sleeps == [60.0]


def test_limiter_enforces_tpm_and_admits_oversized_request_alone():
    clock = Clock()
    waits = []
    limiter = RateLimiter(rpm=100, tpm=10_000, clock=clock, sleep=clock.sleep)
    limiter.acquire(6000)
    clock.now += 5
    limiter.acquire(6000, on_wait=waits.append)
    assert waits == [55.0]
    limiter.acquire(50_000)
    assert clock.now == 120.0


def test_limiter_uses_actual_usage_after_response():
    clock = Clock()
    c, _ = client(embed_ok, clock)
    c.limiter = RateLimiter(rpm=100, tpm=100, clock=clock, sleep=clock.sleep)
    c.embed(["a"], "query", 90)
    c.limiter.acquire(90)
    assert clock.sleeps == []
