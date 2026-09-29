# Approvals and escalation

## Policy summary
In staging, `restart_consumer` is allowed, `scale_consumer` is allowed within 1 to 10 replicas, `pause_pipeline` and `reset_consumer_offset` require approval. In prod, `restart_consumer` and `pause_pipeline` require approval, `scale_consumer` is allowed within 1 to 10 replicas, and `reset_consumer_offset` is denied.

## Approvals
An approver reviews the proposed action, the cited runbook sections and the incident summary. Approval requests expire; an expired request is not executed and must be raised again.

## Escalation
Escalate to the platform on-call when no fleet action addresses the cause, such as broker disk pressure or a sink outage outside the team's control, or when an action was denied and the incident is still customer-impacting.
