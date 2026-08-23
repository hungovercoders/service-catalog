# Working in this repository (agents)

Read [CONTRIBUTING.md](CONTRIBUTING.md) first — the workflow, gates, and
catalog rules there apply to you exactly as they do to humans. The points
below are the agent-specific sharp edges.

- **The catalog is the authority.** Gated artifacts (AsyncAPI, OpenAPI,
  data contracts, feature files) are never edited to make a check, test, or
  implementation pass. If a gated artifact looks wrong, stop and say so —
  changing it is a deliberate, versioned act with its own gates.
- **Verify through `task`, nothing else.** `task ci` is the definition of
  green. Do not re-implement checks ad hoc or bypass a failing gate; a red
  gate is information, and the negative result gets reported as-is.
- **Read the catalog through the MCP tools when available** (`catalog`
  server: `list_services`, `get_service`, `get_artifact`,
  `get_message_schema`, `get_acceptance_criteria`, `trace_channel`,
  `search_catalog`) rather than grepping files — the tools tell you which
  artifacts are gated and who consumes what.
- **Skills define the deeper processes**: `skills/service-catalog/SKILL.md`
  for authoring conventions and how to work against contracts;
  `skills/implement-service/SKILL.md` for building a real implementation of
  a service (contracts.lock pinning, verification loop, sync).
- **Version everything you touch**: gated artifact ⇒ artifact + service
  version bumps; plugin surface (server, skills, templates) ⇒
  `.claude-plugin/plugin.json` bump. `check:version` and `lint:manifest`
  catch the former; the latter is on you.
