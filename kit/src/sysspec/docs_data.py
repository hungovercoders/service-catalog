"""Emit the normalized specs data the docs site renders from.

Reads every <specs>/*/service.yaml and writes one specs.json - the
manifests, the produces/consumes graph, the whole message surface
(commands and queries from OpenAPI, events with their schemas and
Microcks examples, data products with ER diagrams), structured
acceptance criteria, tag-driven changelogs, and the mermaid charts the
site renders (delivery sequences and ER diagrams, gated by
`sysspec docs diagrams`; the system graphs are react islands). Raw
artifacts are
copied under the site's public/ dir so spec renderers and
contract-of-record links reach them by URL. The manifests are the only
input; a new service appears on the site with no config edits.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import yaml

from .docs_gen import (
    PHASES,
    STEP_RE,
    channel_index,
    channel_sequence,
    clean,
    deref,
    doc_label,
    git_lines,
    json_body_schema,
    load_examples,
    load_manifests,
    load_rest_examples,
    odcs_er,
    parse_feature,
    surface_index,
)


def unfence(lines: list[str]) -> str:
    """A fenced ```mermaid block from docs_gen as a bare chart string."""
    return "\n".join(lines[1:-1])


def flatten_schema(properties: dict, required: list[str], prefix: str = "") -> list[dict]:
    """JSON-schema properties as row dicts, one level deep - the data
    twin of docs_gen.schema_rows."""
    rows = []
    for prop, schema in (properties or {}).items():
        schema = schema or {}
        type_ = schema.get("type", "—")
        if fmt := schema.get("format"):
            type_ = f"{type_} ({fmt})"
        constraints = []
        if "const" in schema:
            constraints.append(f"always {schema['const']!r}")
        if "enum" in schema:
            constraints.append("one of " + ", ".join(str(v) for v in schema["enum"]))
        if "minimum" in schema:
            constraints.append(f"≥ {schema['minimum']}")
        if "maximum" in schema:
            constraints.append(f"≤ {schema['maximum']}")
        if "pattern" in schema:
            constraints.append(f"pattern {schema['pattern']}")
        if "minItems" in schema:
            constraints.append(f"min {schema['minItems']} item(s)")
        rows.append({
            "field": f"{prefix}{prop}",
            "type": type_,
            "required": prop in (required or []),
            "constraints": "; ".join(constraints),
        })
        if schema.get("type") == "object" and not prefix:
            rows += flatten_schema(
                schema.get("properties") or {},
                schema.get("required") or [],
                prefix=f"{prop}.",
            )
        elif schema.get("type") == "array" and not prefix:
            items = schema.get("items") or {}
            if items.get("type") == "object":
                rows += flatten_schema(
                    items.get("properties") or {},
                    items.get("required") or [],
                    prefix=f"{prop}[].",
                )
    return rows


def http_operations(m: dict, specs: Path, rest_examples: dict) -> list[dict]:
    """Every OpenAPI operation as a structured entry; GET documents as a
    query, everything else as a command."""
    ops: list[dict] = []
    for a in m.get("artifacts") or []:
        if a["kind"] != "openapi":
            continue
        doc = yaml.safe_load((specs / m["name"] / a["path"]).read_text()) or {}
        for path_, methods in (doc.get("paths") or {}).items():
            for method, op in (methods or {}).items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                op_id = op.get("operationId") or f"{method} {path_}"
                parameters = []
                for p in op.get("parameters") or []:
                    schema = deref(doc, p.get("schema") or {})
                    type_ = schema.get("type", "—")
                    if fmt := schema.get("format"):
                        type_ = f"{type_} ({fmt})"
                    parameters.append({
                        "name": p.get("name"), "in": p.get("in"),
                        "type": type_, "required": bool(p.get("required")),
                    })
                request = json_body_schema(doc, op.get("requestBody") or {})
                responses = [
                    {"status": str(status),
                     "description": clean((r or {}).get("description", ""))}
                    for status, r in (op.get("responses") or {}).items()
                ]
                response_body = None
                for status, r in (op.get("responses") or {}).items():
                    if str(status).startswith("2"):
                        body = json_body_schema(doc, r or {})
                        if body:
                            response_body = {
                                "status": str(status),
                                "rows": flatten_schema(
                                    body.get("properties") or {},
                                    body.get("required") or [],
                                ),
                            }
                            break
                examples = [
                    {"case": case, "request": req, "status": status, "response": resp}
                    for case, req, status, resp in rest_examples.get(
                        f"{method.upper()} {path_}", []
                    )
                ]
                ops.append({
                    "op_id": op_id,
                    "kind": "query" if method == "get" else "command",
                    "method": method.upper(),
                    "path": path_,
                    "summary": clean(op.get("summary", "")),
                    "description": clean(op.get("description", "")),
                    "artifact_path": a["path"],
                    "artifact_version": a.get("version"),
                    "parameters": parameters,
                    "request_rows": flatten_schema(
                        request.get("properties") or {}, request.get("required") or []
                    ) if request else [],
                    "responses": responses,
                    "response_body": response_body,
                    "examples": examples,
                })
    return ops


def channel_entry(
    address: str, info: dict, consumers: dict, examples: dict
) -> dict:
    """One published channel: producer, consumers, delivery sequence
    diagram, and each message's schema split into data and envelope."""
    consuming = consumers.get(address, [])
    messages = []
    for msg_name, message in info["messages"]:
        payload = message.get("payload") or {}
        props = payload.get("properties") or {}
        data = props.get("data") or {}
        envelope = {k: v for k, v in props.items() if k != "data"}
        messages.append({
            "name": msg_name,
            "title": message.get("title", ""),
            "event_type": (props.get("type") or {}).get("const"),
            "data_rows": flatten_schema(
                data.get("properties") or {}, data.get("required") or []
            ),
            "envelope_rows": flatten_schema(
                envelope, payload.get("required") or []
            ),
        })
    return {
        "address": address,
        "producer": info["service"],
        "producer_title": info["title"],
        "artifact_path": info["artifact_path"],
        "artifact_version": info["artifact_version"],
        "description": info["description"],
        "consumers": [c["name"] for c in consuming],
        "sequence_mermaid": unfence(channel_sequence(address, info, consuming)),
        "messages": messages,
        "examples": [
            {"case": case, "payload": payload}
            for case, payload in examples.get(info["op_name"], [])
        ],
    }


def structured_steps(block_lines: list[str]) -> list[dict]:
    """Gherkin step lines as data: keyword/phase/text steps, tables, and
    prose - the data twin of docs_gen.render_steps."""
    out: list[dict] = []
    table: list[list[str]] = []
    phase = "given"

    def flush_table() -> None:
        if table:
            out.append({"table": table[:]})
            table.clear()

    for raw in block_lines:
        line = raw.strip()
        if line.startswith("|"):
            table.append([c.strip() for c in line.strip("|").split("|")])
            continue
        flush_table()
        if not line:
            continue
        if step := STEP_RE.match(line):
            keyword, rest = step.groups()
            phase = PHASES.get(keyword, phase)
            out.append({"keyword": keyword, "phase": phase, "text": rest})
        elif line.startswith("Examples:"):
            out.append({"heading": "Examples"})
        elif line.startswith("#"):
            out.append({"comment": line.lstrip("# ")})
        else:
            out.append({"prose": line})
    flush_table()
    return out


def feature_data(a: dict, source: str) -> dict:
    title, description, background, scenarios = parse_feature(source)
    prose = [line.strip() for line in description]
    while prose and not prose[0]:
        prose.pop(0)
    while prose and not prose[-1]:
        prose.pop()
    return {
        "title": title or Path(a["path"]).stem,
        "description": prose,
        "background": structured_steps(background),
        "scenarios": [
            {"title": t, "steps": structured_steps(lines)} for t, lines in scenarios
        ],
    }


def changelog_data(m: dict) -> list[dict]:
    """Release history from <name>/v<version> tags, newest first; [] in a
    checkout without tags (fresh scaffold, shallow clone)."""
    name = m["name"]
    releases = list(reversed(release_tags(name)))
    if not releases:
        return []

    def commits(rev_range: str) -> list[str]:
        subjects = git_lines(
            "log", "--no-merges", "--format=%s", rev_range, "--", f"specs/{name}"
        )
        return subjects or [f"no commits under specs/{name}/ in this release"]

    out = []
    if m.get("version") and m["version"] != releases[0][0]:
        out.append({
            "version": m["version"], "date": None, "unreleased": True,
            "commits": commits(f"{releases[0][1]}..HEAD"),
        })
    for i, (version, tag) in enumerate(releases):
        date = (git_lines("log", "-1", "--format=%cs", tag) or [None])[0]
        sha = (git_lines("rev-parse", f"{tag}^{{commit}}") or [None])[0]
        older = releases[i + 1][1] if i + 1 < len(releases) else None
        out.append({
            "version": version, "date": date, "unreleased": False,
            "tag": tag, "sha": sha,
            "commits": commits(f"{older}..{tag}" if older else tag),
        })
    return out


def release_tags(name: str) -> list[tuple[str, str]]:
    """(version, tag) for the service's release tags, oldest first."""
    releases = []
    for tag in git_lines("tag", "-l", f"{name}/v*"):
        version = tag.removeprefix(f"{name}/v")
        parts = version.split(".")
        if all(p.isdigit() for p in parts):
            releases.append((tuple(int(p) for p in parts), version, tag))
    return [(version, tag) for _, version, tag in sorted(releases)]


def artifact_histories(m: dict) -> dict[str, list[dict]]:
    """Per-artifact version history from the service's release tags: the
    manifest as it stood at each tag, recording when each artifact version
    first shipped. Pre-rename tags carry the old catalog/ tree, hence the
    path fallback. Empty in a checkout without tags."""
    name = m["name"]
    histories: dict[str, list[dict]] = {}
    for service_version, tag in release_tags(name):
        manifest = None
        for root in ("specs", "catalog"):
            out = git_lines("show", f"{tag}:{root}/{name}/service.yaml")
            if out:
                manifest = yaml.safe_load("\n".join(out)) or {}
                break
        if not manifest:
            continue
        date = (git_lines("log", "-1", "--format=%cs", tag) or [None])[0]
        for a in manifest.get("artifacts") or []:
            if not a.get("version"):
                continue
            entries = histories.setdefault(a["path"], [])
            if not entries or entries[-1]["version"] != a["version"]:
                entries.append({
                    "version": a["version"],
                    "date": date,
                    "service_version": service_version,
                })
    return {path: list(reversed(entries)) for path, entries in histories.items()}


def build_data(manifests: list[dict], specs: Path, mocks: Path) -> dict:
    index, consumers = channel_index(manifests, specs)
    surfaces = surface_index(manifests, specs)

    services = []
    for m in manifests:
        name = m["name"]
        examples = load_examples(mocks, name)
        rest_examples = load_rest_examples(mocks, name)
        histories = artifact_histories(m)
        artifacts = []
        for a in m.get("artifacts") or []:
            entry = {
                "kind": a["kind"],
                "path": a["path"],
                "stem": Path(a["path"]).stem,
                "version": a.get("version"),
                "gated": a.get("gated", a["kind"] != "doc"),
                "summary": a.get("summary", ""),
                "history": histories.get(a["path"], []),
            }
            source = specs / name / a["path"]
            if a["kind"] == "data-contract":
                odcs = yaml.safe_load(source.read_text()) or {}
                entry["odcs"] = odcs
                er = odcs_er(odcs)
                if er:
                    entry["er_mermaid"] = unfence(er)
            elif a["kind"] == "feature":
                entry["text"] = source.read_text()
                entry["feature"] = feature_data(a, entry["text"])
            elif a["kind"] == "doc":
                entry["text"] = source.read_text()
                entry["label"] = doc_label(source, a.get("summary", ""))
            artifacts.append(entry)

        ops = http_operations(m, specs, rest_examples)
        surface = surfaces.get(name) or {"ops": [], "data": []}
        changelog = changelog_data(m)
        released = [r for r in changelog if not r["unreleased"]]
        services.append({
            "name": name,
            "title": m["title"],
            "version": m.get("version"),
            "domain": m["domain"],
            "owner": m["owner"],
            "summary": clean(m["summary"]),
            "implementation_repo": m.get("implementationRepo"),
            "artifacts": artifacts,
            "produces": m.get("produces") or [],
            "consumes": m.get("consumes") or [],
            "operations": ops,
            "data_products": [
                {"stem": stem, "title": title} for stem, title in surface["data"]
            ],
            "channels": [
                channel_entry(address, index[address], consumers, examples)
                for address in m.get("produces") or []
                if address in index
            ],
            "changelog": changelog,
            "latest_release": released[0] if released else None,
            "ahead": bool(changelog) and changelog[0]["unreleased"],
        })

    produced = {
        address: m["name"] for m in manifests for address in m.get("produces") or []
    }
    edges, unconsumed = [], dict(produced)
    for m in manifests:
        for address in m.get("consumes") or []:
            if address in produced:
                edges.append(
                    {"from": produced[address], "channel": address, "to": m["name"]}
                )
                unconsumed.pop(address, None)
    return {
        "services": services,
        "edges": edges,
        "unconsumed": [
            {"channel": a, "producer": s} for a, s in sorted(unconsumed.items())
        ],
    }


def collect_mermaid(data: dict) -> list[tuple[str, str]]:
    """(label, chart) for every mermaid string in the emitted data - the
    input `sysspec docs diagrams` validates on an Astro-site repo. The
    system graphs are react islands, not mermaid, so they are not here."""
    charts = []
    for s in data["services"]:
        for c in s["channels"]:
            charts.append((f"{s['name']} {c['address']} sequence",
                           c["sequence_mermaid"]))
        for a in s["artifacts"]:
            if a.get("er_mermaid"):
                charts.append((f"{s['name']} {a['stem']} ER", a["er_mermaid"]))
    return charts


def run(specs_dir: str, site_dir: str, mocks_dir: str = "mocks") -> int:
    specs = Path(specs_dir)
    site = Path(site_dir)
    manifests = load_manifests(specs)
    if not manifests:
        raise SystemExit(f"no service manifests found under {specs_dir}/*/service.yaml")

    data = build_data(manifests, specs, Path(mocks_dir))
    data_file = site / "src" / "data" / "specs.json"
    data_file.parent.mkdir(parents=True, exist_ok=True)
    data_file.write_text(json.dumps(data, indent=2) + "\n")

    public = site / "public" / "specs"
    shutil.rmtree(public, ignore_errors=True)
    for m in manifests:
        for a in m.get("artifacts") or []:
            source = specs / m["name"] / a["path"]
            target = public / m["name"] / a["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    print(f"emitted specs data for {len(manifests)} service(s) to {data_file}")
    return 0
