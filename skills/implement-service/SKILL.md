---
name: implement-service
description: Guide a real implementation of a catalog service's contracts, from interview to verified definition of done. Use when asked to implement a catalog service (e.g. orders, payments), build one of its APIs for real, or take its contracts to production.
---

# Implement a catalog service

You are implementing one service's contracts for real. The contracts are the
authority; this skill is only the process for getting from them to a verified
implementation. Every path and tool below is parameterized by the service
name — ask which service if it is not obvious from the request.

## Phase 0 — locate the contracts

Never work from an embedded or remembered copy of the contracts. How you read
them depends on where you are running:

- **Interactive sessions**: use the `catalog` MCP tools — that is what they
  are for. `list_services()` → `get_service(<name>)` for the artifact index
  and produce/consume edges, then fetch narrowly:
  `get_acceptance_criteria(<name>)`, `get_message_schema(<name>, <Message>)`,
  `get_artifact(<name>, <path>)`. `trace_channel(<address>)` shows who you
  would break.
- **CI, cucumber binding, and anything needing reproducible file paths**:
  pin a clone and resolve paths against it:

  ```sh
  git clone --depth 1 https://github.com/hungovercoders/service-catalog
  ```

  Record the commit sha in a `CONTRACT_REF` file at the implementation
  repo's root — it is the statement of which contract version this
  implementation satisfies, and the sync loop in phase 5 keys off it.

The files, under `catalog/<service>/`:

- `openapi/*.yaml`, `asyncapi/*.yaml` — the interface
- `features/*.feature` — the cross-interaction rules (normative)
- `data-contracts/*.yaml` — data products the service exposes
- `docs/` — ADRs and runbooks: why the contract is shaped the way it is

## Ground rules

- Never edit specs or features to make an implementation pass. A red suite is
  a finding about the implementation. Contract changes are separate work in
  the catalog repo, gated by `task check:version` (any change needs a
  manifest bump) and `task check:compat` (breaking needs a major bump).
- Internals — queue, storage, framework — are free choices, and they stay out
  of the implementation's public documentation for the same reason they are
  absent from the contracts.
- Report verification results honestly: a failing suite is reported as
  failing, with output, not routed around.

## Phase 1 — interview

Ask before writing any code (one round of questions where possible):

1. **Which service**, if not already stated.
2. **Language and framework**.
3. **Where the implementation lives** — a new repository (recommended; the
   catalog stays contracts-only) or a path the user names.
4. **Hosting / runtime target** — affects scaffolding and CI, nothing
   contractual.
5. **Event transport** — the contracts mock over WebSocket but do not mandate
   it. The choice must be one the Microcks async test runner can point at,
   because it becomes the scheme of the `ASYNC_ENDPOINT` override in
   verification (e.g. `ws://`, `kafka://`, `mqtt://`, `amqp://`).
6. **Storage** — for aggregate state and idempotency records.
7. **Constraints** — CI system, registries, observability, anything
   third-party the implementation must fit into.

Do **not** interview about anything the contract already decides: endpoints,
status codes, payload shapes, channel addresses, event semantics. If the user
wants one of those changed, stop — that is a catalog change first, not an
implementation choice.

## Phase 2 — build

- Read the specs and features before scaffolding; generate or hand-write from
  the contract files, never from memory of them.
- Bind `catalog/<service>/features/*.feature` with a Cucumber implementation
  for the chosen language. Bind the files from the pinned clone; do not copy
  or paraphrase them into the implementation repo.

## Phase 3 — verify (the definition of done)

Loop until all three are green, from a service-catalog checkout with the
implementation running and reachable from the Microcks containers:

1. **Contract tests**:

   ```sh
   task mocks:contract SERVICE=<name> REST_ENDPOINT=<http url> ASYNC_ENDPOINT=<transport url>
   ```

2. **The bound feature suite** against the running implementation.

3. **Schema fuzz** for declared-but-unexampled paths:

   ```sh
   uvx schemathesis run catalog/<service>/openapi/*.yaml --url <http url>
   ```

## Phase 4 — wire the implementation's CI

Recreate the loop in the implementation's pipeline using the pinned clone:
start the mock stack, start the implementation, run the three checks above,
tear down. The implementation's CI then enforces the same definition of done
as the catalog declares — one definition of "correct", no drift.

## Phase 5 — keep it in sync

Contract changes should reach the implementation without a human having to
notice them:

- Copy `templates/contract-sync.yml` (bundled beside this skill) into the
  implementation repo's `.github/workflows/`, and ensure `CONTRACT_REF`
  holds the service-catalog commit sha the implementation was built against.
- One-time setup in the implementation repo: an `ANTHROPIC_API_KEY` actions
  secret, and the Claude GitHub App installed — PRs created with the default
  `GITHUB_TOKEN` never trigger workflows, so the merge gate would silently
  vanish.
- One-time setup in the catalog repo: set `implementationRepo: <owner>/<repo>`
  in `catalog/<service>/service.yaml`, and a `CONTRACTS_DISPATCH_TOKEN`
  actions secret (fine-grained PAT with contents write on the implementation
  repo) so `dispatch-contract-change.yml` can reach it.

The loop then runs itself: a merge to the catalog's `main` touching
`catalog/<service>/` dispatches `{contract_ref, service}` to that service's
implementation repo; the sync workflow no-ops when `CONTRACT_REF` already
matches, otherwise an agent scopes its work from the contract diff, updates
the implementation minimally, and converges on a single `contract-sync/<sha>`
PR gated by the implementation's own CI.
