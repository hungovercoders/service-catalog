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
  the implementation repo pins a released contract surface in a
  `contracts.lock` at its root:

  ```yaml
  # contract pin - version is the catalog release tag, sha its commit
  version: orders/v1.0.0
  sha: 0123456789abcdef0123456789abcdef01234567
  ```

  The catalog publishes every merged surface as a lightweight tag
  `<service>/v<version>`; the lock records which one this implementation
  satisfies. Contracts are then *fetched, never vendored*, by a task the
  implementation repo carries:

  ```yaml
  contracts:fetch:
    desc: Fetch the pinned contract surface into read-only .contracts/
    cmds:
      - |
        sha=$(awk '/^sha:/{print $2}' contracts.lock)
        chmod -R u+w .contracts 2>/dev/null || true
        rm -rf .contracts && git init -q .contracts
        git -C .contracts remote add origin https://github.com/hungovercoders/service-catalog
        git -C .contracts sparse-checkout set catalog/<service>
        git -C .contracts fetch -q --depth 1 origin "$sha"
        git -C .contracts checkout -q FETCH_HEAD
        chmod -R a-w .contracts/catalog
  ```

  `.contracts/` is gitignored and write-protected: consumable, not editable.
  Every run re-fetches at the pinned sha, so a local edit cannot survive,
  and the sha (not the tag) is what gets checked out — a re-cut tag cannot
  silently change what you build against.

The files, under `.contracts/catalog/<service>/`:

- `openapi/*.yaml`, `asyncapi/*.yaml` — the interface
- `features/*.feature` — the cross-interaction rules (normative)
- `data-contracts/*.yaml` — data products the service exposes
- `docs/` — ADRs and runbooks: why the contract is shaped the way it is

## Ground rules

- Never edit specs or features to make an implementation pass. A red suite is
  a finding about the implementation. Contract changes are separate work in
  the catalog repo, gated by `task check:version`, `task check:compat` and
  `task check:intent`. The read-only fetch makes this mechanical, not
  aspirational.
- Feature binding runs strict: an undefined or pending step fails the build.
  Disabling strict mode is forbidden — an unbound scenario is a contract
  obligation silently dropped.
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
- Bind `.contracts/catalog/<service>/features/*.feature` with a Cucumber
  implementation for the chosen language, in strict mode (cucumber-js is
  strict by default since v7; other runners have an equivalent — turn it
  on). Bind the fetched files in place; do not copy or paraphrase them into
  the implementation repo.

## Phase 3 — verify (the definition of done)

Loop until all three are green, from a service-catalog checkout at the lock
sha with the implementation running and reachable from the Microcks
containers:

1. **Contract tests**:

   ```sh
   task mocks:contract SERVICE=<name> REST_ENDPOINT=<http url> ASYNC_ENDPOINT=<transport url>
   ```

2. **The bound feature suite** against the running implementation — strict,
   every scenario bound.

3. **Schema fuzz** for declared-but-unexampled paths:

   ```sh
   uvx schemathesis run .contracts/catalog/<service>/openapi/*.yaml --url <http url>
   ```

## Phase 4 — wire the implementation's CI

Recreate the loop in the implementation's pipeline: mise-action, then
`task contracts:fetch`, start the mock stack and the implementation, run the
three checks above, tear down. Everything resolves against `.contracts/`, so
the pipeline verifies exactly the surface the lock names — one definition of
"correct", no drift.

## Phase 5 — keep it in sync

The catalog never pushes work at implementations; they pull. When a merge to
the catalog's main publishes a new `<service>/v<version>` tag, Renovate opens
a PR on the implementation repo bumping `contracts.lock`. That PR runs the
phase 4 gates against the new pin:

- **Green** (typical for additive minors): the bump auto-merges. The
  implementation now records that it satisfies the new surface — no human,
  no agent.
- **Red, or a major bump**: the deterministic gates have proven code changes
  are needed, and only then does an agent wake to converge the
  implementation on the same branch, with the failing checks as its scope.

Setup for both sides lives in the `renovate.json` and `contract-converge.yml`
templates bundled beside this skill — copy them in, substitute the service
name, and see their headers for the one-time repository settings.
