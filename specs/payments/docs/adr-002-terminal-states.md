# ADR-002: Only PaymentSettled is terminal

**Status:** accepted · **Date:** 2026-04-02

## Context

Downstream services repeatedly treated authorisation as "payment done"
and released goods. Authorisations reverse — for fraud holds, for
processor timeouts, and at the issuer's discretion — sometimes days later.

## Decision

We publish only `PaymentSettled` on the public channel. Authorisation is
an internal state and is not exposed as an event.

Anything needing to know that money moved waits for settlement. Anything
needing a faster signal for UX purposes queries the payments API and is
explicitly told the answer is provisional.

## Consequences

- Order fulfilment is slower by the settlement window.
- We accept that tradeoff. The alternative was releasing goods against
  money that later vanished, which we did twice in Q1.
- If a future consumer genuinely needs authorisation, it gets its own
  channel with a name that makes the provisionality obvious.
