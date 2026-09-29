import asyncio
import sys
from temporalio.client import Client
from shared import TASK_QUEUE, HelloWorkflow


async def main():
    client = await Client.connect("localhost:7233")
    handle = await client.start_workflow(HelloWorkflow.run, "m0", id=sys.argv[1], task_queue=TASK_QUEUE)
    print(f"started {handle.id} run_id={handle.result_run_id}", flush=True)


asyncio.run(main())
