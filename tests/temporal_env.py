import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker


def run_with_worker(body, workflows, activities, time_skipping=True):
    async def main():
        start = WorkflowEnvironment.start_time_skipping if time_skipping else WorkflowEnvironment.start_local
        async with await start(data_converter=pydantic_data_converter) as env:
            queue = f"test-{uuid.uuid4().hex}"
            with ThreadPoolExecutor(8) as pool:
                async with Worker(env.client, task_queue=queue, workflows=workflows, activities=activities, activity_executor=pool):
                    return await body(env, queue)

    return asyncio.run(main())
