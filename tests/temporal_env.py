import asyncio
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

TEMPORAL_CLI = os.environ.get("TEMPORAL_CLI", r"C:\Users\bhara\tools\temporal\temporal.exe")


def run_with_worker(body, workflows, activities):
    async def main():
        existing = TEMPORAL_CLI if Path(TEMPORAL_CLI).exists() else None
        async with await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter, dev_server_existing_path=existing) as env:
            queue = f"test-{uuid.uuid4().hex}"
            with ThreadPoolExecutor(8) as pool:
                async with Worker(env.client, task_queue=queue, workflows=workflows, activities=activities, activity_executor=pool):
                    return await body(env, queue)

    return asyncio.run(main())
