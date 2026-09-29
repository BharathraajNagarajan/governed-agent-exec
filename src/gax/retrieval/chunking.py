import hashlib
import re
from pathlib import Path
from pydantic import BaseModel, ConfigDict

CHARS_PER_TOKEN_ESTIMATE = 3


class Chunk(BaseModel):
    model_config = ConfigDict(frozen=True)
    chunk_id: str
    source: str
    title: str
    heading: str
    text: str
    hash: str


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN_ESTIMATE + 1


def chunk_markdown(stem: str, markdown: str) -> list[Chunk]:
    lines = markdown.replace("\r\n", "\n").split("\n")
    title = next((l[2:].strip() for l in lines if l.startswith("# ")), stem)
    sections: list[tuple[str, list[str]]] = []
    for line in lines:
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif sections:
            sections[-1][1].append(line)
    chunks, seen = [], {}
    for heading, body in sections:
        content = "\n".join(body).strip()
        if not content:
            continue
        slug = slugify(heading)
        seen[slug] = seen.get(slug, 0) + 1
        if seen[slug] > 1:
            slug = f"{slug}-{seen[slug]}"
        text = f"{title} / {heading}\n{content}"
        chunks.append(Chunk(chunk_id=f"{stem}#{slug}", source=f"{stem}.md", title=title, heading=heading, text=text, hash=sha256(text)))
    return chunks


def load_corpus(directory: str | Path) -> list[Chunk]:
    paths = sorted(Path(directory).glob("*.md"))
    if not paths:
        raise FileNotFoundError(f"no markdown runbooks in {directory}")
    return [c for p in paths for c in chunk_markdown(p.stem, p.read_text(encoding="utf-8"))]


def batch_by_tokens(chunks: list[Chunk], max_tokens: int, max_items: int = 128) -> list[list[Chunk]]:
    batches, current, used = [], [], 0
    for c in chunks:
        cost = estimate_tokens(c.text)
        if current and (used + cost > max_tokens or len(current) >= max_items):
            batches.append(current)
            current, used = [], 0
        current.append(c)
        used += cost
    if current:
        batches.append(current)
    return batches
