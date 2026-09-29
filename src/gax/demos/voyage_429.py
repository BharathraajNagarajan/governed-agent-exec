import re
import httpx
from gax.retrieval.voyage import EMBED_MODEL

NAME = "voyage_429"
TITLE = "Voyage free-tier rate limit exhausted, then an incident uses real retrieval"
PROBES = 8
BACKOFF = re.compile(r"voyage 429 path=(\S+) attempt=(\d+) backoff_s=([\d.]+)")


async def run(d):
    key = d.s.voyage_api_key.get_secret_value()
    if not d.check("VOYAGE_API_KEY configured", key, bool(key), reproduce=True):
        return
    probes = []
    with httpx.Client(base_url=d.s.voyage_base_url.rstrip("/") + "/", headers={"Authorization": f"Bearer {key}"}, timeout=30) as c:
        for i in range(PROBES):
            r = c.post("embeddings", json={"input": [f"rate limit probe {i}"], "model": EMBED_MODEL, "input_type": "query"})
            probes.append(r.status_code)
            if r.status_code == 429:
                break
    d.evidence["direct_probe_statuses"] = probes
    d.check("direct calls (bypassing the client limiter) hit 429", 429 in probes, probes)
    wid = d.incident_id()
    h = await d.start(wid, retrieve=True)
    status = await d.outcome(h, 360)
    hist = await d.history(h)
    detail = (d.audit(wid, h.run_id) or {}).get("detail", {})
    backoffs = [{"path": p, "attempt": int(a), "backoff_s": float(b)} for p, a, b in BACKOFF.findall(d.worker_log())]
    d.evidence.update(worker_429_backoffs=backoffs, history=hist, context=detail.get("context"), retrieval_error=detail.get("retrieval_error"))
    d.check("real 429s in worker log", backoffs, backoffs, reproduce=True)
    d.check("retrieve_context completed", "retrieve_context" in hist["completed"], hist["completed"])
    d.check("retrieval returned runbook chunks, no retrieval_error", detail.get("context") and not detail.get("retrieval_error"),
            {"chunks": [c["chunk_id"] for c in detail.get("context") or []], "retrieval_error": detail.get("retrieval_error")})
    d.check("status VERIFIED", status == "VERIFIED", status)
