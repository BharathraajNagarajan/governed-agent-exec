from datetime import timedelta
import asyncio
from temporalio import activity, workflow
from temporalio.common import RetryPolicy

TASK_QUEUE = "m0-1-hello"


@activity.defn
async def slow_hello(name: str) -> str:
    info = activity.info()
    print(f"activity attempt={info.attempt} start", flush=True)
    await asyncio.sleep(20)
    print(f"activity attempt={info.attempt} done", flush=True)
    return f"hello {name} (attempt {info.attempt})"


@workflow.defn
class HelloWorkflow:
    @workflow.run
    async def run(self, name: str) -> str:
        return await workflow.execute_activity(
            slow_hello,
            name,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=1),
                backoff_coefficient=2.0,
                maximum_interval=timedelta(seconds=10),
                maximum_attempts=5,
            ),
        )
