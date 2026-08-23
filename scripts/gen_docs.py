"""Generate the docs site content from the service manifests.

Reads every catalog/*/service.yaml and emits docs/index.md (catalog graph),
docs/SUMMARY.md (literate-nav) and docs/services/<name>/ pages. AsyncAPI
HTML references are generated separately by `task docs:generate`, which
runs this script first. A new service appears on the site with no config
edits - the manifests are the only input.

Usage: python scripts/gen_docs.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

CATALOG = Path("catalog")
DOCS = Path("docs")
SERVICES = DOCS / "services"


def load_manifests() -> list[dict]:
    return [
        yaml.safe_load(p.read_text())
        for p in sorted(CATALOG.glob("*/service.yaml"))
    ]


def rel_artifact(name: str, path: str) -> str:
    """Path from docs/services/<name>/ to the raw artifact via the docs/catalog symlink."""
    return f"../../catalog/{name}/{path}"


def write_index(manifests: list[dict]) -> None:
    produced = {
        address: m["name"] for m in manifests for address in m.get("produces") or []
    }
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
        "| Service | Version | Domain | Owner | Summary |",
        "| --- | --- | --- | --- | --- |",
    ]
    for m in manifests:
        lines.append(
            f"| [{m['title']}](services/{m['name']}/index.md) "
            f"| {m.get('version', '—')} "
            f"| {m['domain']} | {m['owner']} | {' '.join(m['summary'].split())} |"
        )
    lines += ["", "## Event flow", "", "```mermaid", "flowchart LR"]
    for m in manifests:
        lines.append(f"    {m['name']}[{m['title']}]")
    unconsumed = dict(produced)
    for m in manifests:
        for address in m.get("consumes") or []:
            if address in produced:
                lines.append(f"    {produced[address]} -- {address} --> {m['name']}")
                unconsumed.pop(address, None)
    lines.append("```")
    if unconsumed:
        lines += ["", "Channels with no consumer in the catalog:", ""]
        lines += [f"- `{a}` (produced by {s})" for a, s in sorted(unconsumed.items())]
    (DOCS / "index.md").write_text("\n".join(lines) + "\n")


def write_service(m: dict) -> list[str]:
    """Write one service's pages; return its literate-nav lines."""
    name = m["name"]
    out = SERVICES / name
    out.mkdir(parents=True)

    lines = [
        f"# {m['title']}",
        "",
        f"**Version:** {m.get('version', '—')} · "
        f"**Domain:** {m['domain']} · **Owner:** `{m['owner']}`",
        "",
        " ".join(m["summary"].split()),
        "",
        "## Artifacts",
        "",
        "| Kind | Artifact | Version | Class | Summary |",
        "| --- | --- | --- | --- | --- |",
    ]
    for a in m.get("artifacts") or []:
        gated = a.get("gated", a["kind"] != "doc")
        badge = "**gated**" if gated else "context"
        version = a.get("version") or "—"
        lines.append(
            f"| {a['kind']} | [`{a['path']}`]({rel_artifact(name, a['path'])}) "
            f"| {version} | {badge} | {a.get('summary', '')} |"
        )
    for heading, key in [("Produces", "produces"), ("Consumes", "consumes")]:
        addresses = m.get(key) or []
        if addresses:
            lines += ["", f"## {heading}", ""]
            lines += [f"- `{a}`" for a in addresses]
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
        (out / f"{stem}.md").write_text(
            f"# {m['title']} — event contract\n\n"
            f"Contract of record: [`{a['path']}`]({rel_artifact(name, a['path'])}) "
            f"@ {a['version']}\n\n"
            f'<iframe src="../asyncapi-html/index.html" '
            f'style="width:100%;height:85vh;border:none;" '
            f'title="{m["title"]} events"></iframe>\n'
        )
        nav.append(f"        - [Events (AsyncAPI)](services/{name}/{stem}.md)")

    for a in kinds.get("data-contract", []):
        stem = Path(a["path"]).stem
        odcs = yaml.safe_load((CATALOG / name / a["path"]).read_text()) or {}
        title = odcs.get("name", stem)
        (out / f"{stem}.md").write_text(
            f"# {m['title']} — {title}\n\n"
            f"Contract of record: [`{a['path']}`]({rel_artifact(name, a['path'])}) "
            f"@ {a['version']}\n\n"
            f'<iframe src="../datacontract-html/{stem}.html" '
            f'style="width:100%;height:85vh;border:none;" '
            f'title="{title}"></iframe>\n'
        )
        nav.append(f"        - [Data: {title}](services/{name}/{stem}.md)")

    features = kinds.get("feature", [])
    if features:
        body = [f"# {m['title']} — acceptance criteria", ""]
        for a in features:
            body += [
                f"Binding, versioned at {a['version']}: "
                f"[`{a['path']}`]({rel_artifact(name, a['path'])})",
                "",
                f"```gherkin\n--8<-- \"catalog/{name}/{a['path']}\"\n```",
                "",
            ]
        (out / "features.md").write_text("\n".join(body))
        nav.append(f"        - [Acceptance criteria](services/{name}/features.md)")

    for a in kinds.get("doc", []):
        stem = Path(a["path"]).stem
        (out / f"{stem}.md").write_text(f"--8<-- \"catalog/{name}/{a['path']}\"\n")
        nav.append(f"        - [{a.get('summary', stem)}](services/{name}/{stem}.md)")

    return nav


def main() -> None:
    shutil.rmtree(SERVICES, ignore_errors=True)
    manifests = load_manifests()
    write_index(manifests)

    nav = ["- [Overview](index.md)", "- Services:"]
    for m in manifests:
        nav += write_service(m)
    (DOCS / "SUMMARY.md").write_text("\n".join(nav) + "\n")
    print(f"generated docs for {len(manifests)} service(s)")


if __name__ == "__main__":
    main()
