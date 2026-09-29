from typing import Optional
from pydantic import BaseModel
from pymongo.database import Database
from gax.retrieval.chunking import estimate_tokens
from gax.retrieval.store import get_active, vector_search


class RetrievalNotReady(Exception):
    pass


class SearchHit(BaseModel):
    chunk_id: str
    source: str
    heading: str
    text: str
    vector_score: float
    rerank_score: Optional[float] = None


def search(db: Database, embedder, query: str, k: int = 5, rerank: bool = True, reranker=None) -> list[SearchHit]:
    active = get_active(db)
    if not active:
        raise RetrievalNotReady("retrieval_config has no active version; run ingest first")
    if active["model"] != embedder.embed_model:
        raise RetrievalNotReady(f"active version {active['version']} uses {active['model']}, query embedder is {embedder.embed_model}")
    vector = embedder.embed([query], "query", estimate_tokens(query)).vectors[0]
    candidates = k * 3 if rerank else k
    hits = [SearchHit(chunk_id=d["chunk_id"], source=d["source"], heading=d["heading"], text=d["text"], vector_score=d["score"])
            for d in vector_search(db, vector, active["version"], candidates, max(candidates * 10, 50))]
    if not rerank or not hits:
        return hits[:k]
    reranker = reranker or embedder
    texts = [h.text for h in hits]
    est = estimate_tokens(query) * len(texts) + sum(estimate_tokens(t) for t in texts)
    order = reranker.rerank(query, texts, min(k, len(texts)), est)
    return [hits[i].model_copy(update={"rerank_score": score}) for i, score in order]
