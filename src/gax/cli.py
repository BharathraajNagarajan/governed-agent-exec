import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from pymongo import MongoClient
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from gax.config import get_settings
from gax.retrieval import search
from gax.retrieval.ingest import IngestInput
from gax.retrieval.voyage import VoyageClient
from gax.workflows import TASK_QUEUE, IngestWorkflow

ROOT = Path(__file__).resolve().parents[2]


def out(value) -> None:
    print(json.dumps(value, indent=2, default=str))


def db(s):
    return MongoClient(s.mongo_uri, serverSelectionTimeoutMS=5000)[s.gax_db]


async def temporal(s) -> Client:
    return await Client.connect(s.temporal_address, data_converter=pydantic_data_converter)


async def cmd_ingest(s, a):
    client = await temporal(s)
    wid = f"ingest-{a.version}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    print(f"started {wid}", flush=True)
    report = await client.execute_workflow(IngestWorkflow.run, IngestInput(corpus_dir=str(Path(a.corpus).resolve()), version=a.version),
                                           id=wid, task_queue=TASK_QUEUE)
    out(report.model_dump())


async def cmd_search(s, a):
    voyage = VoyageClient(s.voyage_base_url, s.voyage_api_key)
    hits = search(db(s), voyage, a.query, k=a.k, rerank=not a.no_rerank)
    for i, h in enumerate(hits, 1):
        rr = f" rerank={h.rerank_score:.4f}" if h.rerank_score is not None else ""
        print(f"{i}. {h.chunk_id} vector={h.vector_score:.4f}{rr}\n   {h.text.splitlines()[1][:140]}")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gax")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("ingest")
    c.add_argument("--corpus", default=str(ROOT / "corpus" / "runbooks"))
    c.add_argument("--version", default="v1")
    c.set_defaults(fn=cmd_ingest)
    c = sub.add_parser("search")
    c.add_argument("query")
    c.add_argument("-k", type=int, default=5)
    c.add_argument("--no-rerank", action="store_true")
    c.set_defaults(fn=cmd_search)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    result = asyncio.run(args.fn(get_settings(), args))
    return result or 0


if __name__ == "__main__":
    sys.exit(main())
