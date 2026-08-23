# service-catalog

A Claude Code plugin that serves a **service catalog** — contracts, specs,
acceptance criteria and docs — as read-only MCP tools, alongside the skill
that says how to use them.

The problem it solves: specs living as files next to an implementation get
quietly amended to match failing code. Here the catalog is reached only
through tools, gated artifacts are labelled as binding, and CI fails any
change to one that does not carry a version bump.

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

## Layout

```
service-catalog/
├── .claude-plugin/
│   ├── plugin.json           manifest ONLY — nothing else here
│   └── marketplace.json
├── skills/service-catalog/   plugin root, auto-discovered
├── server/                   FastMCP server, packaged for uvx
├── scripts/                  version gate
├── .mcp.json                 plugin root, wires server + catalog
└── catalog/
    ├── orders/
    │   ├── service.yaml      manifest: artifacts, produces, consumes
    │   ├── asyncapi/         events
    │   ├── openapi/          sync API
    │   ├── features/         Gherkin acceptance criteria
    │   └── docs/             ADRs, runbook (ungated)
    └── payments/
        ├── service.yaml
        ├── asyncapi/
        ├── data-contracts/   ODCS v3.1
        ├── features/
        └── docs/
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

To consume the catalog from your own project, see
[Use it from another project](#use-it-from-another-project).

Then ask things like:

- "What fields are on OrderPlaced?"
- "Who consumes payments.settled.v1?"
- "Implement the order placement handler" — it should pull the Gherkin first
- "Change OrderPlaced to drop customerId" — it should refuse and cite consumers

Requires `uv` on PATH.

## Use it from another project

Three ways in, in order of preference. All of them need `uv` on PATH;
replace `/path/to/service-catalog` with wherever you cloned this repo.

**1. Install as a plugin from the marketplace (no clone needed).**
Inside any Claude Code session:

```
/plugin marketplace add hungovercoders/service-catalog
/plugin install service-catalog@hungovercoders
```

You get the MCP tools *and* the skill, available in every project.
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
  --env CATALOG_DIR=/path/to/service-catalog/catalog \
  -- uvx --from /path/to/service-catalog/server contracts-mcp
```

This writes the consuming project's `.mcp.json` (use `--scope user` to
make it global instead). Both paths must be absolute — `CATALOG_DIR` is
the only path the server reads, so this is also how you point the server
at a different catalog tree. Tools only; the skill comes with the plugin
routes above.

Whichever route, verify with `/mcp` and then `list_services()`.

## The gate

```bash
python scripts/check_version_bump.py origin/main
```

Fails when a gated artifact's content changes without its `version` moving
in `service.yaml`, and when a gated artifact is quietly dropped from a
manifest. Docs are exempt.

## Known gaps

- The gate checks that a version *moved*, not whether the change was
  breaking. Real teeth means diffing against the last published tag and
  classifying additive vs breaking.
- Nothing verifies that `produces`/`consumes` in a manifest match the
  channels actually declared in that service's AsyncAPI. Easy lint to add.
- Gherkin is served but not executed. Wiring the same feature files into
  the consuming repo's test run is what closes the loop.
- Conventions in the skill are placeholders. Rewrite against how you
  actually name channels.
