---
name: service-catalog
description: Use when working against any service in the catalog — implementing an event handler or HTTP endpoint, changing a message or payload shape, writing tests, checking what an event contains, or asking who consumes a channel. Also use when the user mentions AsyncAPI, OpenAPI, ODCS or data contracts, feature files, acceptance criteria, or names a service such as orders or payments.
---

# Working against the service catalog

The catalog is reached only through the `catalog` MCP tools. Do not search
the filesystem for specs or feature files — the copies you would find are
not the record.

## Two classes of artifact

**Gated** — AsyncAPI, OpenAPI, ODCS data contracts, Gherkin features.
These are the contract of record. If an implementation disagrees with a
gated artifact, the implementation is wrong. Never adjust one to make a
failing test or handler pass. If a gated artifact genuinely looks wrong,
say so and stop: changing it is a deliberate, versioned act in the catalog
repo, gated in CI.

**Ungated** — ADRs, runbooks, domain notes. Context and rationale. Read
them for the *why*, but they bind nothing and you may propose edits freely.

Every `get_artifact` response tells you which class it is. Believe it.

## Order of operations

1. `list_services()` — what exists.
2. `get_service(name)` — the artifact index and the produce/consume edges.
   Returns no file contents, so it is cheap.
3. Then fetch narrowly:
   - `get_message_schema(service, message)` for one event payload
   - `get_acceptance_criteria(service)` before writing any behaviour
   - `get_artifact(service, path)` for a whole spec, ADR, or data contract
4. `trace_channel(address)` before changing any published shape — the
   consumers it lists are what you will break.
5. `search_catalog(query, kind=...)` when you do not know where something lives.

Do not pull whole documents when a single message or scenario would do.

## Implementing from Gherkin

Feature files are acceptance criteria, not inspiration. Each scenario maps
to a test. Implement toward the scenarios as written; if one cannot be
satisfied, that is a finding to report, not a line to edit.

Where a scenario and a schema seem to disagree, the schema wins on shape
and the scenario wins on behaviour. Raise the conflict either way.

## Conventions

- Channel addresses: `<service>.<event>.v<major>`, lowercase, dot separated
  (`orders.placed.v1`). Major version lives in the address; minor changes
  never change it.
- Payloads set `additionalProperties: false` and an explicit `required`.
- Money is an integer in minor units, suffixed `Pence`. Never a float.
- Identifiers are `format: uuid`. Timestamps `format: date-time`, UTC.
- Message names are `PascalCase`, past tense (`OrderPlaced`,
  `PaymentSettled`).
- Delivery is at-least-once. The payloads carry no envelope event id, so
  handlers dedupe on the natural key (`orderId`, `paymentId`) plus the
  event's semantics.
- Ordering holds only within a partition key (the aggregate id), never
  across channels.
- Spec `info.version` always equals the manifest version — mock URLs and
  rendered docs surface `info.version`, and `lint:manifest` enforces the
  match.
- AsyncAPI channels carry a `ws` binding (the mock transport) and every
  operation lists explicit `messages` refs — the Microcks async runner
  cannot validate without them.
- A service that has a live implementation names it in the manifest as
  `implementationRepo: <owner>/<repo>`; merges touching that service then
  dispatch its contract-sync workflow.
- Every schema element you add — message, payload property, endpoint,
  parameter — must be named in that service's feature files. The feature
  change is part of the contract change, not an afterthought; `check:intent`
  enforces this with no escape hatch. If it is not worth a scenario, it is
  not worth adding to the contract yet.
