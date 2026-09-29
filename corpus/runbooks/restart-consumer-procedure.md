# Restart consumer procedure

## What a restart does
`restart_consumer` stops and starts the consumer processes. The group rebalances and consumption resumes from committed offsets. The fleet increments the consumer's `restart_count` once per restart.

## Idempotency
A restart is not idempotent: each call causes another rebalance and another restart. Automated callers must send an `Idempotency-Key` so that a retried request after a timeout does not restart the consumer twice.

## Approval
Allowed without approval in staging. Requires approval in prod because a rebalance pauses all partitions of the group.

## When not to restart
Do not restart for poison-message crash loops, sink failures, schema mismatches or broker disk pressure. A restart will not change the outcome and adds a rebalance.
