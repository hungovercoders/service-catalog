---
name: consume-service
description: Build a consumer of a catalog service — a UI, client app, or downstream system that calls its API or subscribes to its events — against mocks, before or without the real service existing. Use when asked to build a frontend or client against orders or payments, integrate with a service's API or events from outside, generate a client from the contracts, or develop against mock data.
---

# Consume a catalog service

You are building something that *uses* a service — you implement none of its
contracts, you rely on them. The mock stack stands in for the real service,
so the consumer can be built and verified end-to-end before an
implementation exists. Every path below is parameterized by the consumed
service's name; a consumer may pin more than one.

## Phase 0 — locate the contracts and mocks

- **Interactive sessions**: the `catalog` MCP tools. `get_service(<name>)`
  for the surface, `get_acceptance_criteria(<name>)` for what the service
  guarantees, `get_message_schema(<name>, <Message>)` for event shapes,
  `trace_channel(<address>)` for who else is on a channel.
- **CI and anything needing reproducible paths**: pin a released surface in
  `contracts.lock`, exactly as an implementation repo does (see
  `skills/implement-service/SKILL.md` phase 0). The consumer's
  `contracts:fetch` differs in one line — `mocks` joins the sparse
  checkout, so the mock stack itself is versioned by the pin:

  ```yaml
  contracts:fetch:
    desc: Fetch the pinned contract surface and mock stack into read-only .contracts/
    cmds:
      - |
        sha=$(awk '/^sha:/{print $2}' contracts.lock)
        chmod -R u+w .contracts 2>/dev/null || true
        rm -rf .contracts && git init -q .contracts
        git -C .contracts remote add origin https://github.com/hungovercoders/service-catalog
        git -C .contracts sparse-checkout set catalog/<service> mocks
        git -C .contracts fetch -q --depth 1 origin "$sha"
        git -C .contracts checkout -q FETCH_HEAD
        chmod -R a-w .contracts/catalog
  ```

  Root files (`Taskfile.yml`) ride along with a cone-mode sparse checkout,
  so the catalog's own mock tasks run from the pin — no copies:

  ```sh
  task -d .contracts mocks:load SERVICE=<service>   # start stack, load pinned specs + examples
  task -d .contracts mocks:down
  ```

## Ground rules

- The contracts bind the *service*; you inherit them read-only. Never edit
  anything under `.contracts/` — if a contract blocks you, that is a catalog
  change to propose, not a local fix.
- The feature files are the behavioural contract to **rely on**, not
  scenarios to bind — the service's implementers own the step definitions.
  Read them to learn what a 409 means, when an order becomes `paid`, what
  delivery guarantees hold; test *your* behaviour on top of those promises.
- Generate client types from the fetched specs; never hand-model a payload
  from memory or from observed mock traffic.
- Honour the event conventions the features state: dedupe on the CloudEvents
  envelope `id`, expect at-least-once delivery, assume ordering only within
  an aggregate, and tolerate additive change — a minor bump must never break
  you.

## Phase 1 — interview

1. **Which service(s)** are consumed, if not already stated.
2. **What kind of consumer** — UI, service, batch job, agent.
3. **Language and framework**, and the type-generation tool that fits it.
4. **Event transport in production** — mocks emit over WebSocket; the real
   subscription (Kafka, MQTT, AMQP, SSE…) is the consumer's choice and only
   changes the adapter, never the envelope handling.
5. **Where the consumer lives** — its own repository, as with
   implementations.

Do **not** interview about anything the contract decides: endpoints, status
codes, payload shapes, event semantics.

## Phase 2 — build against the mocks

- `task -d .contracts mocks:load SERVICE=<service>`, then point the client at
  the pinned mocks (title and version come from the spec's `info` block):
  - REST: `http://localhost:8585/rest/<Title>/<version>/...` — fixture data
    included, so list/detail screens render real-looking aggregates.
  - Events: `ws://localhost:8081/api/ws/<Title>/<version>/<operation>` — the
    async-minion emits the example CloudEvents on a schedule;
    `task -d .contracts mocks:watch CHANNEL=<Title>/<version>/<operation>`
    to eyeball them.
- Generate types from `.contracts/catalog/<service>/openapi/*.yaml` and the
  message payload schemas in the AsyncAPI file; wire the client through
  them.
- A UI built this way is demonstrable — with data — before any backend
  exists.

## Phase 3 — verify (the consumer's definition of done)

The suite must run headlessly against the mock stack (this is also the
`contracts:verify` task the sync loop calls):

1. **Client flows against the REST mocks** — every call the consumer makes,
   exercised against Microcks, responses parsed through the generated types.
2. **Event handling against real envelopes** — feed the handler from the WS
   mock or directly from `mocks/<service>.events.examples.yaml`, and
   validate each consumed payload against the AsyncAPI schema before acting
   on it.
3. **Idempotence** — replaying the same envelope `id` must not double-apply;
   the features promise at-least-once delivery, so this is contract, not
   hygiene.

Green here proves the consumer satisfies the pinned surface's examples and
schemas — the lock records exactly which surface that was. It does not prove
the real service behaves; that is the *service's* verification loop.

## Phase 4 — wire the consumer's CI

mise-action → `task contracts:fetch` → `task -d .contracts mocks:load
SERVICE=<service>` → the phase 3 suite → `task -d .contracts mocks:down`.
Everything resolves against `.contracts/`, so CI verifies exactly the
surface the lock names.

## Phase 5 — stay current

Identical machinery to implementations: the catalog publishes
`<service>/v<version>` tags, Renovate bumps `contracts.lock`, and the
templates in `skills/implement-service/templates/` (`renovate.json`,
`contract-converge.yml`) drive the loop — copy them in and substitute the
service name. Define `contracts:verify` as the phase 3 suite. Additive
minors go green and auto-merge — new mocks, new fixtures, no human. A red
run or a major bump means the surface moved under you, and only then does an
agent wake to converge the consumer, with the failing suite as its scope.
