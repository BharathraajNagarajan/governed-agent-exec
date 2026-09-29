# Lag after a consumer deploy

## Symptoms
Lag starts growing within minutes of a consumer deployment. Error logs may show a new exception type, or processing time per record doubled.

## Diagnosis
If the new version crashes on start, members leave the group; see the no-active-members runbook. If the new version is slower, the group is under-provisioned; see the CPU saturation runbook.

## Remediation
Roll back the deployment through the normal release process when the new version is faulty. As a short-term measure for a slower version, `scale_consumer` within the 1 to 10 replica bound. A restart only helps when members are stuck after the deploy.
