import shutil
import time
import uuid
from gax.retrieval import search
from gax.retrieval import store
from gax.retrieval.ingest import IngestActivities, IngestInput
from gax.workflows import IngestWorkflow
from tests.doubles import FakeEmbedderTestDouble
from tests.temporal_env import run_with_worker
from tests.unit.test_chunking import CORPUS


def ingest(mongo, embedder, corpus_dir):
    acts = IngestActivities(mongo, embedder)

    async def body(env, queue):
        return await env.client.execute_workflow(IngestWorkflow.run, IngestInput(corpus_dir=str(corpus_dir), batch_tokens=800),
                                                 id=f"ingest-{uuid.uuid4().hex}", task_queue=queue)

    return run_with_worker(body, [IngestWorkflow], acts.all())


def search_until(mongo, embedder, query, timeout=60):
    deadline = time.time() + timeout
    while True:
        hits = search(mongo, embedder, query, k=3, rerank=False)
        if len(hits) == 3 or time.time() > deadline:
            return hits
        time.sleep(1)


def test_ingest_skips_unchanged_and_search_finds_runbook(mongo, tmp_path):
    mongo[store.CHUNKS].delete_many({})
    mongo[store.CONFIG].delete_many({})
    corpus = tmp_path / "runbooks"
    shutil.copytree(CORPUS, corpus)
    embedder = FakeEmbedderTestDouble()

    first = ingest(mongo, embedder, corpus)
    assert first.embedded == first.total > 0
    assert first.skipped == 0
    assert first.batches > 1
    assert first.model == "fake-embedder-test-double"
    assert mongo[store.CHUNKS].count_documents({"version": "v1"}) == first.total
    doc = mongo[store.CHUNKS].find_one({"_id": "v1:consumer-lag-no-active-members#remediation"})
    assert (doc["model"], doc["dims"], len(doc["hash"])) == ("fake-embedder-test-double", 64, 64)
    assert store.get_active(mongo)["version"] == "v1"

    second = ingest(mongo, embedder, corpus)
    assert (second.embedded, second.skipped, second.deleted, second.batches, second.index) == (0, first.total, 0, 0, "exists")

    path = corpus / "scaling-limits.md"
    path.write_text(path.read_text().replace("12 partitions", "24 partitions"))
    (corpus / "lag-after-deploy.md").unlink()
    third = ingest(mongo, embedder, corpus)
    assert (third.embedded, third.deleted) == (1, 3)
    assert third.skipped == third.total - 1

    hits = search_until(mongo, embedder, "zero active members consumer group restart")
    assert hits[0].chunk_id.startswith("consumer-lag-no-active-members#")
    reranked = search(mongo, embedder, "sink returns errors pause pipeline", k=3, rerank=True)
    assert len(reranked) == 3
    assert all(h.rerank_score is not None for h in reranked)
    assert reranked[0].rerank_score >= reranked[-1].rerank_score
