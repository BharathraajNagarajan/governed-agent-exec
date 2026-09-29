from pathlib import Path
from gax.retrieval.chunking import batch_by_tokens, chunk_markdown, estimate_tokens, load_corpus, sha256

CORPUS = Path(__file__).resolve().parents[2] / "corpus" / "runbooks"
DOC = "# Title\n\nintro ignored\n\n## Symptoms\nlag grows\n\n## Fix It!\nrestart\n\n## Symptoms\nsecond\n\n## Empty\n\n"


def test_chunk_ids_hashes_and_text():
    chunks = chunk_markdown("rb", DOC)
    assert [c.chunk_id for c in chunks] == ["rb#symptoms", "rb#fix-it", "rb#symptoms-2"]
    assert chunks[0].text == "Title / Symptoms\nlag grows"
    assert chunks[0].hash == sha256(chunks[0].text)
    assert chunks[0].source == "rb.md"


def test_ids_and_hashes_stable_and_hash_tracks_content():
    a, b = chunk_markdown("rb", DOC), chunk_markdown("rb", DOC.replace("\n", "\r\n"))
    assert a == b
    changed = chunk_markdown("rb", DOC.replace("restart", "restart twice"))
    assert [c.chunk_id for c in changed] == [c.chunk_id for c in a]
    assert [c.hash == d.hash for c, d in zip(a, changed)] == [True, False, True]


def test_real_corpus_covers_domain():
    files = sorted(CORPUS.glob("*.md"))
    assert 12 <= len(files) <= 15
    chunks = load_corpus(CORPUS)
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    text = " ".join(c.text for c in chunks).lower()
    for term in ("restart_consumer", "scale_consumer", "pause_pipeline", "reset_consumer_offset",
                 "consumer lag", "crash loop", "sink", "offset", "broker disk"):
        assert term in text


def test_batches_respect_token_budget():
    chunks = load_corpus(CORPUS)
    batches = batch_by_tokens(chunks, 1000)
    assert [c for b in batches for c in b] == chunks
    for b in batches:
        assert len(b) == 1 or sum(estimate_tokens(c.text) for c in b) <= 1000
    assert len(batch_by_tokens(chunks, 10**9, max_items=5)) == -(-len(chunks) // 5)
