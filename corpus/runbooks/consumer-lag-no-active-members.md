# Consumer lag with no active members

## Symptoms
Consumer lag on the topic grows steadily. The consumer group reports zero active members, or members that joined and never received a partition assignment. Throughput for the group is zero while producers keep writing.

## Diagnosis
Check the group state. A group in `Empty` or `Dead` state with committed offsets means the consumer processes stopped polling, usually after an unhandled exception, a stuck heartbeat thread or a lost connection to the coordinator. Confirm the brokers are healthy and broker disk is below 85 percent before acting, otherwise a restart will not help.

## Remediation
Restart the consumer with `restart_consumer`. The restart rejoins the group, triggers a rebalance and resumes from the last committed offset, so no data is skipped or replayed beyond the uncommitted batch. In staging this is allowed without approval. In prod it requires approval.

## Verification
The consumer's `restart_count` increases by exactly one, the group shows active members again and lag starts to fall within five minutes.
