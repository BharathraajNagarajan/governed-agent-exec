# Sink schema mismatch

## Symptoms
The sink rejects records with schema or type errors after an upstream producer deployed a new record version. Only records from the new producer version fail.

## Diagnosis
Compare the record schema version in the failing records with the sink table schema. A missing column or a changed type is the usual cause. Restarting or scaling the consumer does not help because every retry fails the same way.

## Remediation
Pause the pipeline with `pause_pipeline` while the schema is fixed, either by migrating the sink table or rolling back the producer. Do not reset offsets to skip the failing records; they are valid data that must be loaded once the schema is fixed.

## Verification
After the schema fix and resume, sink error rate returns to zero and lag drains.
