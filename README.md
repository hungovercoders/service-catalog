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

```bash
claude --plugin-dir $(pwd)
/mcp                      # confirm the catalog server started
```

Then ask things like:

- "What fields are on OrderPlaced?"
- "Who consumes payments.settled.v1?"
- "Implement the order placement handler" — it should pull the Gherkin first
- "Change OrderPlaced to drop customerId" — it should refuse and cite consumers

Requires `uv` on PATH.

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
