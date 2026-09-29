from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from gax.retrieval.chunking import batch_by_tokens
    from gax.retrieval.ingest import EmbedBatchInput, IngestActivities, IngestInput, IngestReport, PlanInput, StaleInput

TASK_QUEUE = "gax"
QUICK = dict(start_to_close_timeout=timedelta(seconds=30),
             retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1), maximum_interval=timedelta(seconds=10), maximum_attempts=5))


@workflow.defn
class IngestWorkflow:
    @workflow.run
    async def run(self, inp: IngestInput) -> IngestReport:
        chunks = await workflow.execute_activity_method(IngestActivities.load_corpus, inp.corpus_dir, **QUICK)
        index = await workflow.execute_activity_method(
            IngestActivities.ensure_index, start_to_close_timeout=timedelta(seconds=240),
            retry_policy=RetryPolicy(initial_interval=timedelta(seconds=2), maximum_attempts=3))
        plan = await workflow.execute_activity_method(IngestActivities.plan_ingest, PlanInput(version=inp.version, chunks=chunks), **QUICK)
        batches = batch_by_tokens(plan.to_embed, inp.batch_tokens)
        embedded = 0
        for batch in batches:
            embedded += await workflow.execute_activity_method(
                IngestActivities.embed_batch, EmbedBatchInput(version=inp.version, chunks=batch),
                start_to_close_timeout=timedelta(minutes=5), heartbeat_timeout=timedelta(seconds=90),
                retry_policy=RetryPolicy(initial_interval=timedelta(seconds=20), backoff_coefficient=2.0,
                                         maximum_interval=timedelta(seconds=60), maximum_attempts=4,
                                         non_retryable_error_types=["VoyageAuthError", "VoyageRequestError"]))
        deleted = await workflow.execute_activity_method(IngestActivities.delete_stale, StaleInput(version=inp.version, chunk_ids=plan.stale), **QUICK)
        active = await workflow.execute_activity_method(IngestActivities.activate, inp.version, **QUICK)
        return IngestReport(version=inp.version, model=active["model"], total=len(chunks), embedded=embedded, skipped=plan.unchanged,
                            deleted=deleted, batches=len(batches), index=index)
