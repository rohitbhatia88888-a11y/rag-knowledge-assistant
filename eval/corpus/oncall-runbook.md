# Engineering On-Call Runbook

This runbook covers how engineering responds to production incidents.

## Severity levels

| Severity | Definition | Response time | Status update cadence |
|---|---|---|---|
| SEV1 | Full outage or data loss affecting customers | 15 minutes | Every 30 minutes |
| SEV2 | Major feature degraded for many customers | 30 minutes | Every 60 minutes |
| SEV3 | Minor degradation with a workaround | 4 business hours | Daily |
| SEV4 | Cosmetic or internal-only issue | Next sprint | None |

## Rotation

Each team runs a weekly rotation with a primary and a secondary on-call engineer. Handoff happens every Monday at 10:00 Pacific time.

## Paging and escalation

Pages are sent through PagerDuty. If the primary does not acknowledge a page within 10 minutes, it escalates to the secondary. If it is still unacknowledged after 20 minutes, it escalates to the engineering manager.

## Running an incident

Every SEV1 and SEV2 has an incident commander, who is the first responder until they hand the role over. Open a Slack channel named #inc-date-short-description. For a SEV1, the public status page must be updated within 30 minutes of the incident starting.

## On-call compensation

Primary on-call engineers receive 500 dollars for each week on call, and secondary on-call engineers receive 250 dollars. If you are paged outside working hours more than three times in one week, you also get one day off in lieu.

## Postmortems

A blameless postmortem is required for every SEV1 and SEV2. It must be published within 5 business days of the incident being resolved.
