import os
import time
import httpx
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.operations import SearchIndexModel

load_dotenv("../../.env")
BASE = os.environ["VOYAGE_BASE_URL"].rstrip("/")
HEADERS = {"Authorization": f"Bearer {os.environ['VOYAGE_API_KEY']}"}
EMBED_MODEL = "voyage-4-lite"
RERANK_MODEL = "rerank-2.5-lite"
MAX_ATTEMPTS = 5
INDEX = "voyage_vec_idx"
stats = {"429": 0}

CHUNKS = [
    {"_id": "rb-1", "text": "If consumer lag grows while the consumer group shows no active members, restart the consumer in staging."},
    {"_id": "rb-2", "text": "When lag is high and CPU is saturated across all consumers, scale the consumer group to more replicas, up to 10."},
    {"_id": "rb-3", "text": "Pause the pipeline when downstream sinks return errors, to avoid writing partial data."},
    {"_id": "rb-4", "text": "Resetting a consumer offset skips or replays data; never do it in prod without a data owner."},
    {"_id": "rb-5", "text": "Check broker disk usage before any remediation; full disks cause producer timeouts."},
]
QUERY = "consumer lag is climbing and the consumer group has zero members"


def post(path, body):
    for attempt in range(1, MAX_ATTEMPTS + 1):
        t = time.perf_counter()
        r = httpx.post(f"{BASE}/{path}", headers=HEADERS, json=body, timeout=30)
        ms = (time.perf_counter() - t) * 1000
        print(f"{path} attempt={attempt} status={r.status_code} latency_ms={ms:.0f}")
        if r.status_code == 429:
            stats["429"] += 1
            time.sleep(float(r.headers.get("retry-after", 2 ** attempt)))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"{path} still 429 after {MAX_ATTEMPTS} attempts")


docs = post("embeddings", {"input": [c["text"] for c in CHUNKS], "model": EMBED_MODEL, "input_type": "document"})
vecs = [d["embedding"] for d in sorted(docs["data"], key=lambda d: d["index"])]
q = post("embeddings", {"input": [QUERY], "model": EMBED_MODEL, "input_type": "query"})["data"][0]["embedding"]
dims = len(vecs[0])
print("embed model", docs.get("model"), "dims", dims, "usage", docs.get("usage"))

rr = post("rerank", {"query": QUERY, "documents": [c["text"] for c in CHUNKS], "model": RERANK_MODEL, "top_k": 3})
print("rerank model", rr.get("model"), "usage", rr.get("usage"))
for d in rr["data"]:
    print(" rerank", CHUNKS[d["index"]]["_id"], round(d["relevance_score"], 4))

c = MongoClient("mongodb://localhost:27017/?directConnection=true", serverSelectionTimeoutMS=5000)["m0_spike"]["voyage_chunks"]
c.drop()
c.insert_many([{**ch, "embedding": v, "model": EMBED_MODEL} for ch, v in zip(CHUNKS, vecs)])
c.create_search_index(SearchIndexModel(name=INDEX, type="vectorSearch", definition={
    "fields": [{"type": "vector", "path": "embedding", "numDimensions": dims, "similarity": "cosine"}]}))
end = time.time() + 120
while not any(i.get("queryable") for i in c.list_search_indexes(INDEX)):
    if time.time() > end:
        raise TimeoutError("index not queryable")
    time.sleep(2)
print("index queryable, numDimensions", dims)
for r in c.aggregate([
    {"$vectorSearch": {"index": INDEX, "path": "embedding", "queryVector": q, "numCandidates": 10, "limit": 3}},
    {"$project": {"_id": 1, "score": {"$meta": "vectorSearchScore"}}},
]):
    print(" vector", r["_id"], round(r["score"], 4))
print("429 count", stats["429"])
