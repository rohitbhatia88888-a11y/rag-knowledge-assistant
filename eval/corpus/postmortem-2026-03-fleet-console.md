# Postmortem: Fleet Console outage, March 12, 2026

## Summary

On March 12, 2026, the Fleet Console was unavailable for 47 minutes, from 14:05 to 14:52 UTC. Customers could not view the status of their robot fleets or send commands. The incident was classified as SEV1. Priya Raman was the incident commander.

## Impact

All customers using the Fleet Console were affected. Robots kept operating normally, but 312 scheduled missions could not be started from the console during the outage.

## Root cause

The TLS certificate on the internal API gateway gw-internal-2 expired. The job that renews certificates automatically had been failing silently since a configuration migration in February.

## Detection

The outage was first reported by an Enterprise customer through support, 9 minutes after it started. Our alerts did not fire because the gateway health check used plain HTTP instead of HTTPS, so it kept reporting healthy.

## Resolution

The on-call engineer issued a new certificate manually and restarted the gateway, which restored service at 14:52 UTC.

## Action items

| Action | Owner | Due date |
|---|---|---|
| Alert when any certificate is within 30 days of expiry | Platform team | April 15, 2026 |
| Switch gateway health checks to HTTPS | Platform team | March 31, 2026 |
| Alert on failures of the certificate renewal job | SRE team | March 31, 2026 |
