# Offset management basics

## Committed offsets
Each consumer group commits the offset of the next record to read per partition. On restart or rebalance, consumption resumes from the committed offset. Offsets are the only record of progress.

## Safe operations
Restarting a consumer, scaling it and pausing its pipeline all preserve committed offsets. They can delay processing but never skip or replay records beyond the last uncommitted batch.

## Destructive operations
Resetting an offset changes what the group will read next. It is the only fleet action that can lose or duplicate data, which is why `reset_consumer_offset` needs approval in staging and is denied in prod.

## Before any reset
Record the current committed offset, the target offset and the reason. Confirm the data owner accepts the loss or replay.
