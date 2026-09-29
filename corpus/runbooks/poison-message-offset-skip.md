# Skipping a poison message by resetting offsets

## When to use
Only when a single record or a small, known range blocks a partition, the record has been captured for later analysis, and the data owner accepts that the skipped records will not be processed by this consumer.

## Risks
`reset_consumer_offset` moves the committed offset. Moving it forward skips data; moving it back replays data and can create duplicates in non-idempotent sinks. The action is destructive and cannot be undone by another reset without knowing the original offset.

## Policy
In staging, `reset_consumer_offset` requires human approval. In prod it is denied by policy: an automated system must never reset prod offsets. Prod resets are done by the data owner through the manual change process.

## Verification
The consumer's `offset` equals the target offset and the consumer processes records after it.
