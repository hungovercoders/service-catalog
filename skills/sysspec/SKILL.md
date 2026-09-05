---
name: sysspec
description: Use when working against any service in the specs — implementing an event handler or HTTP endpoint, changing a message or payload shape, writing tests, checking what an event contains, or asking who consumes a channel. Also use when the user mentions AsyncAPI, OpenAPI, ODCS or data contracts, feature files, acceptance criteria, or names a service such as orders or payments.
---

# Working against the system specs

The specs is reached only through the `sysspec` MCP tools. Do not search
the filesystem for specs or feature files — the copies you would find are
not the record.

## Two classes of artifact

**Gated** — AsyncAPI, OpenAPI, ODCS data contracts, Gherkin features.
These are the contract of record. If an implementation disagrees with a
gated artifact, the implementation is wrong. Never adjust one to make a
failing test or handler pass. If a gated artifact genuinely looks wrong,
say so and stop: changing it is a deliberate, versioned act in the specs
repo, gated in CI.

**Ungated** — ADRs, runbooks, domain notes. Context and rationale. Read
them for the *why*, but they bind nothing and you may propose edits freely.

Every `get_artifact` response tells you which class it is. Believe it.

## Order of operations

1. `list_services()` — what exists.
2. `get_service(name)` — the artifact index and the produce/consume edges.
   Returns no file contents, so it is cheap.
3. Then fetch narrowly:
   - `get_message_schema(service)` — no message argument — to list message
     and schema names; `get_message_schema(service, message)` for one
     payload (AsyncAPI messages and OpenAPI schemas both resolve here)
   - `get_acceptance_criteria(service, names_only=True)` for the scenario
     index; then `scenario="..."` or `path="..."` for just the ones you
     need. Omit all filters only when implementing the whole service.
   - `get_artifact(service, path, section="/components/schemas/Order")`
     for one section of a YAML spec (RFC 6901 pointer; `~1` escapes `/`
     in OpenAPI paths); omit `section` only for a whole spec, ADR, or
     data contract
4. `trace_channel(address)` before changing any published shape — the
   consumers it lists are what you will break. Empty `produced_by` and
   `consumed_by` means nothing references the address, not an error.
5. `search_specs(query, kind=..., service=...)` when you do not know where
   something lives. The response says `truncated` when there were more
   hits than it returned; raise `limit` only then.

Do not pull whole documents when a single message or scenario would do —
every accessor above has a mode for exactly one, and any response that
had to cut content says so with a `truncated` flag.

## Implementing from Gherkin

Feature files are acceptance criteria, not inspiration. Each scenario maps
to a test. Implement toward the scenarios as written; if one cannot be
satisfied, that is a finding to report, not a line to edit.

Where a scenario and a schema seem to disagree, the schema wins on shape
and the scenario wins on behaviour. Raise the conflict either way.

## Conventions

- **Attributes and their values are `lower_snake_case`** — every payload
  property, every schema property, every path and query parameter, every
  ODCS column, and every enumerated value (`out_of_stock`, not
  `outOfStock`). This is the default across all four artifact kinds, so a
  field is spelled the same in the AsyncAPI payload, the OpenAPI schema and
  the data contract, and no consumer has to translate between them.
  Enforced, not advisory: Spectral rules over the specs
  (`contract-*-snake-case` in `.spectral.yaml`) and a Spectral ruleset over
  the ODCS files, which `lint:datacontracts` runs alongside datacontract-cli.
- Document-local identifiers are *not* attributes and keep their own
  conventions: message names `PascalCase`, channel and operation keys and
  OpenAPI `operationId`s `camelCase`, channel addresses dotted lowercase.
  Nor are free-form string values gated — a `const` like
  `com.hungovercoders.orders.placed.v2` or `application/json` is an
  identifier, and only `enum` members are treated as a controlled
  vocabulary.
- Channel addresses: `<service>.<event>.v<major>`, lowercase, dot separated
  (`orders.placed.v2`). Major version lives in the address; minor changes
  never change it.
- Every event is a CloudEvents 1.0 **structured** envelope
  (`application/cloudevents+json`): `specversion` `"1.0"`, `id` (uuid),
  `source` (`/<service>`), `type`
  (`com.<org>.<service>.<event>.v<major>` — reverse-DNS org, matching the
  channel major; these specs uses `com.hungovercoders`), `subject` (the aggregate id), `time`, `datacontenttype`, and the
  domain payload under `data`. Envelope and `data` both set
  `additionalProperties: false` and an explicit `required`.
- Money is an integer in minor units, suffixed `_pence`. Never a float.
- Identifiers are `format: uuid`. Timestamps `format: date-time`, UTC.
- Message names are `PascalCase`, past tense (`OrderPlaced`,
  `PaymentSettled`).
- Delivery is at-least-once; handlers dedupe on the envelope `id`. The
  natural key in `data` (`order_id`, `payment_id`) identifies the aggregate,
  not the event.
- Ordering holds only within a partition key (the aggregate id), never
  across channels.
- Spec `info.version` always equals the manifest version — mock URLs and
  rendered docs surface `info.version`, and `lint:manifest` enforces the
  match.
- Every manifest also carries a top-level `version:` — the version of the
  service's whole contract surface, which is what consumers pin. Any gated
  artifact bump bumps it; an artifact major bump or breaking change bumps
  its major. Merges to main are tagged `<service>/v<version>`.
- AsyncAPI channels carry a `ws` binding (the mock transport) and every
  operation lists explicit `messages` refs — the Microcks async runner
  cannot validate without them.
- Merges to main publish each changed service's contract surface as a
  lightweight tag `<service>/v<version>`. Implementation repos pull these
  tags; the specs never pushes work at them.
- A service that has a live implementation may name it in the manifest as
  `implementationRepo: <owner>/<repo>` — optional metadata for docs and
  the specs graph, nothing more.
- Every schema element you add — message, payload property, endpoint,
  parameter — must be named in that service's feature files. The feature
  change is part of the contract change, not an afterthought; `check:intent`
  enforces this with no escape hatch. If it is not worth a scenario, it is
  not worth adding to the contract yet.
