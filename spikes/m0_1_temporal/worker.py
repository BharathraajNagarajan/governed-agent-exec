import asyncio
import os
from temporalio.client import Client
from temporalio.worker import Worker
from shared import TASK_QUEUE, HelloWorkflow, slow_hello


async def main():
    client = await Client.connect("localhost:7233")
    print(f"worker pid={os.getpid()}", flush=True)
    await Worker(client, task_queue=TASK_QUEUE, workflows=[HelloWorkflow], activities=[slow_hello]).run()


asyncio.run(main())
