# Contributing

This applies to humans and coding agents equally: the checks are the
contract, and they are identical locally, in the git hooks, and in CI.

## Setup

```sh
task setup   # installs the pinned toolchain (mise) and the git hooks
```

Everything runs through [Task](https://taskfile.dev). If a command is not a
`task`, it is not part of the workflow.

## The one command that matters

```sh
task ci
```

Exactly what CI runs. It composes, in order:

| Task | What it enforces |
| --- | --- |
| `check:branch` | branch is `main` or `<user>/gri-<number>-<slug>` (Linear convention) |
| `lint:specs` | Spectral over the OpenAPI/AsyncAPI contracts |
| `lint:features` | gherkin-lint over the acceptance criteria |
| `lint:datacontracts` | datacontract-cli over the ODCS data contracts |
| `lint:manifest` | manifests ⇄ contracts ⇄ catalog graph consistency, semver versions, feature references resolve to real messages and channels |
| `check:version` | any gated artifact change bumps its manifest version *and* the service's top-level version; artifact major ⇒ service major |
| `check:plugin` | plugin surface changes (kit, skills, plugin manifests) bump the plugin version |
| `check:kit` | changes under `kit/` bump the `catalog-kit` package version |
| `docs:build` | the generated docs site builds `--strict` |
| `check:commits` | conventional commit messages |
| `check:compat` | breaking contract changes carry major bumps (artifact and service) |
| `check:intent` | every added schema element is named in the service's feature files — no escape hatch |
| `mocks:*` | Microcks mocks load, contract-test, and smoke-test green |

Scope most tasks to one service with `SERVICE=<name>`.

## Changing the catalog

Gated artifacts — AsyncAPI, OpenAPI, ODCS data contracts, feature files —
are the contract of record. Never edit one to make an implementation or a
test pass; that direction is always a finding, not a fix. When you do change
one deliberately:

1. Bump the artifact's version in `catalog/<service>/service.yaml` (and
   `info.version` in the spec — they must match).
2. Bump the service's top-level `version:`. Breaking change ⇒ major on both.
3. If you added a schema element, name it in a scenario in that service's
   `features/` — `check:intent` fails otherwise, deliberately without an
   escape hatch.
4. Conventions (channel naming, payload rules, money, idempotency) live in
   `skills/service-catalog/SKILL.md`.

On merge to main, each changed service is published as a lightweight git tag
`<service>/v<version>`. Implementation and consumer repos pin those tags
via a `contracts.lock` and pull updates through Renovate — see
`skills/implement-service/SKILL.md` and `skills/consume-service/SKILL.md`.
The catalog never pushes work at them.

## Pull requests

One Linear ticket per PR, branch named from the ticket, conventional
commits (`feat:`, `fix:`, `chore:`, …— the hooks enforce this). Open PRs as
drafts; keep `task ci` green.

## The Claude Code plugin

This repo doubles as a Claude Code plugin (`.claude-plugin/plugin.json`):
the MCP server plus the skills are the installed surface. If a change alters
that surface — server behaviour, any skill, the bundled templates — bump
the plugin `version` in the same PR (semver: breaking/feature/fix).
`task check:plugin` enforces this.

## Releasing catalog-kit

The kit (gates, mocks, docs, MCP server) is published to PyPI as
`catalog-kit`. To cut a release: make sure `kit/pyproject.toml` carries the
new version (`check:kit` forces this whenever `kit/` changes), then tag the
merge commit `v<version>` and push the tag. `release.yml` verifies the tag
matches the kit version, builds with `uv build`, publishes via PyPI trusted
publishing, and force-moves the floating `v<major>` tag that adopter
workflows reference. Product `v*` tags live alongside the `<service>/v*`
contract tags.

One-time setup: on pypi.org, add a *trusted publisher* for the
`catalog-kit` project pointing at this repository, workflow `release.yml`,
environment `pypi`.
