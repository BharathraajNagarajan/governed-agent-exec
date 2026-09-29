import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pymongo import MongoClient
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker
from gax.config import get_settings
from gax.retrieval.ingest import IngestActivities
from gax.retrieval.voyage import VoyageClient
from gax.workflows import TASK_QUEUE, IngestWorkflow


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = get_settings()
    client = await Client.connect(s.temporal_address, data_converter=pydantic_data_converter)
    db = MongoClient(s.mongo_uri, serverSelectionTimeoutMS=5000)[s.gax_db]
    voyage = VoyageClient(s.voyage_base_url, s.voyage_api_key)
    ingest = IngestActivities(db, voyage)
    activities = [ingest.load_corpus, ingest.ensure_index, ingest.plan_ingest, ingest.embed_batch, ingest.delete_stale, ingest.activate]
    print(f"worker pid={os.getpid()} task_queue={TASK_QUEUE}", flush=True)
    with ThreadPoolExecutor(16) as pool:
        await Worker(client, task_queue=TASK_QUEUE, workflows=[IngestWorkflow], activities=activities, activity_executor=pool).run()


if __name__ == "__main__":
    asyncio.run(main())
