---
name: implement-service
description: Build a real implementation of a sysspec service, from interview to verified definition of done. Use when asked to implement a service (e.g. orders, payments), build one of its APIs for real, or take its contracts to production.
---

# Implement a sysspec service

You are building a real service whose behaviour is already decided: the
contracts in the spec repo say what it must do; you choose how. This skill
is the whole journey — what to ask, what to create, and the exact commands
that prove you are done. Examples use the `orders` service and the
`hungovercoders/sysspec` spec repo; substitute your own.

## What you end up with

A **new repository**, separate from the spec repo, that looks like this:

```
orders-service/
├── contracts.lock          # which released contract surface this repo satisfies
├── .contracts/             # read-only fetch of that surface (gitignored, regenerated)
├── Taskfile.yml            # contracts:fetch and contracts:verify (defined below)
├── src/                    # the implementation — language, storage, framework all yours
├── steps/                  # step definitions binding the pinned feature files
├── renovate.json           # pulls future contract releases into contracts.lock
└── .github/workflows/
    ├── ci.yml              # fetch + verify on every push
    └── contract-converge.yml   # handles contract bumps; agent wakes only when red
```

The definition of done is one command: `task contracts:verify` green against
your running implementation. Nothing in the spec repo changes.

## Five terms, once

- **Contract surface** — everything under `specs/<service>/` in the spec
  repo at one released version: OpenAPI, AsyncAPI, feature files, data
  contracts.
- **Release tag** — every merge to the spec repo's main tags each changed
  service `<service>/v<x.y.z>` (e.g. `orders/v3.1.0`). These are the only
  versions you build against.
- **The lock** — `contracts.lock` records the tag and its commit sha. It is
  the single source of truth for which surface this implementation satisfies.
- **The Microcks stack** — a docker-compose stack orchestrated by the spec
  repo's kit. It plays two segregated roles: the `mocks:*` tasks serve
  mocks of the contracts (for consumers and spec conformance), and
  `contract:test` uses the same engine to hold a real implementation to
  the contracts. You run both from the pinned fetch; you install nothing
  else.
- **Strict binding** — the feature files run against your service via step
  definitions you write. Strict means an unbound or pending step fails the
  build: every sentence in the contract is enforced or the suite is red.
  What strict cannot see is a bound-but-*empty* step — the null-service
  check in Step 4 exists for exactly that.

## Ground rules

- Never edit specs or features to make the implementation pass. A red suite
  is a finding about the implementation. If a contract looks wrong, stop and
  raise it — contract changes happen in the spec repo, behind its own gates.
- Never weaken verification: no disabling strict mode, no skipping scenarios,
  no exempting scenarios from the null-service check, no editing under
  `.contracts/`. The fetch re-applies read-only at the pinned sha, so local
  edits cannot survive anyway.
- Internals — storage, queue, framework — are your choice, and they stay out
  of the implementation's public documentation for the same reason they are
  absent from the contracts.
- Report results honestly: a failing suite is reported failing, with output.

## Step 1 — interview

Ask before writing code (one round of questions where possible):

1. **Which service** — decides every path below.
2. **Language and framework** — decides the Cucumber runner and scaffolding.
3. **Where the implementation lives** — a new repository (recommended; the
   spec repo stays contracts-only) or a path the user names.
4. **Event transport** — the mocks emit over WebSocket but the contract does
   not mandate a transport. Your choice becomes the scheme of the
   `ASYNC_ENDPOINT` in verification (`ws://`, `kafka://`, `mqtt://`,
   `amqp://` — it must be one the Microcks async runner can point at).
5. **Storage** — for aggregate state and idempotency records.
6. **Hosting / CI constraints** — affects scaffolding, nothing contractual.

Do **not** interview about anything the contract already decides: endpoints,
status codes, payload shapes, channel addresses, event semantics. If the
user wants one of those changed, stop — that is spec repo work first.

## Step 2 — scaffold and pin

Create the repository, then pin the newest released surface.

**Find the tag and its sha** (lightweight tags, so the listed sha *is* the
commit sha the lock wants):

```sh
git ls-remote https://github.com/hungovercoders/sysspec "refs/tags/orders/v*"
# 6c4e1fc2e735f2491fe67c7f31390ced024d5d2a  refs/tags/orders/v3.1.0   <- highest
```

**Write `contracts.lock`** at the repo root from that line:

```yaml
# contract pin - version is the spec repo's release tag, sha its commit
version: orders/v3.1.0
sha: 6c4e1fc2e735f2491fe67c7f31390ced024d5d2a
```

**Write the two tasks** the whole loop runs on (and gitignore `.contracts/`
and `.null-results.json`):

```yaml
version: '3'

vars:
  SERVICE: orders
  SPECS_REPO: hungovercoders/sysspec

tasks:
  contracts:fetch:
    desc: Fetch the pinned contract surface and its toolchain into read-only .contracts/
    cmds:
      - |
        sha=$(awk '/^sha:/{print $2}' contracts.lock)
        chmod -R u+w .contracts 2>/dev/null || true
        rm -rf .contracts && git init -q .contracts
        git -C .contracts remote add origin https://github.com/{{.SPECS_REPO}}
        git -C .contracts sparse-checkout set specs/{{.SERVICE}} mocks kit
        git -C .contracts fetch -q --depth 1 origin "$sha"
        git -C .contracts checkout -q FETCH_HEAD
        chmod -R a-w .contracts/specs

  contracts:verify:
    desc: The definition of done - run with the implementation up (see Step 4 for the URLs)
    cmds:
      - task -d .contracts contract:test SERVICE={{.SERVICE}} REST_ENDPOINT={{.REST_ENDPOINT}} ASYNC_ENDPOINT={{.ASYNC_ENDPOINT}}
      - BASE_URL={{.BASE_URL}} npx cucumber-js .contracts/specs/{{.SERVICE}}/features --require steps/
      - BASE_URL=http://localhost:9099 task -d .contracts null:run RESULTS={{.ROOT_DIR}}/.null-results.json -- npx cucumber-js {{.ROOT_DIR}}/.contracts/specs/{{.SERVICE}}/features --require {{.ROOT_DIR}}/steps/ --format json:{{.ROOT_DIR}}/.null-results.json
      - uvx schemathesis run .contracts/specs/{{.SERVICE}}/openapi/*.yaml --url {{.BASE_URL}}
```

The sparse checkout brings the contracts plus the spec repo's `mocks/`
examples, `kit/` and root `Taskfile.yml`, all at the pinned sha — so
`task -d .contracts mocks:...` runs the spec repo's own mock orchestration,
versioned by the pin, with nothing copied or installed. The specs are
write-protected and `.contracts/` is regenerated on every fetch: consumable,
never editable, and the sha (not the tag) is checked out, so a re-cut tag
cannot silently change what you build against.

One consequence, spelled out: **the pinned toolchain is authoritative over
this skill.** The task names here are the spec repo's main as of this
skill's writing; an older pin may name them differently or lack newer gates
(at `orders/v3.1.0`, for example, the contract test was `mocks:contract`
and the kit had no `null:run`). When the skill and your pin disagree,
`task -d .contracts --list` shows what the pin actually provides — follow
the pin, and mirror a missing gate in your own repo rather than skipping
it.

Run `task contracts:fetch` now. The contracts land under
`.contracts/specs/orders/`:

- `openapi/*.yaml` — the synchronous interface: paths, status codes, schemas
- `asyncapi/*.yaml` — events produced and consumed: channels, envelopes, payloads
- `features/*.feature` — acceptance criteria; every scenario must pass, bound
- `data-contracts/*.yaml` — data products the service must expose

In interactive sessions, also use the `sysspec` MCP tools to explore —
`list_services()`, `get_service(orders)`, `get_acceptance_criteria(orders)`,
`get_message_schema(orders, OrderPlaced)`, `trace_channel(<address>)` (who
you would break) — but everything you build and everything CI runs resolves
against the fetched files, never a remembered copy.

## Step 3 — build

Read the specs and features before scaffolding; generate or hand-write from
the contract files. Internals are free; the surface is not.

The example files under `.contracts/mocks/` are not only mock fixtures:
`contract:test` replays each REST example against your real service and
expects the example's exact response status. An example that fetches a
well-known id expecting 200 means your implementation must hold that state
— seed the example fixtures at startup (idempotently, behind an env switch
if you like) or the contract test can never pass against a real service.

Bind the feature files **in place** — the spec repo owns the sentences, you
own the glue. Do not copy or paraphrase them into your repo. With
cucumber-js (strict by default since v7):

```sh
npx cucumber-js .contracts/specs/orders/features --require steps/
```

and each step definition maps a contract sentence onto the running service:

```js
Then('the order status is {string}', async function (status) {
  const order = await this.api.get(`/orders/${this.orderId}`);
  assert.equal(order.status, status);
});
```

Other runners take the external path the same way: Cucumber-JVM
`@CucumberOptions(features = ".contracts/...")`, pytest-bdd
`scenarios(".contracts/...")`, Reqnroll linked feature files. Whatever the
runner, strict mode stays on — an unbound scenario is a contract obligation
silently dropped.

## Step 4 — verify (the definition of done)

Start your implementation, then:

```sh
task contracts:verify \
  BASE_URL=http://localhost:3000 \
  REST_ENDPOINT=http://host.docker.internal:3000 \
  ASYNC_ENDPOINT=ws://host.docker.internal:3001/events
```

Two views of the same running service, and mixing them up is the classic
failure: `BASE_URL` is how *your machine* reaches it (the feature suite and
schemathesis run on the host); `REST_ENDPOINT`/`ASYNC_ENDPOINT` are how the
*Microcks containers* reach it — `localhost` inside a container is the
container, so a service on the host is `host.docker.internal`, and the
`ASYNC_ENDPOINT` scheme is your chosen transport from the interview. The
kit appends `/<operation>` to the `ASYNC_ENDPOINT` for each send
operation's test (`.../events/publishOrderPlaced`, ...), so each operation
is validated on its own path — serve each channel at a path naming its
operation or channel address, or ignore the path and send everything. Keep
a path on the base URL itself (`/events` above): pins older than kit
0.22.0 use the endpoint verbatim, and Microcks' WS consumer rejects a bare
`ws://host:port` with the opaque "found no suitable MessageConsumptionTask
implementation for endpoint".

What each check proves, in order:

1. **Contract tests** (`contract:test`) — Microcks replays every operation
   in the OpenAPI and AsyncAPI against your service and validates the real
   responses and emitted events against the schemas. (The first run starts
   the Microcks stack, which the runner needs even when testing a real
   service; `task -d .contracts mocks:down` stops it. The `mocks:*` tasks
   themselves are for consumers and the spec repo's own conformance checks
   — as an implementer, `contract:test` is the only one you invoke.)
2. **The bound feature suite** — every acceptance scenario passes against
   the running service, strict, no unbound steps.
3. **The negative control** (`null:run`) — the same suite replayed against
   a null service the kit serves: `200 {}` to every request, no events
   (event awaits just time out). Red unless **zero** scenarios pass, naming
   any that do. Green against your real service means nothing unless the
   suite is also fully red against a service that does nothing — strict
   mode catches unbound steps, this catches bound-but-empty ones. A named
   scenario is either a hollow binding (fix the glue) or a scenario that
   genuinely asserts nothing beyond a 200 — a spec finding to raise, never
   a gate to exempt. The response is deliberately a plausible 200 rather
   than an error so that status-code-only bindings are flagged too. This
   proves the suite is falsifiable — every scenario *can* fail — not that
   its assertions are deep.
4. **Schema fuzz** (`schemathesis`) — declared-but-unexampled paths still
   honour the schemas.

Loop on red until all four are green. Green means the repo demonstrably
satisfies the surface named in `contracts.lock` — that is the claim the lock
makes, and this is the command that backs it.

## Step 5 — wire the implementation's CI

Recreate exactly that loop in the pipeline: mise-action →
`task contracts:fetch` → start the implementation → `task contracts:verify`
(with the endpoint URLs for the CI network) → teardown. Everything resolves
against `.contracts/`, so CI verifies exactly the surface the lock names —
one definition of "correct", no drift.

## Step 6 — stay in sync

The spec repo never pushes work at implementations; they pull. When a merge
publishes a new `<service>/v<version>` tag, Renovate opens a PR here bumping
`contracts.lock`, and CI runs the Step 5 gates against the new pin:

- **Green** (typical for additive minors): the bump auto-merges. The repo
  now records that it satisfies the new surface — no human, no agent.
- **Red, or a major bump**: the gates have proven code changes are needed,
  and only then does an agent wake to converge the implementation on the
  same branch, with the failing checks as its scope.

Copy in the `renovate.json` and `contract-converge.yml` templates bundled
beside this skill, substitute `__SERVICE__` and `__SPECS_REPO__`, and see
their headers for the one-time repository settings. The converge workflow
calls the same `task contracts:fetch` and `task contracts:verify` you
defined in Step 2 — nothing new exists only in CI.

## Done when

- [ ] `contracts.lock` pins the intended release tag and its commit sha
- [ ] `task contracts:fetch` produces a read-only `.contracts/` (gitignored)
- [ ] every feature file scenario is bound — strict, no pending steps
- [ ] the suite is falsifiable — zero scenarios pass against the null service
- [ ] `task contracts:verify` is green against the running implementation
- [ ] CI runs fetch + verify on every push
- [ ] `renovate.json` + `contract-converge.yml` installed with the
      placeholders substituted and the one-time settings from their headers
