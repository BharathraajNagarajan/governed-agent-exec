# Broker disk pressure

## Symptoms
Broker disk usage is above 85 percent. Producers see request timeouts, replication falls behind and under-replicated partition counts rise. Consumer lag may grow as a side effect.

## Diagnosis
Identify the topics with the largest retention footprint. A consumer that stopped committing can hold back log cleanup on compacted topics. Full disks cause broker failures, and restarting consumers while disks are full only adds load.

## Remediation
None of the four fleet actions frees broker disk. Escalate to the platform on-call to extend storage or reduce retention. If a single pipeline is the main writer, pausing that pipeline with `pause_pipeline` can reduce pressure while storage is added. Do not restart or scale consumers during disk pressure.

## Verification
Broker disk usage below 75 percent and under-replicated partitions at zero.
