import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import anthropic
import httpx
from pymongo import MongoClient
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker
from gax.config import get_settings
from gax.credentials import build_broker
from gax.credentials.local_only_broker import GRANTS_COLLECTION, MongoGrantStore
from gax.llm import propose
from gax.remediation.activities import RemediationActivities
from gax.retrieval import search
from gax.retrieval.ingest import IngestActivities
from gax.retrieval.voyage import VoyageClient
from gax.workflows import TASK_QUEUE, IngestWorkflow, RemediationWorkflow


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = get_settings()
    client = await Client.connect(s.temporal_address, data_converter=pydantic_data_converter)
    db = MongoClient(s.mongo_uri, serverSelectionTimeoutMS=5000)[s.gax_db]
    voyage = VoyageClient(s.voyage_base_url, s.voyage_api_key)
    broker = build_broker(s, store=MongoGrantStore(db[GRANTS_COLLECTION]))
    llm = anthropic.Anthropic(api_key=s.anthropic_api_key.get_secret_value())
    remediation = RemediationActivities(
        db, broker, httpx.Client(base_url=s.fleet_api_url, timeout=10), partial(propose, llm, s.anthropic_model),
        lambda query, k, rerank: search(db, voyage, query, k=k, rerank=rerank))
    activities = IngestActivities(db, voyage).all() + remediation.all()
    print(f"worker pid={os.getpid()} task_queue={TASK_QUEUE} credential_mode={broker.mode} model={s.anthropic_model}", flush=True)
    with ThreadPoolExecutor(16) as pool:
        await Worker(client, task_queue=TASK_QUEUE, workflows=[IngestWorkflow, RemediationWorkflow], activities=activities,
                     activity_executor=pool).run()


if __name__ == "__main__":
    asyncio.run(main())
