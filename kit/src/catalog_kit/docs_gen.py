"""Generate the docs site content from the service manifests.

Reads every <catalog>/*/service.yaml and emits docs/index.md (catalog
graph), docs/SUMMARY.md (literate-nav) and docs/services/<name>/ pages,
then renders the AsyncAPI HTML and ODCS data-contract HTML the pages
embed. A new service appears on the site with no config edits - the
manifests are the only input.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml

from .pins import ASYNCAPI_CLI, ASYNCAPI_HTML_TEMPLATE, DATACONTRACT_CLI


def load_manifests(catalog: Path) -> list[dict]:
    return [
        yaml.safe_load(p.read_text())
        for p in sorted(catalog.glob("*/service.yaml"))
    ]


def rel_artifact(name: str, path: str) -> str:
    """Path from docs/services/<name>/ to the raw artifact via the docs/catalog symlink."""
    return f"../../catalog/{name}/{path}"


def write_index(manifests: list[dict], docs: Path) -> None:
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
    (docs / "index.md").write_text("\n".join(lines) + "\n")


def write_service(m: dict, catalog: Path, docs: Path) -> list[str]:
    """Write one service's pages; return its literate-nav lines."""
    name = m["name"]
    out = docs / "services" / name
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
        odcs = yaml.safe_load((catalog / name / a["path"]).read_text()) or {}
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


def run(catalog_dir: str, docs_dir: str, html: bool = True) -> int:
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
    write_index(manifests, docs)

    nav = ["- [Overview](index.md)", "- Services:"]
    for m in manifests:
        nav += write_service(m, catalog, docs)
    (docs / "SUMMARY.md").write_text("\n".join(nav) + "\n")
    print(f"generated docs for {len(manifests)} service(s)")

    if html:
        for m in manifests:
            render_html(m, catalog, docs)
    return 0
