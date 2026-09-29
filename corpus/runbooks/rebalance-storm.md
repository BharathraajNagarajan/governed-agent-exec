# Rebalance storm

## Symptoms
The consumer group rebalances every few seconds. Members join and leave repeatedly, throughput drops to near zero and lag grows on every partition.

## Diagnosis
Common causes are processing time exceeding the poll interval, too many replicas joining at once after a scale-up, or an unstable network between consumers and the coordinator.

## Remediation
Do not restart the consumer; a restart triggers another rebalance. If the storm started after a scale-up, scale the consumer back down with `scale_consumer` to the previous replica count. If processing time exceeds the poll interval, escalate to the owning team to tune batch size.

## Verification
The group reaches `Stable` and stays there for ten minutes.
