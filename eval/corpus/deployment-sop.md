# Deployment Standard Operating Procedure

## Pipeline

All services deploy through the Shipyard pipeline. Every merge to the main branch deploys automatically to staging. A production deploy needs approval from one reviewer and a green CI run.

## Deploy windows

Production deploys are allowed Monday to Thursday, 09:00 to 16:00 Pacific time. There are no Friday deploys, except hotfixes approved by an engineering manager.

## Code freezes

There is an annual code freeze from December 15 to January 5. Teams also freeze production deploys for 48 hours before a major product launch.

## Canary releases

Every production deploy starts as a canary on 5 percent of traffic for 30 minutes. The deploy rolls back automatically if the error rate goes above 2 percent or if p95 latency increases by more than 50 percent.

## Rollback

Roll back a service with `shipyard rollback <service>`. Database migrations must be backward compatible, using the expand and contract pattern, so that any deploy can be rolled back safely.

## Feature flags

Use Flagpole for feature flags. Flags older than 90 days must be removed from the code.
