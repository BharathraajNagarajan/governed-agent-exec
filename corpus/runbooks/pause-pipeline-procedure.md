# Pause pipeline procedure

## What a pause does
`pause_pipeline` stops the consumer from fetching new records while keeping its group membership and committed offsets. Data accumulates on the brokers as lag.

## Idempotency
Pausing an already paused pipeline has no further effect.

## Approval
Requires approval in staging and prod because it stops data delivery to downstream consumers of the sink.

## Resume
Resuming is a manual step after the cause is fixed. Watch broker retention: a pipeline paused longer than the topic retention period loses data.
