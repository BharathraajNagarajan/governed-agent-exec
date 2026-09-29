# Consumer crash loop

## Symptoms
The consumer restarts repeatedly. Logs show the same exception on the same partition and offset after every start. Lag grows only on that partition while other partitions keep moving.

## Diagnosis
A crash loop pinned to one offset usually means a poison message: a record the consumer cannot deserialize or process. Restarting the consumer again will replay the same record and crash again. A crash loop across all partitions points to configuration or dependency failures instead, such as an unreachable sink.

## Remediation
Do not use `restart_consumer` for a poison-message crash loop. If the sink or downstream system is failing, pause the pipeline with `pause_pipeline`. If a single poison record must be skipped, see the offset reset runbook; `reset_consumer_offset` is destructive and needs approval.

## Verification
The consumer stays up for at least ten minutes and the affected partition makes progress.
