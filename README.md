# service-catalog

A contract-first **service catalog** you can install: AsyncAPI, OpenAPI,
ODCS data contracts and Gherkin acceptance criteria as the versioned
contract of record, with deterministic gates, Microcks mocks, generated
docs, and read-only MCP access for agents.

The problem it solves: specs living as files next to an implementation get
quietly amended to match failing code. Here the catalog is reached only
through tools, gated artifacts are labelled as binding, and CI fails any
change to one that does not carry a version bump.

This repo is three things at once:

1. **The toolkit** — [`catalog-kit`](kit/) on PyPI: the `catalog` CLI
   (gates, lint, docs, mock orchestration, `init` scaffold) and the
   `contracts-mcp` server.
2. **The distribution** — reusable GitHub workflows
   (`.github/workflows/catalog-*.yml`) and a Claude Code plugin (MCP tools
   + the three skills).
3. **The living example** — the `orders`/`payments` catalog, which doubles
   as the toolkit's regression suite: every kit change must keep it green.

## Start your own catalog

```bash
uvx --from catalog-kit catalog init my-catalog --org com.acme
cd my-catalog
git init && git add -A && git commit -m "chore: scaffold catalog"
mise install
task ci        # gates + mock cycle, green from the first commit
```

The scaffold owns only its catalog. Everything substantive arrives by
reference and stays current without you copying anything:

| Piece | Reference | Updates via |
| --- | --- | --- |
| Gates, mocks, docs, MCP server | `catalog-kit==X` pin in `Taskfile.yml` / `.mcp.json` | Renovate (pypi), minor/patch automerge |
| CI / Pages / release tagging | `uses: hungovercoders/service-catalog/.github/workflows/catalog-*.yml@v<major>` | floating major tag |
| Agent skills | Claude Code plugin | `/plugin marketplace update` |

Merges to main publish each changed service as a `<service>/v<version>`
git tag; implementation and consumer repos pin those via `contracts.lock`
and pull updates through Renovate (see the `implement-service` and
`consume-service` skills).

## The model

A **service** is the unit. It owns artifacts and declares the channels it
produces and consumes, so the catalog is a graph rather than a folder.

Artifacts come in two classes:

| Class | Kinds | Authority | Editable |
| --- | --- | --- | --- |
| **Gated** | `asyncapi`, `openapi`, `data-contract`, `feature` | Contract of record | Only via versioned change, CI enforced |
| **Ungated** | `doc` | Context and rationale | Freely |

Gherkin sits deliberately in the gated class. Feature files are behavioural
contracts, and they are the ones most at risk of being softened to make a
test pass — so they get the same protection as a schema.

Every event is a CloudEvents 1.0 structured envelope with a
`com.<org>.<service>.<event>.v<major>` type; the gates enforce that
breaking changes take majors (`check:compat`) and that every added schema
element is named in the service's features (`check:intent`, no escape
hatch). `task ci` is the definition of green — identical locally, in the
pre-commit hook, and in CI.

## Layout

```
service-catalog/
├── kit/                      catalog-kit: CLI + MCP server, published to PyPI
├── .github/workflows/        catalog-*.yml reusable; thin local callers
├── skills/                   service-catalog, implement-service, consume-service
├── .claude-plugin/           plugin + marketplace manifests
├── .mcp.json                 plugin root, wires server + catalog
├── mocks/                    Microcks stack + per-service example files
└── catalog/                  the example: orders, payments
    └── <service>/
        ├── service.yaml      manifest: version, artifacts, produces, consumes
        ├── asyncapi/  openapi/  data-contracts/  features/  docs/
```

`service.yaml` is the single source of truth for an artifact's version —
there is no second place to forget to update.

## Tools

| Tool | Use |
| --- | --- |
| `list_services()` | Discovery. Start here. |
| `get_service(name)` | Artifact index + produce/consume edges. No file contents. |
| `get_message_schema(service, message)` | One event payload — the cheap call. |
| `get_acceptance_criteria(service)` | Gherkin, labelled binding. |
| `get_artifact(service, path)` | Any declared artifact, with its authority class. |
| `trace_channel(address)` | Who produces and consumes it — i.e. who you break. |
| `search_catalog(query, kind)` | Matching lines, not whole files. |

No write tool exists. Reads are confined to the service directory **and**
to paths the manifest actually declares, so dropping a file into the tree
does not silently expose it.

## Try it

From inside this repo (the dev loop):

```bash
claude --plugin-dir $(pwd)
/mcp                      # confirm the catalog server started
```

Then ask things like:

- "What fields are on OrderPlaced?"
- "Who consumes payments.settled.v2?"
- "Implement the order placement handler" — it should pull the Gherkin first
- "Change OrderPlaced to drop customer_id" — it should refuse and cite consumers

Requires `uv` on PATH.

## Use it from another project

Three ways in, in order of preference.

**1. Install as a plugin from the marketplace (no clone needed).**
Inside any Claude Code session:

```
/plugin marketplace add hungovercoders/service-catalog
/plugin install service-catalog@hungovercoders
```

You get the MCP tools *and* the skills, available in every project.
To pick up a new version later: `/plugin marketplace update hungovercoders`
then reinstall (or `/reload-plugins` after an auto-update).

**2. Load a local clone as a plugin.** From any project directory:

```bash
claude --plugin-dir /path/to/service-catalog
```

Same result as the marketplace install, scoped to that session — useful
when you are iterating on the catalog itself.

**3. Register just the MCP server.** In the consuming project:

```bash
claude mcp add catalog --scope project \
  --env CATALOG_DIR=/path/to/your-catalog/catalog \
  -- uvx --from catalog-kit contracts-mcp
```

This writes the consuming project's `.mcp.json` (use `--scope user` to
make it global instead). `CATALOG_DIR` is the only path the server reads,
so this is also how you point the server at any catalog tree. Tools only;
the skills come with the plugin routes above. (Repos scaffolded by
`catalog init` already carry this wiring.)

Whichever route, verify with `/mcp` and then `list_services()`.

## Contributing and releasing

The gate table, catalog change rules, and the `catalog-kit` release
process live in [CONTRIBUTING.md](CONTRIBUTING.md). Agents: read
[AGENTS.md](AGENTS.md) first.
