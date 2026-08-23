"""Cross-check every service manifest against its contracts and the catalog graph.

Per service:
  1. `produces` must exactly match the channel addresses its AsyncAPI files
     publish with a `send` operation; documented `receive` channels must be
     listed in `consumes`.
  2. Every `consumes` entry must be produced by some service in the catalog.
  3. Every declared artifact path must exist on disk, and every file of a
     gated kind on disk must be declared in the manifest.
  4. For asyncapi/openapi artifacts, the spec's `info.version` must equal the
     manifest version (mock URLs and rendered docs surface `info.version`).

Usage: python scripts/lint_manifest.py [service]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

CATALOG = Path("catalog")
GATED_KINDS = {"asyncapi", "openapi", "data-contract", "feature"}
KIND_DIRS = {
    "asyncapi": "asyncapi",
    "openapi": "openapi",
    "data-contract": "data-contracts",
    "feature": "features",
}
SPEC_SUFFIXES = {".yaml", ".yml", ".feature"}


def channel_ops(doc: dict) -> tuple[set[str], set[str]]:
    """Return (sent, received) channel addresses of an AsyncAPI 3 document."""
    channels = doc.get("channels") or {}
    sent, received = set(), set()
    for op in (doc.get("operations") or {}).values():
        ref = (op.get("channel") or {}).get("$ref", "")
        key = ref.rsplit("/", 1)[-1]
        address = (channels.get(key) or {}).get("address")
        if not address:
            continue
        if op.get("action") == "send":
            sent.add(address)
        elif op.get("action") == "receive":
            received.add(address)
    return sent, received


def lint_service(service_dir: Path, produced_by: dict[str, str]) -> list[str]:
    problems: list[str] = []
    manifest = yaml.safe_load((service_dir / "service.yaml").read_text())
    name = manifest["name"]
    artifacts = manifest.get("artifacts") or []

    declared = {a["path"] for a in artifacts}
    sent: set[str] = set()
    received: set[str] = set()

    for a in artifacts:
        path = service_dir / a["path"]
        if not path.is_file():
            problems.append(f"{name}: declared artifact missing on disk: {a['path']}")
            continue
        if a["kind"] in {"asyncapi", "openapi"}:
            doc = yaml.safe_load(path.read_text())
            spec_version = (doc.get("info") or {}).get("version")
            if spec_version != a.get("version"):
                problems.append(
                    f"{name}: {a['path']} info.version {spec_version} != "
                    f"manifest version {a.get('version')}"
                )
            if a["kind"] == "asyncapi":
                s, r = channel_ops(doc)
                sent |= s
                received |= r

    for kind, subdir in KIND_DIRS.items():
        for f in sorted((service_dir / subdir).glob("*")):
            rel = str(f.relative_to(service_dir))
            if f.suffix in SPEC_SUFFIXES and rel not in declared:
                problems.append(f"{name}: {kind} file on disk but not in manifest: {rel}")

    repo = manifest.get("implementationRepo")
    if repo is not None and not re.fullmatch(r"[\w.-]+/[\w.-]+", str(repo)):
        problems.append(
            f"{name}: implementationRepo must be <owner>/<repo>, got {repo!r}"
        )

    produces = set(manifest.get("produces") or [])
    consumes = set(manifest.get("consumes") or [])

    for address in sorted(produces - sent):
        problems.append(
            f"{name}: produces '{address}' but no AsyncAPI send operation publishes it"
        )
    for address in sorted(sent - produces):
        problems.append(
            f"{name}: AsyncAPI sends '{address}' but the manifest does not list it in produces"
        )
    for address in sorted(received - consumes):
        problems.append(
            f"{name}: AsyncAPI receives '{address}' but the manifest does not list it in consumes"
        )
    for address in sorted(consumes):
        if address not in produced_by:
            problems.append(f"{name}: consumes '{address}' but no service produces it")

    return problems


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    service_dirs = sorted(p.parent for p in CATALOG.glob("*/service.yaml"))

    produced_by: dict[str, str] = {}
    for d in service_dirs:
        manifest = yaml.safe_load((d / "service.yaml").read_text())
        for address in manifest.get("produces") or []:
            produced_by[address] = manifest["name"]

    problems: list[str] = []
    checked = 0
    for d in service_dirs:
        if only and d.name != only:
            continue
        checked += 1
        problems += lint_service(d, produced_by)

    if not checked:
        print(f"no such service: {only}", file=sys.stderr)
        return 1
    if problems:
        print("Manifest drift:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    print(f"{checked} manifest(s) consistent with contracts and catalog graph.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
