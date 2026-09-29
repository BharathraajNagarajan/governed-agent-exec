# Consumer lag with saturated consumers

## Symptoms
Lag grows on all partitions while every consumer replica is active and assigned. CPU on the consumer replicas is above 90 percent and processing time per batch is rising.

## Diagnosis
The group is healthy but under-provisioned for the current input rate. This is common after a traffic increase or a new, slower transformation. A restart will not help because the consumers are working, just not fast enough.

## Remediation
Scale the consumer out with `scale_consumer` and set `params.replicas` to the new replica count. The policy only allows between 1 and 10 replicas. Do not scale above the topic partition count; extra replicas stay idle. Scaling is idempotent: repeating the same request leaves the same replica count.

## Verification
The consumer's `replicas` value equals the requested count and lag stops growing within ten minutes.
