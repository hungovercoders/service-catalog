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
- **The mock stack** — a Microcks docker-compose stack orchestrated by the
  spec repo's kit. It serves mocks of the contracts *and* contract-tests a
  real implementation against them. You run it from the pinned fetch; you
  install nothing else.
- **Strict binding** — the feature files run against your service via step
  definitions you write. Strict means an unbound or pending step fails the
  build: every sentence in the contract is enforced or the suite is red.

## Ground rules

- Never edit specs or features to make the implementation pass. A red suite
  is a finding about the implementation. If a contract looks wrong, stop and
  raise it — contract changes happen in the spec repo, behind its own gates.
- Never weaken verification: no disabling strict mode, no skipping scenarios,
  no editing under `.contracts/`. The fetch re-applies read-only at the
  pinned sha, so local edits cannot survive anyway.
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

**Write the two tasks** the whole loop runs on (and gitignore `.contracts/`):

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
    desc: The definition of done - run with the implementation up (see Step 5 for the URLs)
    cmds:
      - task -d .contracts mocks:contract SERVICE={{.SERVICE}} REST_ENDPOINT={{.REST_ENDPOINT}} ASYNC_ENDPOINT={{.ASYNC_ENDPOINT}}
      - BASE_URL={{.BASE_URL}} npx cucumber-js .contracts/specs/{{.SERVICE}}/features --require steps/
      - uvx schemathesis run .contracts/specs/{{.SERVICE}}/openapi/*.yaml --url {{.BASE_URL}}
```

The sparse checkout brings the contracts plus the spec repo's `mocks/`
examples, `kit/` and root `Taskfile.yml`, all at the pinned sha — so
`task -d .contracts mocks:...` runs the spec repo's own mock orchestration,
versioned by the pin, with nothing copied or installed. The specs are
write-protected and `.contracts/` is regenerated on every fetch: consumable,
never editable, and the sha (not the tag) is checked out, so a re-cut tag
cannot silently change what you build against.

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
  ASYNC_ENDPOINT=ws://host.docker.internal:3001
```

Two views of the same running service, and mixing them up is the classic
failure: `BASE_URL` is how *your machine* reaches it (the feature suite and
schemathesis run on the host); `REST_ENDPOINT`/`ASYNC_ENDPOINT` are how the
*Microcks containers* reach it — `localhost` inside a container is the
container, so a service on the host is `host.docker.internal`, and the
`ASYNC_ENDPOINT` scheme is your chosen transport from the interview.

What each check proves, in order:

1. **Contract tests** (`mocks:contract`) — Microcks replays every operation
   in the OpenAPI and AsyncAPI against your service and validates the real
   responses and emitted events against the schemas. Despite the name, this
   is not testing mocks: the task lives under the spec repo's `mocks:`
   namespace because Microcks is both the mock server and the contract-test
   runner — with the endpoint overrides it is testing *your implementation*.
   (The first run starts the Microcks stack itself, which the runner needs
   even when testing a real service; `task -d .contracts mocks:down` stops
   it.)
2. **The bound feature suite** — every acceptance scenario passes against
   the running service, strict, no unbound steps.
3. **Schema fuzz** (`schemathesis`) — declared-but-unexampled paths still
   honour the schemas.

Loop on red until all three are green. Green means the repo demonstrably
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
- [ ] `task contracts:verify` is green against the running implementation
- [ ] CI runs fetch + verify on every push
- [ ] `renovate.json` + `contract-converge.yml` installed with the
      placeholders substituted and the one-time settings from their headers
