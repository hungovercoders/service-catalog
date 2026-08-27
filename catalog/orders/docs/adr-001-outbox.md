# ADR-001: Order placement writes through an outbox

**Status:** accepted · **Date:** 2026-03-11

## Context

Placing an order must both persist the Order aggregate and emit
`OrderPlaced`. Doing these as two independent operations means either a
lost event (order saved, publish failed) or a phantom event (publish
succeeded, transaction rolled back). Neither is acceptable — finance
reconciles on the event stream.

## Decision

Placement writes the order row and an outbox row in a single database
transaction. A separate relay publishes unpublished outbox rows to the
broker and marks them sent.

Consumers therefore see at-least-once delivery and must be idempotent on
`order_id`. This is stated in the AsyncAPI description and asserted in
`features/place-order.feature`.

## Consequences

- Publication is asynchronous. A 201 does not mean the event is on the
  broker yet, only that it will be.
- The relay is a piece of infrastructure that can fall behind. See
  `docs/runbook.md` for stuck-row procedures.
- We cannot use broker-assigned ordering as a sequencing guarantee.
