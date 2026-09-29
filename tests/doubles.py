import hashlib
import math
import re
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
