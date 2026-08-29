"""Shared specs readers and chart builders for the docs pipeline.

Everything the docs data emitter needs to see the specs whole: the
manifests, the channel and surface indexes, the mermaid charts (specs
graph, per-channel delivery sequences, ODCS ER diagrams), the gherkin
parser, the Microcks example loaders and the release-tag reader. The
mermaid gate lives here too: `check_diagrams` parses every chart -
fences in ungated doc sources and generated markdown, plus each chart
string `sysspec docs data` emits - headlessly with mermaid-cli, so a
syntax error lands in CI instead of a viewer's browser.
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

from .pins import MERMAID_CLI


def load_manifests(specs: Path) -> list[dict]:
    return [
        yaml.safe_load(p.read_text())
        for p in sorted(specs.glob("*/service.yaml"))
    ]


def clean(text: str) -> str:
    return " ".join(text.split())


def node_id(name: str) -> str:
    """A mermaid-safe node id."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def anchor(address: str) -> str:
    """Deterministic heading id for a channel address."""
    return address.replace(".", "-")


def channel_index(
    manifests: list[dict], specs: Path
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
            doc = yaml.safe_load((specs / m["name"] / a["path"]).read_text()) or {}
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


def surface_index(manifests: list[dict], specs: Path) -> dict[str, dict]:
    """Per-service HTTP operations and data products, for the graph views
    and overview lists."""
    out: dict[str, dict] = {}
    for m in manifests:
        ops: list[tuple[str, str, str]] = []
        data: list[tuple[str, str]] = []
        for a in m.get("artifacts") or []:
            path = specs / m["name"] / a["path"]
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


def load_examples(mocks_dir: Path, service: str) -> dict[str, list[tuple[str, str]]]:
    """Example payloads per send operation from <mocks>/<service>.events.examples.yaml.

    Anything missing - the directory, the file, the keys - yields {}:
    a specs without Microcks examples still documents its messages,
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


def channel_sequence(address: str, info: dict, consuming: list[dict]) -> list[str]:
    """One channel's delivery as a fenced sequence diagram: the producer
    publishing each message to the channel, and the channel delivering it
    to every consumer - a note stands in when there is none yet."""
    producer = node_id(info["service"])
    lines = [
        "```mermaid",
        "sequenceDiagram",
        f'    participant {producer} as {info["title"]}',
        f"    participant chan as {address}",
    ]
    for c in consuming:
        lines.append(f'    participant {node_id(c["name"])} as {c["title"]}')
    for msg_name, _ in info["messages"]:
        lines.append(f"    {producer}-)chan: {msg_name}")
        for c in consuming:
            lines.append(f'    chan-){node_id(c["name"])}: {msg_name}')
    if not consuming:
        lines.append("    Note over chan: no consumer in the specs yet")
    lines.append("```")
    return lines


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


STEP_RE = re.compile(r"^(Given|When|Then|And|But|\*)\s+(.+)$")
SCENARIO_RE = re.compile(r"^[ \t]*(?:Scenario Outline|Scenario):[ \t]*(.+)$")
PHASES = {"Given": "given", "When": "when", "Then": "then"}


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


def git_lines(*args: str) -> list[str]:
    """Non-empty stdout lines of a git command, [] on any failure -
    generation must degrade in a checkout without tags (a fresh scaffold,
    a shallow CI clone) rather than fail or emit broken pages."""
    run = subprocess.run(["git", *args], capture_output=True, text=True)
    return [] if run.returncode else [
        line for line in run.stdout.splitlines() if line.strip()
    ]


def doc_label(src: Path, summary: str) -> str:
    """Nav label for an ungated doc: its own H1, else the summary, else the stem."""
    for line in src.read_text().splitlines():
        if match := re.match(r"#\s+(.+)", line):
            return match.group(1).strip()
    return clean(summary).rstrip(".") if summary else src.stem


MERMAID_FENCE = re.compile(r"^```mermaid\n(.*?)^```", re.MULTILINE | re.DOTALL)


def mermaid_blocks(specs: Path, docs: Path) -> list[tuple[Path, int, str]]:
    """(file, line, source) for every mermaid fence the site will render:
    any generated pages, plus the ungated doc sources the site renders
    (an ADR's diagram breaks the page just as surely as a generated one)."""
    pages = [
        p
        for p in (sorted(docs.rglob("*.md")) if docs.is_dir() else [])
        # docs/specs symlinks into the source tree; its .md files are
        # covered by the explicit source glob below.
        if not p.is_relative_to(docs / "specs")
    ]
    sources = sorted(specs.glob("*/docs/*.md"))
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


def check_diagrams(specs_dir: str, docs_dir: str, site_dir: str = "docs-site") -> int:
    """Parse every mermaid diagram with mermaid-cli.

    Static builds never parse mermaid - a syntax error only surfaces in
    the viewer's browser, as raw diagram source. This gate renders each
    chart headlessly so that failure lands in CI instead: the fences in
    ungated doc sources (and any generated markdown), plus every chart
    string `sysspec docs data` emitted when the site's specs.json is
    present.
    """
    blocks = mermaid_blocks(Path(specs_dir), Path(docs_dir))
    data_file = Path(site_dir) / "src" / "data" / "specs.json"
    if data_file.is_file():
        from .docs_data import collect_mermaid
        data = json.loads(data_file.read_text())
        blocks += [
            (f"{data_file} ({label})", 0, chart)
            for label, chart in collect_mermaid(data)
        ]
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
