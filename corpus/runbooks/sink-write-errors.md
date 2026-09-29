# Sink write errors

## Symptoms
The pipeline's sink returns errors: HTTP 5xx from a warehouse loader, connection refused from a database, or throttling responses. Consumers retry, lag grows, and dead-letter volume rises.

## Diagnosis
Check the sink's own health dashboard and recent deploys. If the sink is down or rejecting writes, continuing to consume produces partial writes, retries that amplify load on the sink, and dead-letter noise.

## Remediation
Pause the pipeline with `pause_pipeline` until the sink recovers. Pausing stops consumption without moving offsets, so no data is lost; the backlog is processed after resume. Pausing is idempotent and requires approval in both staging and prod because it stops data delivery.

## Verification
The pipeline shows `paused` true and dead-letter volume stops growing.
