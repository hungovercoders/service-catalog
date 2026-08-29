"""Emit the normalized catalog data the docs site renders from.

Reads every <catalog>/*/service.yaml and writes one catalog.json - the
manifests, the produces/consumes graph, and the inlined artifact content
the site renders natively (ODCS as JSON, features and docs as text).
Raw artifacts are copied under the site's public/ dir so spec renderers
and contract-of-record links can reach them by URL. The manifests are
the only input; a new service appears on the site with no config edits.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from .docs_gen import load_manifests


def build_data(manifests: list[dict], catalog: Path) -> dict:
    services = []
    for m in manifests:
        artifacts = []
        for a in m.get("artifacts") or []:
            entry = {
                "kind": a["kind"],
                "path": a["path"],
                "stem": Path(a["path"]).stem,
                "version": a.get("version"),
                "gated": a.get("gated", a["kind"] != "doc"),
                "summary": a.get("summary", ""),
            }
            source = catalog / m["name"] / a["path"]
            if a["kind"] == "data-contract":
                entry["odcs"] = yaml.safe_load(source.read_text()) or {}
            elif a["kind"] in ("feature", "doc"):
                entry["text"] = source.read_text()
            artifacts.append(entry)
        services.append({
            "name": m["name"],
            "title": m["title"],
            "version": m.get("version"),
            "domain": m["domain"],
            "owner": m["owner"],
            "summary": " ".join(m["summary"].split()),
            "artifacts": artifacts,
            "produces": m.get("produces") or [],
            "consumes": m.get("consumes") or [],
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


def run(catalog_dir: str, site_dir: str, mocks_dir: str = "mocks") -> int:
    catalog = Path(catalog_dir)
    site = Path(site_dir)
    manifests = load_manifests(catalog)
    if not manifests:
        raise SystemExit(f"no service manifests found under {catalog_dir}/*/service.yaml")

    data_file = site / "src" / "data" / "catalog.json"
    data_file.parent.mkdir(parents=True, exist_ok=True)
    data_file.write_text(json.dumps(build_data(manifests, catalog), indent=2) + "\n")

    public = site / "public" / "catalog"
    shutil.rmtree(public, ignore_errors=True)
    for m in manifests:
        for a in m.get("artifacts") or []:
            source = catalog / m["name"] / a["path"]
            target = public / m["name"] / a["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    print(f"emitted catalog data for {len(manifests)} service(s) to {data_file}")
    return 0
