# Runbook: stuck outbox rows

## Symptom

`outbox_unpublished_age_seconds` alerting above 300.

## Triage

1. Is the broker reachable from the relay? Check relay logs for connect
   errors before assuming a data problem.
2. Is one row poisoning the batch? The relay publishes in order per
   partition key, so a single oversized payload blocks everything behind
   it for that `orderId`.
3. Has the relay simply stopped? Check liveness before touching data.

## Resolution

- Broker unreachable: no action on the data. The relay drains on its own
  once connectivity returns. Do not manually publish.
- Poison row: move it to `outbox_quarantine`, let the batch drain, then
  investigate the payload. Never delete outright — finance reconciles
  against it.

## Do not

Do not republish from the outbox by hand while the relay is running.
You will produce duplicates, and while consumers are idempotent, the
duplicate lands in the audit trail.
