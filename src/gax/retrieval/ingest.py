from pydantic import BaseModel
from pymongo.database import Database
from temporalio import activity
from gax.retrieval import store
from gax.retrieval.chunking import Chunk, estimate_tokens, load_corpus


class IngestInput(BaseModel):
    corpus_dir: str
    version: str = store.DEFAULT_VERSION
    batch_tokens: int = 3000


class PlanInput(BaseModel):
    version: str
    chunks: list[Chunk]


class IngestPlan(BaseModel):
    to_embed: list[Chunk]
    unchanged: int
    stale: list[str]


class EmbedBatchInput(BaseModel):
    version: str
    chunks: list[Chunk]


class StaleInput(BaseModel):
    version: str
    chunk_ids: list[str]


class IngestReport(BaseModel):
    version: str
    model: str
    total: int
    embedded: int
    skipped: int
    deleted: int
    batches: int
    index: str


class IngestActivities:
    def __init__(self, db: Database, embedder):
        self.db = db
        self.embedder = embedder

    @activity.defn
    def load_corpus(self, corpus_dir: str) -> list[Chunk]:
        return load_corpus(corpus_dir)

    @activity.defn
    def ensure_index(self) -> str:
        return store.ensure_vector_index(self.db, self.embedder.dims)

    @activity.defn
    def plan_ingest(self, inp: PlanInput) -> IngestPlan:
        existing = store.existing_chunks(self.db, inp.version)
        model = self.embedder.embed_model
        to_embed = [c for c in inp.chunks if (e := existing.get(c.chunk_id)) is None or e["hash"] != c.hash or e["model"] != model]
        current = {c.chunk_id for c in inp.chunks}
        return IngestPlan(to_embed=to_embed, unchanged=len(inp.chunks) - len(to_embed), stale=sorted(set(existing) - current))

    @activity.defn
    def embed_batch(self, inp: EmbedBatchInput) -> int:
        texts = [c.text for c in inp.chunks]
        result = self.embedder.embed(texts, "document", sum(estimate_tokens(t) for t in texts),
                                     on_wait=lambda s: activity.heartbeat(f"waiting {s:.1f}s for Voyage rate limit"))
        activity.logger.info("embedded %d chunks tokens=%d model=%s", len(texts), result.total_tokens, result.model)
        return store.upsert_chunks(self.db, inp.version, inp.chunks, result.vectors, self.embedder.embed_model)

    @activity.defn
    def delete_stale(self, inp: StaleInput) -> int:
        return store.delete_chunks(self.db, inp.version, inp.chunk_ids)

    @activity.defn
    def activate(self, version: str) -> dict:
        doc = store.set_active(self.db, version, self.embedder.embed_model, self.embedder.dims)
        return {k: v for k, v in doc.items() if k != "updated_at"}
