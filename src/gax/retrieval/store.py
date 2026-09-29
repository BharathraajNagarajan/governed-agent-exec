import time
from typing import Optional
from pymongo import UpdateOne
from pymongo.database import Database
from pymongo.operations import SearchIndexModel
from gax.models import utcnow
from gax.retrieval.chunking import Chunk

CHUNKS = "runbook_chunks"
CONFIG = "retrieval_config"
INDEX = "runbook_chunks_vec"
ACTIVE = "active"
DEFAULT_VERSION = "v1"


def index_definition(dims: int) -> dict:
    return {"fields": [
        {"type": "vector", "path": "embedding", "numDimensions": dims, "similarity": "cosine"},
        {"type": "filter", "path": "version"},
    ]}


def _index_dims(idx: dict) -> Optional[int]:
    fields = idx.get("latestDefinition", {}).get("fields", [])
    return next((f.get("numDimensions") for f in fields if f.get("type") == "vector"), None)


def ensure_vector_index(db: Database, dims: int, timeout: float = 180.0) -> str:
    if CHUNKS not in db.list_collection_names():
        db.create_collection(CHUNKS)
    coll = db[CHUNKS]
    existing = list(coll.list_search_indexes(INDEX))
    if not existing:
        coll.create_search_index(SearchIndexModel(name=INDEX, type="vectorSearch", definition=index_definition(dims)))
        outcome = "created"
    elif _index_dims(existing[0]) != dims:
        coll.update_search_index(INDEX, index_definition(dims))
        outcome = "updated"
    else:
        outcome = "exists"
    deadline = time.time() + timeout
    while True:
        idx = next(iter(coll.list_search_indexes(INDEX)), {})
        if idx.get("queryable") and idx.get("status") == "READY" and _index_dims(idx) == dims:
            return outcome
        if time.time() > deadline:
            raise TimeoutError(f"search index {INDEX} not queryable: status={idx.get('status')}")
        time.sleep(1)


def existing_chunks(db: Database, version: str) -> dict[str, dict]:
    return {d["chunk_id"]: d for d in db[CHUNKS].find({"version": version}, {"chunk_id": 1, "hash": 1, "model": 1})}


def upsert_chunks(db: Database, version: str, chunks: list[Chunk], vectors: list[list[float]], model: str) -> int:
    now = utcnow()
    ops = [
        UpdateOne({"_id": f"{version}:{c.chunk_id}"}, {"$set": {
            **c.model_dump(), "version": version, "embedding": v, "model": model, "dims": len(v), "updated_at": now}}, upsert=True)
        for c, v in zip(chunks, vectors, strict=True)
    ]
    if ops:
        db[CHUNKS].bulk_write(ops)
    return len(ops)


def delete_chunks(db: Database, version: str, chunk_ids: list[str]) -> int:
    if not chunk_ids:
        return 0
    return db[CHUNKS].delete_many({"version": version, "chunk_id": {"$in": chunk_ids}}).deleted_count


def set_active(db: Database, version: str, model: str, dims: int) -> dict:
    doc = {"version": version, "model": model, "dims": dims, "index": INDEX, "updated_at": utcnow()}
    db[CONFIG].replace_one({"_id": ACTIVE}, doc, upsert=True)
    return doc


def get_active(db: Database) -> Optional[dict]:
    return db[CONFIG].find_one({"_id": ACTIVE})


def vector_search(db: Database, vector: list[float], version: str, limit: int, num_candidates: int) -> list[dict]:
    return list(db[CHUNKS].aggregate([
        {"$vectorSearch": {"index": INDEX, "path": "embedding", "queryVector": vector, "numCandidates": num_candidates,
                           "limit": limit, "filter": {"version": version}}},
        {"$project": {"_id": 0, "chunk_id": 1, "source": 1, "heading": 1, "text": 1, "score": {"$meta": "vectorSearchScore"}}},
    ]))
