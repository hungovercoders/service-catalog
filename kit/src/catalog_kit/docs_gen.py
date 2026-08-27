"""Generate the docs site content from the service manifests.

Reads every <catalog>/*/service.yaml and emits docs/index.md (catalog
graph), docs/SUMMARY.md (literate-nav) and docs/services/<name>/ pages,
then renders the AsyncAPI HTML and ODCS data-contract HTML the pages
embed. A new service appears on the site with no config edits - the
manifests are the only input.

Beyond the per-artifact pages, each producing service gets a readable
message reference (messages.md) generated from its AsyncAPI payload
schemas, with example payloads lifted from the Microcks example files
when a mocks directory is present. The index and service pages draw the
catalog graph as mermaid; styling is class-based only, because Material
initialises mermaid at its default strict security level (no click
interactions) and injects its own theme CSS for both colour schemes.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import yaml

from .pins import ASYNCAPI_CLI, ASYNCAPI_HTML_TEMPLATE, DATACONTRACT_CLI

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


def write_assets(docs: Path) -> None:
    assets = docs / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "extra.css").write_text(EXTRA_CSS)


def mermaid_flow(
    manifests: list[dict],
    index: dict[str, dict],
    consumers: dict[str, list[dict]],
    focus: str | None = None,
) -> list[str]:
    """The catalog graph as fenced mermaid markdown lines.

    Without a focus: every service, grouped into domain subgraphs, with a
    labelled edge per consumed channel and a dashed edge to a sink node
    per channel nobody consumes yet. With a focus: only the edges that
    touch that service, and the service itself outlined.
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
        arrow = f"-. {address} .->" if dashed else f"-- {address} -->"
        lines.append(f"    {node_id(producer)} {arrow} {node_id(target)}")

    service_nodes = ",".join(node_id(m["name"]) for m in shown)
    if service_nodes:
        lines.append(f"    class {service_nodes} service")
    if sinks:
        lines.append(f"    class {','.join(sinks)} sink")
    if focus:
        lines.append(f"    class {node_id(focus)} focus")
    lines += [
        "    classDef service stroke-width:2px",
        "    classDef sink stroke-dasharray:4 3,opacity:0.65",
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
    lines += ["</div>", "", "## Event flow", ""]
    lines += mermaid_flow(manifests, index, consumers)

    http = [
        (m, a)
        for m in manifests
        for a in m.get("artifacts") or []
        if a["kind"] == "openapi"
    ]
    if http:
        links = ", ".join(
            f"[{m['title']}](services/{m['name']}/{Path(a['path']).stem}.md)"
            for m, a in http
        )
        lines += ["", f"Synchronous HTTP APIs (not in the event flow): {links}."]
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


def write_messages(
    m: dict,
    index: dict[str, dict],
    consumers: dict[str, list[dict]],
    examples: dict[str, list[tuple[str, str]]],
    out: Path,
) -> bool:
    """The service's readable message reference; returns False when it has none."""
    name = m["name"]
    produced = [a for a in m.get("produces") or [] if a in index]
    if not produced:
        return False

    first = index[produced[0]]
    lines = [
        f"# {m['title']} — messages",
        "",
        f"Every message {m['title']} publishes, generated from",
        f"[`{first['artifact_path']}`]({rel_artifact(name, first['artifact_path'])})"
        f" @ {first['artifact_version']}.",
        "All payloads are CloudEvents 1.0 structured envelopes; the domain",
        "payload sits in `data`.",
    ]
    for address in produced:
        info = index[address]
        lines += ["", f"## `{address}` {{ #{anchor(address)} }}", ""]
        if info["description"]:
            lines += [info["description"], ""]
        for msg_name, message in info["messages"]:
            if len(info["messages"]) > 1:
                lines += [f"### {msg_name}", ""]
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
                lines += ["", f'??? example "Example — {case}"', "", "    ```json"]
                lines += [f"    {row}" for row in payload_str.splitlines()]
                lines += ["    ```"]
    (out / "messages.md").write_text("\n".join(lines) + "\n")
    return True


def split_feature(text: str) -> tuple[str, str, list[tuple[str, str]]]:
    """(feature title, preamble incl. Background, [(scenario title, block)])."""
    feature_title = ""
    if match := re.search(r"^Feature:\s*(.+)$", text, re.MULTILINE):
        feature_title = match.group(1).strip()
    starts = [
        (match.start(), match.group(2).strip())
        for match in re.finditer(
            r"^([ \t]*)(?:Scenario Outline|Scenario):[ \t]*(.+)$", text, re.MULTILINE
        )
    ]
    if not starts:
        return feature_title, text.rstrip(), []
    preamble = text[: starts[0][0]].rstrip()
    scenarios = []
    for i, (pos, title) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        scenarios.append((title, text[pos:end].rstrip()))
    return feature_title, preamble, scenarios


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

    if m.get("produces") or m.get("consumes"):
        lines += ["", "## At a glance", ""]
        lines += mermaid_flow(manifests, index, consumers, focus=name)

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
        lines.append(
            f"| {icon} {a['kind']} | [`{a['path']}`]({rel_artifact(name, a['path'])}) "
            f"| {version} | {badge} | {a.get('summary', '')} |"
        )

    examples = load_examples(mocks_dir, name)
    has_messages = write_messages(m, index, consumers, examples, out)

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
    (out / "index.md").write_text("\n".join(lines) + "\n")

    nav = [
        f"    - {m['title']}:",
        f"        - [Overview](services/{name}/index.md)",
    ]
    kinds = {}
    for a in m.get("artifacts") or []:
        kinds.setdefault(a["kind"], []).append(a)

    for a in kinds.get("openapi", []):
        stem = Path(a["path"]).stem
        (out / f"{stem}.md").write_text(
            f"# {m['title']} — HTTP contract\n\n"
            f"Contract of record: [`{a['path']}`]({rel_artifact(name, a['path'])}) "
            f"@ {a['version']}\n\n"
            f'<swagger-ui src="{rel_artifact(name, a["path"])}"/>\n'
        )
        nav.append(f"        - [HTTP (OpenAPI)](services/{name}/{stem}.md)")

    for a in kinds.get("asyncapi", []):
        stem = Path(a["path"]).stem
        reference = (
            "Readable message reference: [Messages](messages.md).\n\n"
            if has_messages
            else ""
        )
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

    if has_messages:
        nav.append(f"        - [Messages](services/{name}/messages.md)")

    for a in kinds.get("data-contract", []):
        stem = Path(a["path"]).stem
        odcs = yaml.safe_load((catalog / name / a["path"]).read_text()) or {}
        title = odcs.get("name", stem)
        (out / f"{stem}.md").write_text(
            f"# {m['title']} — {title}\n\n"
            f"Contract of record: [`{a['path']}`]({rel_artifact(name, a['path'])}) "
            f"@ {a['version']}\n\n"
            f'<iframe src="../datacontract-html/{stem}.html" '
            f'style="width:100%;height:85vh;border:none;" loading="lazy" '
            f'title="{title}"></iframe>\n'
        )
        nav.append(f"        - [Data: {title}](services/{name}/{stem}.md)")

    features = kinds.get("feature", [])
    if features:
        body = [f"# {m['title']} — acceptance criteria"]
        for a in features:
            title, preamble, scenarios = split_feature(
                (catalog / name / a["path"]).read_text()
            )
            body += [
                "",
                f"## {title or Path(a['path']).stem}",
                "",
                f"Binding, versioned at {a['version']} — contract of record: "
                f"[`{a['path']}`]({rel_artifact(name, a['path'])})",
                "",
                "```gherkin",
                preamble,
                "```",
            ]
            for scenario_title, block in scenarios:
                body += ["", f"### {scenario_title}", "", "```gherkin", block, "```"]
        (out / "features.md").write_text("\n".join(body) + "\n")
        nav.append(f"        - [Acceptance criteria](services/{name}/features.md)")

    for a in kinds.get("doc", []):
        stem = Path(a["path"]).stem
        (out / f"{stem}.md").write_text(f"--8<-- \"catalog/{name}/{a['path']}\"\n")
        label = doc_label(catalog / name / a["path"], a.get("summary", ""))
        nav.append(f"        - [{label}](services/{name}/{stem}.md)")

    return nav


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
    write_index(manifests, index, consumers, docs)

    nav = ["- [Overview](index.md)", "- Services:"]
    for m in manifests:
        nav += write_service(
            m, manifests, catalog, docs, index, consumers, Path(mocks_dir)
        )
    (docs / "SUMMARY.md").write_text("\n".join(nav) + "\n")
    print(f"generated docs for {len(manifests)} service(s)")

    if html:
        for m in manifests:
            render_html(m, catalog, docs)
    return 0
