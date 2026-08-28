"""Generate the docs site content from the service manifests.

Reads every <catalog>/*/service.yaml and emits docs/index.md (catalog
graph), docs/SUMMARY.md (literate-nav) and docs/services/<name>/ pages,
then renders the AsyncAPI HTML and ODCS data-contract HTML the pages
embed. A new service appears on the site with no config edits - the
manifests are the only input.

Beyond the per-artifact pages, each service gets a unified message
reference (messages.md) treating its whole surface the same way -
commands and queries from its OpenAPI operations, events from its
AsyncAPI payload schemas, and data products from its ODCS contracts -
with example exchanges lifted from the Microcks example files when a
mocks directory is present, and an ER diagram per ODCS contract ahead
of its field tables.

The index and service pages draw the catalog graph as mermaid with
click targets on every service and data-product node. Material
initialises mermaid at its default strict security level, where click
bindings are inert - so on a full generate the graph fences are
pre-rendered headlessly (mermaid-cli, securityLevel loose) into one
inline SVG per colour scheme, toggled by CSS, and the clicks become
real links. With --no-html the fences stay as-is and Material renders
them theme-synced but unlinked.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from .pins import ASYNCAPI_CLI, ASYNCAPI_HTML_TEMPLATE, DATACONTRACT_CLI, MERMAID_CLI

KIND_ICONS = {
    "asyncapi": ":material-transit-connection-variant:",
    "openapi": ":material-api:",
    "data-contract": ":material-table:",
    "feature": ":material-check-decagram:",
    "doc": ":material-file-document-outline:",
}

# Referenced from mkdocs.yml (extra_css), so it must exist on every run,
# --no-html included. Chips lean on Material's CSS variables so both
# colour schemes stay legible without media queries.
EXTRA_CSS = """\
.sc-chip {
  display: inline-block; border-radius: .8em; padding: .05em .6em;
  font-size: .68rem; font-weight: 700; letter-spacing: .02em;
  background: var(--md-default-fg-color--lightest);
  color: var(--md-default-fg-color--light);
  white-space: nowrap; vertical-align: middle;
}
.sc-chip--gated {
  background: var(--md-accent-fg-color--transparent);
  color: var(--md-accent-fg-color);
}
.sc-kw {
  display: inline-block;
  min-width: 3.6rem;
  text-align: right;
  margin-right: .5rem;
  font-weight: 700;
  letter-spacing: .02em;
  color: var(--md-accent-fg-color);
}
/* Phase hues are Material's own note/warning/success admonition colours,
   identical in both schemes. */
.sc-kw--given { color: #448aff; }
.sc-kw--when { color: #ff9100; }
.sc-kw--then { color: #00c853; }
.md-typeset .sc-scenario {
  border: 1px solid var(--md-default-fg-color--lightest);
  border-left: 4px solid var(--md-accent-fg-color);
  border-radius: .2rem;
  padding: .6rem 1rem;
  margin: .8rem 0 1.6rem;
}
.md-typeset .sc-scenario > ul {
  list-style: none;
  margin-left: 0;
  padding-left: 0;
}
.md-typeset .sc-scenario > ul > li {
  margin: .35rem 0;
}
/* Pre-rendered graph SVGs: one per colour scheme, swapped on Material's
   body attribute so the baked themes track the palette toggle. */
.sc-diagram svg { max-width: 100%; height: auto; }
.sc-diagram--dark { display: none; }
[data-md-color-scheme="slate"] .sc-diagram--light { display: none; }
[data-md-color-scheme="slate"] .sc-diagram--dark { display: block; }
"""


def load_manifests(catalog: Path) -> list[dict]:
    return [
        yaml.safe_load(p.read_text())
        for p in sorted(catalog.glob("*/service.yaml"))
    ]


def rel_artifact(name: str, path: str) -> str:
    """Path from docs/services/<name>/ to the raw artifact via the docs/catalog symlink."""
    return f"../../catalog/{name}/{path}"


def clean(text: str) -> str:
    return " ".join(text.split())


def node_id(name: str) -> str:
    """A mermaid-safe node id."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def anchor(address: str) -> str:
    """Deterministic heading id for a channel address."""
    return address.replace(".", "-")


def channel_index(
    manifests: list[dict], catalog: Path
) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """Producer-side index of every published channel, plus its consumers.

    Returns (index, consumers): index maps a channel address to the
    producing service and the message schemas its AsyncAPI publishes on
    it; consumers maps an address to the manifests that list it under
    `consumes`. lint:manifest guarantees produces/consumes resolve, so
    pages degrade gracefully rather than fail on a gap.
    """
    index: dict[str, dict] = {}
    for m in manifests:
        for a in m.get("artifacts") or []:
            if a["kind"] != "asyncapi":
                continue
            doc = yaml.safe_load((catalog / m["name"] / a["path"]).read_text()) or {}
            channels = doc.get("channels") or {}
            messages = (doc.get("components") or {}).get("messages") or {}
            for op_key, op in (doc.get("operations") or {}).items():
                if op.get("action") != "send":
                    continue
                chan_key = (op.get("channel") or {}).get("$ref", "").rsplit("/", 1)[-1]
                channel = channels.get(chan_key) or {}
                address = channel.get("address")
                if not address:
                    continue
                index[address] = {
                    "service": m["name"],
                    "title": m["title"],
                    "artifact_path": a["path"],
                    "artifact_version": a.get("version"),
                    "op_name": op_key,
                    "description": clean(op.get("description", "")),
                    "messages": [
                        (name, messages.get(name) or {})
                        for name in (channel.get("messages") or {})
                    ],
                }
    consumers: dict[str, list[dict]] = {}
    for m in manifests:
        for address in m.get("consumes") or []:
            consumers.setdefault(address, []).append(m)
    return index, consumers


def surface_index(manifests: list[dict], catalog: Path) -> dict[str, dict]:
    """Per-service HTTP operations and data products, for the graph views
    and overview lists. messages.md re-parses the same files for its full
    schema detail; this is just the shape of the surface."""
    out: dict[str, dict] = {}
    for m in manifests:
        ops: list[tuple[str, str, str]] = []
        data: list[tuple[str, str]] = []
        for a in m.get("artifacts") or []:
            path = catalog / m["name"] / a["path"]
            if a["kind"] == "openapi":
                doc = yaml.safe_load(path.read_text()) or {}
                for path_, methods in (doc.get("paths") or {}).items():
                    for method, op in (methods or {}).items():
                        if method in {"get", "post", "put", "patch", "delete"}:
                            op_id = op.get("operationId") or f"{method} {path_}"
                            ops.append((op_id, method, path_))
            elif a["kind"] == "data-contract":
                odcs = yaml.safe_load(path.read_text()) or {}
                stem = Path(a["path"]).stem
                data.append((stem, odcs.get("name", stem)))
        out[m["name"]] = {"ops": ops, "data": data}
    return out


def write_assets(docs: Path) -> None:
    assets = docs / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "extra.css").write_text(EXTRA_CSS)


def mermaid_flow(
    manifests: list[dict],
    index: dict[str, dict],
    consumers: dict[str, list[dict]],
    surfaces: dict[str, dict],
    focus: str | None = None,
) -> list[str]:
    """The catalog graph as fenced mermaid markdown lines.

    The whole message surface, not just events: a labelled edge per
    consumed channel (dashed to a sink node when nobody consumes it yet),
    HTTP operations as edges from a Clients node, and data products as
    cylinder nodes fed by their service. Without a focus, services group
    into domain subgraphs; with one, only that service's edges appear and
    the service itself is outlined.
    """
    titles = {m["name"]: m["title"] for m in manifests}
    edges: list[tuple[str, str, str, bool]] = []  # (producer, consumer/sink, address, dashed)
    sinks: dict[str, str] = {}  # sink node id -> address
    for address, info in sorted(index.items()):
        consuming = [c["name"] for c in consumers.get(address, [])]
        for consumer in consuming:
            edges.append((info["service"], consumer, address, False))
        if not consuming:
            sink = f"sink_{node_id(address)}"
            sinks[sink] = address
            edges.append((info["service"], sink, address, True))
    if focus:
        edges = [e for e in edges if focus in (e[0], e[1])]
        sinks = {s: a for s, a in sinks.items() if any(e[1] == s for e in edges)}

    used = {e[0] for e in edges} | {e[1] for e in edges if e[1] not in sinks}
    if focus:
        used.add(focus)
        shown = [m for m in manifests if m["name"] in used]
    else:
        shown = manifests

    lines = ["```mermaid", "flowchart LR"]
    if focus:
        for m in shown:
            lines.append(f'    {node_id(m["name"])}["{titles[m["name"]]}"]')
    else:
        domains: dict[str, list[dict]] = {}
        for m in shown:
            domains.setdefault(m["domain"], []).append(m)
        for i, (domain, members) in enumerate(sorted(domains.items())):
            lines.append(f'    subgraph d{i}["{domain}"]')
            for m in members:
                lines.append(f'        {node_id(m["name"])}["{titles[m["name"]]}"]')
            lines.append("    end")
    for sink in sinks:
        lines.append(f'    {sink}["no consumer yet"]')
    for producer, target, address, dashed in edges:
        # Dashed edges use the pipe label form: mermaid's flowchart lexer
        # rejects a "." inside `-. label .->`, and every address has dots.
        arrow = f"-.->|{address}|" if dashed else f"-- {address} -->"
        lines.append(f"    {node_id(producer)} {arrow} {node_id(target)}")

    # The rest of the message surface: HTTP entry points and data products.
    targets = [m for m in shown if not focus or m["name"] == focus]
    http_edges = [
        (m["name"], op_id)
        for m in targets
        for op_id, _, _ in surfaces.get(m["name"], {}).get("ops", [])
    ]
    data_nodes = [
        (m["name"], stem, title)
        for m in targets
        for stem, title in surfaces.get(m["name"], {}).get("data", [])
    ]
    if http_edges:
        lines.append('    clients(["Clients"])')
        for svc, op_id in http_edges:
            lines.append(f"    clients -- {op_id} --> {node_id(svc)}")
    dp_ids = []
    for svc, stem, title in data_nodes:
        dp = f"dp_{node_id(svc)}_{node_id(stem)}"
        dp_ids.append(dp)
        lines.append(f'    {dp}[("{title}")]')
        lines.append(f"    {node_id(svc)} --> {dp}")

    # Click targets make the graph a navigation surface once the fence is
    # pre-rendered to SVG (render_graph_svgs); Material's strict-mode
    # mermaid ignores them harmlessly in the --no-html fallback. URLs are
    # relative to the final directory-URL page: site root for the index,
    # services/<focus>/ for a focus view.
    for m in shown:
        if m["name"] == focus:
            continue
        href = f"../{m['name']}/" if focus else f"services/{m['name']}/"
        lines.append(f'    click {node_id(m["name"])} "{href}" "{titles[m["name"]]}"')
    for svc, stem, title in data_nodes:
        base = "messages/" if focus else f"services/{svc}/messages/"
        lines.append(
            f'    click dp_{node_id(svc)}_{node_id(stem)} '
            f'"{base}#dc-{anchor(stem)}" "{title}"'
        )

    service_nodes = ",".join(node_id(m["name"]) for m in shown)
    if service_nodes:
        lines.append(f"    class {service_nodes} service")
    if sinks:
        lines.append(f"    class {','.join(sinks)} sink")
    if http_edges:
        lines.append("    class clients external")
    if dp_ids:
        lines.append(f"    class {','.join(dp_ids)} external")
    if focus:
        lines.append(f"    class {node_id(focus)} focus")
    lines += [
        "    classDef service stroke-width:2px",
        "    classDef sink stroke-dasharray:4 3,opacity:0.65",
        "    classDef external opacity:0.8",
    ]
    if focus:
        lines.append("    classDef focus stroke-width:3px")
    lines.append("```")
    return lines


def service_card(m: dict) -> list[str]:
    """One Material grid card. The 4-space body indent is load-bearing."""
    name = m["name"]
    return [
        f"-   :material-cube-outline:{{ .lg .middle }} __[{m['title']}](services/{name}/index.md)__",
        "",
        "    ---",
        "",
        f"    {clean(m['summary'])}",
        "",
        "    ---",
        "",
        f"    :material-tag-outline: `v{m.get('version', '—')}` · "
        f":material-domain: {m['domain']} · "
        f":material-account-group: `{m['owner']}`",
        "",
    ]


def write_index(
    manifests: list[dict],
    index: dict[str, dict],
    consumers: dict[str, list[dict]],
    surfaces: dict[str, dict],
    docs: Path,
) -> None:
    lines = [
        "# Service catalog",
        "",
        "Contracts, acceptance criteria and docs for every service, generated",
        "from the `service.yaml` manifests. Gated artifacts are the contract",
        "of record; the [MCP server](https://github.com/hungovercoders/service-catalog)",
        "serves the same catalog to agents.",
        "",
        "## Services",
        "",
        '<div class="grid cards" markdown>',
        "",
    ]
    for m in manifests:
        lines += service_card(m)
    lines += ["</div>", "", "## Message flow", ""]
    lines += mermaid_flow(manifests, index, consumers, surfaces)
    (docs / "index.md").write_text("\n".join(lines) + "\n")


def channel_link(address: str, from_service: str, index: dict[str, dict]) -> str:
    """Markdown link from one service's page to a channel's message reference."""
    info = index.get(address)
    if info is None:
        return f"`{address}`"
    prefix = "" if info["service"] == from_service else f"../{info['service']}/"
    return f"[`{address}`]({prefix}messages.md#{anchor(address)})"


def schema_rows(properties: dict, required: list[str], prefix: str = "") -> list[str]:
    """Markdown table rows for a JSON-schema properties block, one level deep."""
    rows = []
    for prop, schema in (properties or {}).items():
        schema = schema or {}
        type_ = schema.get("type", "—")
        if fmt := schema.get("format"):
            type_ = f"{type_} ({fmt})"
        constraints = []
        if "const" in schema:
            constraints.append(f"always `{schema['const']}`")
        if "enum" in schema:
            constraints.append("one of " + ", ".join(f"`{v}`" for v in schema["enum"]))
        if "minimum" in schema:
            constraints.append(f"≥ {schema['minimum']}")
        if "maximum" in schema:
            constraints.append(f"≤ {schema['maximum']}")
        if "pattern" in schema:
            constraints.append(f"pattern `{schema['pattern']}`")
        if "minItems" in schema:
            constraints.append(f"min {schema['minItems']} item(s)")
        needed = "yes" if prop in (required or []) else "no"
        rows.append(
            f"| `{prefix}{prop}` | {type_} | {needed} | "
            f"{'; '.join(constraints) or '—'} |"
        )
        if schema.get("type") == "object" and not prefix:
            rows += schema_rows(
                schema.get("properties") or {},
                schema.get("required") or [],
                prefix=f"{prop}.",
            )
        elif schema.get("type") == "array" and not prefix:
            items = schema.get("items") or {}
            if items.get("type") == "object":
                rows += schema_rows(
                    items.get("properties") or {},
                    items.get("required") or [],
                    prefix=f"{prop}[].",
                )
    return rows


def schema_table(properties: dict, required: list[str], indent: str = "") -> list[str]:
    header = [
        "| Field | Type | Required | Constraints |",
        "| --- | --- | --- | --- |",
    ]
    return [indent + row for row in header + schema_rows(properties, required)]


def load_examples(mocks_dir: Path, service: str) -> dict[str, list[tuple[str, str]]]:
    """Example payloads per send operation from <mocks>/<service>.events.examples.yaml.

    Anything missing - the directory, the file, the keys - yields {}:
    a catalog without Microcks examples still documents its messages,
    just without the example blocks.
    """
    path = mocks_dir / f"{service}.events.examples.yaml"
    if not path.is_file():
        return {}
    doc = yaml.safe_load(path.read_text()) or {}
    examples: dict[str, list[tuple[str, str]]] = {}
    for op_key, cases in (doc.get("operations") or {}).items():
        op = op_key.split()[-1]
        for case, body in (cases or {}).items():
            payload = ((body or {}).get("eventMessage") or {}).get("payload")
            if payload:
                examples.setdefault(op, []).append((case, payload))
    return examples


def load_rest_examples(
    mocks_dir: Path, service: str
) -> dict[str, list[tuple[str, str | None, str | None, str | None]]]:
    """Example exchanges per 'METHOD /path' from <mocks>/<service>.rest.examples.yaml.

    Same silent fallback as load_examples: no file, no examples.
    """
    path = mocks_dir / f"{service}.rest.examples.yaml"
    if not path.is_file():
        return {}
    doc = yaml.safe_load(path.read_text()) or {}
    examples: dict[str, list[tuple[str, str | None, str | None, str | None]]] = {}
    for op_key, cases in (doc.get("operations") or {}).items():
        for case, body in (cases or {}).items():
            request = (body or {}).get("request") or {}
            response = (body or {}).get("response") or {}
            examples.setdefault(op_key, []).append(
                (case, request.get("body"), response.get("status"),
                 response.get("body"))
            )
    return examples


def deref(doc: dict, schema: dict) -> dict:
    """Resolve a local '#/...' $ref chain against the spec document."""
    for _ in range(10):
        ref = (schema or {}).get("$ref", "")
        if not ref.startswith("#/"):
            break
        target: dict = doc
        for part in ref[2:].split("/"):
            target = (target or {}).get(part) or {}
        schema = target
    return schema or {}


def json_body_schema(doc: dict, holder: dict) -> dict:
    content = (holder or {}).get("content") or {}
    return deref(doc, (content.get("application/json") or {}).get("schema") or {})


def source_line(name: str, a: dict) -> str:
    return (
        f"Contract of record: [`{a['path']}`]({rel_artifact(name, a['path'])}) "
        f"@ {a.get('version')}."
    )


def example_block(title: str, parts: list[tuple[str, str]]) -> list[str]:
    """A collapsed example admonition; parts are (caption, json text)."""
    lines = ["", f'??? example "{title}"']
    for caption, text in parts:
        lines += ["", f"    **{caption}**", "", "    ```json"]
        lines += [f"    {row}" for row in text.splitlines()]
        lines += ["    ```"]
    return lines


def http_entry(
    doc: dict,
    method: str,
    path_: str,
    op: dict,
    examples: dict[str, list[tuple[str, str | None, str | None, str | None]]],
) -> list[str]:
    """One command/query section from an OpenAPI operation."""
    op_id = op.get("operationId") or f"{method} {path_}"
    lines = ["", f"### `{op_id}` {{ #op-{anchor(op_id.lower())} }}", ""]
    head = f"`{method.upper()} {path_}`"
    if summary := op.get("summary"):
        head += f" — {clean(summary)}"
    lines += [head, ""]
    if description := op.get("description"):
        lines += [clean(description), ""]

    parameters = op.get("parameters") or []
    if parameters:
        lines += [
            "**Parameters**",
            "",
            "| Name | In | Type | Required |",
            "| --- | --- | --- | --- |",
        ]
        for p in parameters:
            schema = deref(doc, p.get("schema") or {})
            type_ = schema.get("type", "—")
            if fmt := schema.get("format"):
                type_ = f"{type_} ({fmt})"
            lines.append(
                f"| `{p.get('name')}` | {p.get('in')} | {type_} "
                f"| {'yes' if p.get('required') else 'no'} |"
            )
        lines.append("")

    request = json_body_schema(doc, op.get("requestBody") or {})
    if request:
        lines += ["**Request body**", ""]
        lines += schema_table(
            request.get("properties") or {}, request.get("required") or []
        )
        lines.append("")

    responses = op.get("responses") or {}
    if responses:
        lines += ["**Responses**", ""]
        lines += [
            f"- `{status}` — {clean((r or {}).get('description', ''))}"
            for status, r in responses.items()
        ]
        for status, r in responses.items():
            if not str(status).startswith("2"):
                continue
            body = json_body_schema(doc, r or {})
            if body:
                lines += ["", f"**Response body** (`{status}`)", ""]
                lines += schema_table(
                    body.get("properties") or {}, body.get("required") or []
                )
                break

    for case, req_body, status, resp_body in examples.get(
        f"{method.upper()} {path_}", []
    ):
        parts = []
        if req_body:
            parts.append(("Request", req_body))
        if resp_body:
            parts.append((f"Response `{status}`" if status else "Response", resp_body))
        if parts:
            lines += example_block(f"Example — {case}", parts)
    return lines


def http_sections(
    m: dict,
    catalog: Path,
    rest_examples: dict[str, list[tuple[str, str | None, str | None, str | None]]],
) -> tuple[list[str], list[str]]:
    """(commands, queries) sections from the service's OpenAPI artifacts.

    GET reads state, so it documents as a query; everything else changes
    state and documents as a command.
    """
    name = m["name"]
    commands: list[str] = []
    queries: list[str] = []
    for a in m.get("artifacts") or []:
        if a["kind"] != "openapi":
            continue
        doc = yaml.safe_load((catalog / name / a["path"]).read_text()) or {}
        for path_, methods in (doc.get("paths") or {}).items():
            for method, op in (methods or {}).items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                target = queries if method == "get" else commands
                if not target:
                    target += ["", "## " + ("Queries" if method == "get" else "Commands"),
                               "", source_line(name, a), ""]
                target += http_entry(doc, method, path_, op, rest_examples)
    return commands, queries


def event_section(
    m: dict,
    index: dict[str, dict],
    consumers: dict[str, list[dict]],
    examples: dict[str, list[tuple[str, str]]],
) -> list[str]:
    name = m["name"]
    produced = [a for a in m.get("produces") or [] if a in index]
    if not produced:
        return []
    first = index[produced[0]]
    lines = [
        "",
        "## Events",
        "",
        f"Contract of record: [`{first['artifact_path']}`]"
        f"({rel_artifact(name, first['artifact_path'])}) "
        f"@ {first['artifact_version']}. All payloads are CloudEvents 1.0",
        "structured envelopes; the domain payload sits in `data`.",
    ]
    for address in produced:
        info = index[address]
        lines += ["", f"### `{address}` {{ #{anchor(address)} }}", ""]
        if info["description"]:
            lines += [info["description"], ""]
        for msg_name, message in info["messages"]:
            if len(info["messages"]) > 1:
                lines += [f"#### {msg_name}", ""]
            payload = message.get("payload") or {}
            props = payload.get("properties") or {}
            required = payload.get("required") or []
            data = props.get("data") or {}

            meta = [f"**Message:** {msg_name}"]
            if title := message.get("title"):
                meta[-1] += f" — {title}"
            if event_type := (props.get("type") or {}).get("const"):
                meta.append(f"**Event type:** `{event_type}`")
            consuming = consumers.get(address, [])
            if consuming:
                meta.append(
                    "**Consumed by:** "
                    + ", ".join(
                        f"[{c['title']}](../{c['name']}/index.md)" for c in consuming
                    )
                )
            else:
                meta.append("**Consumed by:** no one in the catalog yet")
            lines += [" · ".join(meta), ""]

            lines += schema_table(
                data.get("properties") or {}, data.get("required") or []
            )

            envelope = {k: v for k, v in props.items() if k != "data"}
            if envelope:
                lines += ["", '??? info "CloudEvents envelope"', ""]
                lines += schema_table(envelope, required, indent="    ")

            for case, payload_str in examples.get(info["op_name"], []):
                lines += example_block(f"Example — {case}", [("Payload", payload_str)])
    return lines


def odcs_rows(properties: list[dict], prefix: str = "") -> list[str]:
    """Markdown table rows for an ODCS property list, one level deep."""
    rows = []
    for p in properties or []:
        type_ = p.get("logicalType", "—")
        if physical := p.get("physicalType"):
            type_ = f"{type_} ({physical})"
        keys = [
            label
            for label, flag in [
                ("primary key", p.get("primaryKey")),
                ("unique", p.get("unique")),
                ("partition", p.get("partitioned")),
            ]
            if flag
        ]
        constraints = []
        for q in p.get("quality") or []:
            if values := q.get("validValues"):
                constraints.append("one of " + ", ".join(f"`{v}`" for v in values))
            elif (minimum := q.get("mustBeGreaterThanOrEqualTo")) is not None:
                constraints.append(f"≥ {minimum}")
            elif rule := q.get("rule"):
                constraints.append(rule)
        rows.append(
            f"| `{prefix}{p.get('name')}` | {type_} "
            f"| {'yes' if p.get('required') else 'no'} "
            f"| {', '.join(keys) or '—'} | {'; '.join(constraints) or '—'} |"
        )
        if p.get("properties") and not prefix:
            rows += odcs_rows(p["properties"], prefix=f"{p.get('name')}.")
    return rows


def odcs_er(odcs: dict) -> list[str]:
    """The contract's schema objects as a fenced mermaid ER diagram.

    One entity per object, top-level properties only with their logical
    type and PK/UK markers - the shape at a glance; the field tables
    below carry nesting and constraints. Empty when nothing would show.
    """
    entities: list[tuple[str, list[str]]] = []
    for obj in odcs.get("schema") or []:
        rows = []
        for p in obj.get("properties") or []:
            type_ = str(p.get("logicalType") or "unknown")
            key = "PK" if p.get("primaryKey") else "UK" if p.get("unique") else ""
            rows.append(f"        {type_} {p.get('name')} {key}".rstrip())
        if rows:
            entities.append((obj.get("physicalName") or obj.get("name"), rows))
    if not entities:
        return []
    lines = ["```mermaid", "erDiagram"]
    for entity, rows in entities:
        lines += [f"    {entity} {{"] + rows + ["    }"]
    lines.append("```")
    return lines


def data_section(m: dict, catalog: Path) -> list[str]:
    name = m["name"]
    lines: list[str] = []
    for a in m.get("artifacts") or []:
        if a["kind"] != "data-contract":
            continue
        odcs = yaml.safe_load((catalog / name / a["path"]).read_text()) or {}
        stem = Path(a["path"]).stem
        if not lines:
            lines += ["", "## Data", ""]
        lines += [f"### {odcs.get('name', stem)} {{ #dc-{anchor(stem)} }}", ""]
        lines += [source_line(name, a), ""]
        if purpose := (odcs.get("description") or {}).get("purpose"):
            lines += [clean(purpose), ""]
        if er := odcs_er(odcs):
            lines += er + [""]
        for obj in odcs.get("schema") or []:
            physical = obj.get("physicalName") or obj.get("name")
            head = f"**`{physical}`**"
            if description := obj.get("description"):
                head += f" — {clean(description)}"
            lines += [
                head,
                "",
                "| Field | Type | Required | Key | Constraints |",
                "| --- | --- | --- | --- | --- |",
            ]
            lines += odcs_rows(obj.get("properties") or [])
            lines.append("")
    return lines


def write_messages(
    m: dict,
    catalog: Path,
    index: dict[str, dict],
    consumers: dict[str, list[dict]],
    examples: dict[str, list[tuple[str, str]]],
    rest_examples: dict[str, list[tuple[str, str | None, str | None, str | None]]],
    out: Path,
) -> bool:
    """The service's unified message reference - commands, queries, events
    and data products, each with the same field-table treatment. Returns
    False when the service exposes none of them."""
    commands, queries = http_sections(m, catalog, rest_examples)
    events = event_section(m, index, consumers, examples)
    data = data_section(m, catalog)
    if not (commands or queries or events or data):
        return False

    lines = [
        f"# {m['title']} — messages",
        "",
        f"Everything {m['title']} exchanges — commands and queries over HTTP,",
        "events on its channels, and the data products it publishes —",
        "generated from the service's contracts.",
    ]
    lines += commands + queries + events + data
    (out / "messages.md").write_text("\n".join(lines).rstrip("\n") + "\n")
    return True


STEP_RE = re.compile(r"^(Given|When|Then|And|But|\*)\s+(.+)$")
SCENARIO_RE = re.compile(r"^[ \t]*(?:Scenario Outline|Scenario):[ \t]*(.+)$")


def step_inline(text: str) -> str:
    """Step text as markdown: quoted values and <placeholders> become code."""
    text = re.sub(r"<([^<>\s]+)>", r"`<\1>`", text)
    return re.sub(r'"([^"]*)"', r"`\1`", text)


def gherkin_table(rows: list[str], indent: str = "") -> list[str]:
    """A Gherkin data table as a markdown table, first row as header."""
    cells = [
        [step_inline(c.strip()) for c in row.strip().strip("|").split("|")]
        for row in rows
    ]
    out = [
        indent + "| " + " | ".join(cells[0]) + " |",
        indent + "|" + " --- |" * len(cells[0]),
    ]
    out += [indent + "| " + " | ".join(r) + " |" for r in cells[1:]]
    return out


PHASES = {"Given": "given", "When": "when", "Then": "then"}


def render_steps(block_lines: list[str], indent: str = "") -> list[str]:
    """Gherkin step lines as styled markdown - keyword chips, real tables.

    Keywords colour by phase; And/But/* inherit the phase of the preceding
    primary keyword, and steps before any primary keyword read as setup.
    """
    out: list[str] = []
    table: list[str] = []
    phase = "given"

    def flush_table() -> None:
        if table:
            out.append(indent)
            out.extend(gherkin_table(table, indent))
            table.clear()

    for raw in block_lines:
        line = raw.strip()
        if line.startswith("|"):
            table.append(line)
            continue
        flush_table()
        if not line:
            continue
        if step := STEP_RE.match(line):
            keyword, rest = step.groups()
            phase = PHASES.get(keyword, phase)
            out.append(
                f"{indent}- **{keyword}**{{ .sc-kw .sc-kw--{phase} }} "
                f"{step_inline(rest)}"
            )
        elif line.startswith("Examples:"):
            out += [indent, f"{indent}**Examples**"]
        elif line.startswith("#"):
            out += [indent, f"{indent}*{line.lstrip('# ')}*"]
        else:
            out += [indent, indent + step_inline(line)]
    flush_table()
    return [row.rstrip() for row in out]


def parse_feature(
    text: str,
) -> tuple[str, list[str], list[str], list[tuple[str, list[str]]]]:
    """(title, description lines, background lines, [(scenario title, lines)])."""
    title = ""
    description: list[str] = []
    background: list[str] = []
    scenarios: list[tuple[str, list[str]]] = []
    section: list[str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Feature:"):
            title = line.removeprefix("Feature:").strip()
            section = description
        elif line.startswith("Background:"):
            section = background
        elif match := SCENARIO_RE.match(raw):
            scenarios.append((match.group(1).strip(), []))
            section = scenarios[-1][1]
        elif section is not None:
            section.append(raw)
    return title, description, background, scenarios


def doc_label(src: Path, summary: str) -> str:
    """Nav label for an ungated doc: its own H1, else the summary, else the stem."""
    for line in src.read_text().splitlines():
        if match := re.match(r"#\s+(.+)", line):
            return match.group(1).strip()
    return clean(summary).rstrip(".") if summary else src.stem


def write_service(
    m: dict,
    manifests: list[dict],
    catalog: Path,
    docs: Path,
    index: dict[str, dict],
    consumers: dict[str, list[dict]],
    surfaces: dict[str, dict],
    mocks_dir: Path,
) -> list[str]:
    """Write one service's pages; return its literate-nav lines."""
    name = m["name"]
    out = docs / "services" / name
    out.mkdir(parents=True)

    meta = [
        f":material-tag-outline: **{m.get('version', '—')}**",
        f":material-domain: {m['domain']}",
        f":material-account-group: `{m['owner']}`",
    ]
    if repo := m.get("implementationRepo"):
        meta.append(
            f":material-source-repository: [{repo}](https://github.com/{repo})"
        )
    lines = [
        f"# {m['title']}",
        "",
        " · ".join(meta),
        "",
        clean(m["summary"]),
    ]

    surface = surfaces.get(name) or {"ops": [], "data": []}
    if m.get("produces") or m.get("consumes") or surface["ops"] or surface["data"]:
        lines += ["", "## At a glance", ""]
        lines += mermaid_flow(manifests, index, consumers, surfaces, focus=name)

    lines += [
        "",
        "## Artifacts",
        "",
        "| Kind | Artifact | Version | Class | Summary |",
        "| --- | --- | --- | --- | --- |",
    ]
    for a in m.get("artifacts") or []:
        gated = a.get("gated", a["kind"] != "doc")
        # attr_list attaches to an inline element, so the chip rides a <strong>.
        badge = (
            "**gated**{ .sc-chip .sc-chip--gated }"
            if gated
            else "**context**{ .sc-chip }"
        )
        version = a.get("version") or "—"
        icon = KIND_ICONS.get(a["kind"], "")
        # Link the rendered page, not the raw file - the page carries the
        # contract-of-record link for anyone who wants the artifact itself.
        page = "features.md" if a["kind"] == "feature" else f"{Path(a['path']).stem}.md"
        lines.append(
            f"| {icon} {a['kind']} | [`{a['path']}`]({page}) "
            f"| {version} | {badge} | {a.get('summary', '')} |"
        )

    examples = load_examples(mocks_dir, name)
    rest_examples = load_rest_examples(mocks_dir, name)
    has_messages = write_messages(
        m, catalog, index, consumers, examples, rest_examples, out
    )

    for heading, wanted in [("Commands", lambda mth: mth != "get"),
                            ("Queries", lambda mth: mth == "get")]:
        ops = [(o, mth, p_) for o, mth, p_ in surface["ops"] if wanted(mth)]
        if ops:
            lines += ["", f"## {heading}", ""]
            lines += [
                f"- [`{op_id}`](messages.md#op-{anchor(op_id.lower())}) — "
                f"`{mth.upper()} {path_}`"
                for op_id, mth, path_ in ops
            ]

    for heading, key in [("Produces", "produces"), ("Consumes", "consumes")]:
        addresses = m.get(key) or []
        if not addresses:
            continue
        lines += ["", f"## {heading}", ""]
        for address in addresses:
            link = channel_link(address, name, index)
            info = index.get(address)
            if key == "produces":
                consuming = consumers.get(address, [])
                if consuming:
                    others = ", ".join(
                        f"[{c['title']}](../{c['name']}/index.md)" for c in consuming
                    )
                    lines.append(f"- {link} — consumed by {others}")
                else:
                    lines.append(f"- {link} — no consumer in the catalog")
            elif info is not None:
                lines.append(
                    f"- {link} — produced by "
                    f"[{info['title']}](../{info['service']}/index.md)"
                )
            else:
                lines.append(f"- {link}")

    if surface["data"]:
        lines += ["", "## Data products", ""]
        lines += [
            f"- [{title}](messages.md#dc-{anchor(stem)})"
            for stem, title in surface["data"]
        ]
    (out / "index.md").write_text("\n".join(lines) + "\n")

    nav = [
        f"    - {m['title']}:",
        f"        - [Overview](services/{name}/index.md)",
    ]
    if has_messages:
        nav.append(f"        - [Messages](services/{name}/messages.md)")
    kinds = {}
    for a in m.get("artifacts") or []:
        kinds.setdefault(a["kind"], []).append(a)

    reference = (
        "Readable message reference: [Messages](messages.md).\n\n"
        if has_messages
        else ""
    )

    for a in kinds.get("openapi", []):
        stem = Path(a["path"]).stem
        (out / f"{stem}.md").write_text(
            f"# {m['title']} — HTTP contract\n\n"
            f"Contract of record: [`{a['path']}`]({rel_artifact(name, a['path'])}) "
            f"@ {a['version']}\n\n"
            f"{reference}"
            f'<swagger-ui src="{rel_artifact(name, a["path"])}"/>\n'
        )
        nav.append(f"        - [HTTP (OpenAPI)](services/{name}/{stem}.md)")

    for a in kinds.get("asyncapi", []):
        stem = Path(a["path"]).stem
        (out / f"{stem}.md").write_text(
            f"# {m['title']} — event contract\n\n"
            f"Contract of record: [`{a['path']}`]({rel_artifact(name, a['path'])}) "
            f"@ {a['version']}\n\n"
            f"{reference}"
            f'<iframe src="../asyncapi-html/index.html" '
            f'style="width:100%;height:85vh;border:none;" loading="lazy" '
            f'title="{m["title"]} events"></iframe>\n'
        )
        nav.append(f"        - [Events (AsyncAPI)](services/{name}/{stem}.md)")

    for a in kinds.get("data-contract", []):
        stem = Path(a["path"]).stem
        odcs = yaml.safe_load((catalog / name / a["path"]).read_text()) or {}
        title = odcs.get("name", stem)
        (out / f"{stem}.md").write_text(
            f"# {m['title']} — {title}\n\n"
            f"Contract of record: [`{a['path']}`]({rel_artifact(name, a['path'])}) "
            f"@ {a['version']}\n\n"
            f"{reference}"
            f'<iframe src="../datacontract-html/{stem}.html" '
            f'style="width:100%;height:85vh;border:none;" loading="lazy" '
            f'title="{title}"></iframe>\n'
        )
        nav.append(f"        - [Data: {title}](services/{name}/{stem}.md)")

    features = kinds.get("feature", [])
    if features:
        body = [f"# {m['title']} — acceptance criteria"]
        for a in features:
            source = (catalog / name / a["path"]).read_text()
            title, description, background, scenarios = parse_feature(source)
            count = len(scenarios)
            body += [
                "",
                f"## {title or Path(a['path']).stem}",
                "",
                f"**{count} scenario{'s' if count != 1 else ''}** — binding, "
                f"versioned at {a['version']} — contract of record: "
                f"[`{a['path']}`]({rel_artifact(name, a['path'])})",
                "",
            ]
            prose = [line.strip() for line in description]
            while prose and not prose[0]:
                prose.pop(0)
            while prose and not prose[-1]:
                prose.pop()
            body += prose
            if background:
                body += ["", '!!! note "Background"', ""]
                body += render_steps(background, indent="    ")
            for scenario_title, scenario_lines in scenarios:
                body += [
                    "",
                    f"### {scenario_title}",
                    "",
                    '<div class="sc-scenario" markdown>',
                    "",
                ]
                body += render_steps(scenario_lines)
                body += ["", "</div>"]
            body += ["", '??? quote "Raw Gherkin"', "", "    ```gherkin"]
            body += ["    " + line for line in source.rstrip().splitlines()]
            body += ["    ```"]
        (out / "features.md").write_text("\n".join(body) + "\n")
        nav.append(f"        - [Acceptance criteria](services/{name}/features.md)")

    for a in kinds.get("doc", []):
        stem = Path(a["path"]).stem
        (out / f"{stem}.md").write_text(f"--8<-- \"catalog/{name}/{a['path']}\"\n")
        label = doc_label(catalog / name / a["path"], a.get("summary", ""))
        nav.append(f"        - [{label}](services/{name}/{stem}.md)")

    return nav


MERMAID_FENCE = re.compile(r"^```mermaid\n(.*?)^```", re.MULTILINE | re.DOTALL)


def mermaid_blocks(catalog: Path, docs: Path) -> list[tuple[Path, int, str]]:
    """(file, line, source) for every mermaid fence the site will render:
    the generated pages, plus the ungated doc sources they snippet-include
    (an ADR's diagram breaks the page just as surely as a generated one)."""
    pages = [
        p
        for p in sorted(docs.rglob("*.md"))
        # docs/catalog symlinks into the source tree; its .md files are
        # covered by the explicit source glob below.
        if not p.is_relative_to(docs / "catalog")
    ]
    sources = sorted(catalog.glob("*/docs/*.md"))
    blocks = []
    for f in pages + sources:
        text = f.read_text()
        for match in MERMAID_FENCE.finditer(text):
            line = text[: match.start()].count("\n") + 1
            blocks.append((f, line, match.group(1)))
    return blocks


def browser_env() -> tuple[dict, dict]:
    """(puppeteer launch config, environment) for a mermaid-cli run. A
    system Chrome is reused when one is found (and puppeteer's own browser
    download is skipped); otherwise puppeteer fetches its own."""
    env = dict(os.environ)
    config: dict = {"args": ["--no-sandbox", "--disable-gpu"]}
    chrome = env.get("PUPPETEER_EXECUTABLE_PATH") or next(
        filter(None, (shutil.which(c) for c in
                      ("google-chrome", "chromium-browser", "chromium", "chrome"))),
        None,
    )
    if chrome:
        config["executablePath"] = chrome
        env["PUPPETEER_SKIP_DOWNLOAD"] = "1"
    return config, env


def render_graph_svgs(docs: Path) -> None:
    """Replace the graph fences on the index and service overview pages
    with pre-rendered inline SVG, one per colour scheme.

    Material's mermaid runs at securityLevel strict, where the graphs'
    click lines are inert; rendered headlessly at loose they become real
    links. Baked SVG no longer follows Material's mermaid theming, hence
    the light/dark pair, toggled by CSS on the palette attribute. A
    render failure fails generation outright - the same guarantee
    check_diagrams gives the fences that remain elsewhere.
    """
    pages = [docs / "index.md"] + sorted((docs / "services").glob("*/index.md"))
    config, env = browser_env()
    with tempfile.TemporaryDirectory() as tmp:
        puppeteer = Path(tmp, "puppeteer.json")
        puppeteer.write_text(json.dumps(config))
        mermaid_conf = Path(tmp, "mermaid.json")
        mermaid_conf.write_text(json.dumps({"securityLevel": "loose"}))
        for page in pages:
            count = 0

            def replace(match: re.Match) -> str:
                nonlocal count
                count += 1
                parts = []
                for theme, scheme in (("default", "light"), ("dark", "dark")):
                    # The id namespaces mermaid's embedded styles, so it
                    # must be unique across every SVG inlined on the page.
                    svg_id = f"sc-{page.parent.name}-g{count}-{scheme}"
                    mmd = Path(tmp, f"{svg_id}.mmd")
                    mmd.write_text(match.group(1))
                    out = Path(tmp, f"{svg_id}.svg")
                    subprocess.run(
                        ["npx", "-y", MERMAID_CLI, "--quiet",
                         "--puppeteerConfigFile", str(puppeteer),
                         "--configFile", str(mermaid_conf),
                         "--theme", theme, "--backgroundColor", "transparent",
                         "--svgId", svg_id,
                         "--input", str(mmd), "--output", str(out)],
                        check=True,
                        env=env,
                    )
                    # A blank line would end the raw-HTML block mid-SVG
                    # when markdown parses the page.
                    svg = re.sub(r"\n\s*\n", "\n", out.read_text()).strip()
                    parts.append(
                        f'<div class="sc-diagram sc-diagram--{scheme}">{svg}</div>'
                    )
                return "\n".join(parts)

            page.write_text(MERMAID_FENCE.sub(replace, page.read_text()))
    print(f"pre-rendered graph SVGs for {len(pages)} page(s)")


def check_diagrams(catalog_dir: str, docs_dir: str) -> int:
    """Parse every mermaid diagram with mermaid-cli.

    mkdocs --strict never parses mermaid - a syntax error only surfaces in
    the viewer's browser, as raw diagram source. This gate renders each
    fence headlessly so that failure lands in CI instead.
    """
    blocks = mermaid_blocks(Path(catalog_dir), Path(docs_dir))
    if not blocks:
        print("no mermaid diagrams found")
        return 0

    config, env = browser_env()
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        config_file = Path(tmp, "puppeteer.json")
        config_file.write_text(json.dumps(config))
        for f, line, source in blocks:
            mmd = Path(tmp, "diagram.mmd")
            mmd.write_text(source)
            run = subprocess.run(
                ["npx", "-y", MERMAID_CLI, "--quiet",
                 "--puppeteerConfigFile", str(config_file),
                 "--input", str(mmd), "--output", str(Path(tmp, "diagram.svg"))],
                capture_output=True,
                text=True,
                env=env,
            )
            if run.returncode:
                failures += 1
                detail = (run.stderr or run.stdout).strip()
                print(f"mermaid FAILED: {f}:{line}\n{detail}\n", file=sys.stderr)
            else:
                print(f"mermaid ok: {f}:{line}")
    if failures:
        print(f"\n{failures} of {len(blocks)} diagram(s) failed to parse.",
              file=sys.stderr)
        return 1
    print(f"\n{len(blocks)} diagram(s) parsed.")
    return 0


def render_html(m: dict, catalog: Path, docs: Path) -> None:
    """Render the AsyncAPI and data-contract HTML a service's pages embed."""
    name = m["name"]
    for a in m.get("artifacts") or []:
        spec = catalog / name / a["path"]
        if a["kind"] == "asyncapi":
            subprocess.run(
                ["npx", "-y", ASYNCAPI_CLI, "generate", "fromTemplate",
                 str(spec), ASYNCAPI_HTML_TEMPLATE,
                 "--output", str(docs / "services" / name / "asyncapi-html"),
                 "--force-write", "--param", "singleFile=true"],
                check=True,
            )
        elif a["kind"] == "data-contract":
            out = docs / "services" / name / "datacontract-html"
            out.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["uvx", "--from", DATACONTRACT_CLI, "datacontract", "export",
                 "html", "--output", str(out / f"{spec.stem}.html"), str(spec)],
                check=True,
            )


def run(
    catalog_dir: str, docs_dir: str, mocks_dir: str = "mocks", html: bool = True
) -> int:
    catalog = Path(catalog_dir)
    docs = Path(docs_dir)
    docs.mkdir(exist_ok=True)
    # The generated pages link raw artifacts through docs/catalog.
    link = docs / "catalog"
    if not link.is_symlink() and not link.exists():
        link.symlink_to(Path("..") / catalog_dir)

    shutil.rmtree(docs / "services", ignore_errors=True)
    manifests = load_manifests(catalog)
    if not manifests:
        raise SystemExit(f"no service manifests found under {catalog_dir}/*/service.yaml")

    write_assets(docs)
    index, consumers = channel_index(manifests, catalog)
    surfaces = surface_index(manifests, catalog)
    write_index(manifests, index, consumers, surfaces, docs)

    nav = ["- [Overview](index.md)", "- Services:"]
    for m in manifests:
        nav += write_service(
            m, manifests, catalog, docs, index, consumers, surfaces, Path(mocks_dir)
        )
    (docs / "SUMMARY.md").write_text("\n".join(nav) + "\n")
    print(f"generated docs for {len(manifests)} service(s)")

    if html:
        render_graph_svgs(docs)
        for m in manifests:
            render_html(m, catalog, docs)
    return 0
