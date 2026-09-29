# Scaling limits

## Replica bounds
Consumers run between 1 and 10 replicas. The policy denies `scale_consumer` requests outside that range in every environment. Zero replicas is not a way to stop a consumer; use `pause_pipeline` instead.

## Partitions
Parallelism is capped by the partition count. The orders topic has 12 partitions and the payments topic has 6. Replicas beyond the partition count stay idle.

## Scaling down
Scale down when lag has stayed near zero for an hour and CPU is below 30 percent. Scale in steps of one or two replicas to avoid rebalance storms.

## Verification
The consumer reports the requested `replicas` and all replicas hold partition assignments.
