"""MCP server exposing a service specs: contracts, specs, features, docs.

Design notes:

* The specs is a graph of services, not a flat list of files. Services own
  artifacts and declare the channels they produce and consume, so questions
  like "who breaks if I change this?" are answerable without grepping.
* Every read is confined to SPECS_DIR and to paths a service manifest
  actually declares. An undeclared file is not reachable, so dropping
  something into the tree does not silently expose it.
* There is no write tool. Gated artifacts (contracts, features) are the
  record; ungated ones (docs) are context.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from fastmcp import FastMCP

mcp = FastMCP("sysspec")

GATED_KINDS = {"asyncapi", "openapi", "data-contract", "feature"}


def specs_dir() -> Path:
    raw = os.environ.get("SPECS_DIR")
    if not raw:
        raise RuntimeError(
            "SPECS_DIR is not set. In a plugin this is wired up in "
            ".mcp.json as ${CLAUDE_PLUGIN_ROOT}/specs."
        )
    root = Path(raw).resolve()
    if not root.is_dir():
        raise RuntimeError(f"SPECS_DIR does not exist: {root}")
    return root


@dataclass(frozen=True)
class Service:
    name: str
    root: Path
    manifest: dict

    @property
    def artifacts(self) -> list[dict]:
        return self.manifest.get("artifacts", []) or []

    def artifact(self, path: str) -> dict:
        for a in self.artifacts:
            if a["path"] == path:
                return a
        declared = [a["path"] for a in self.artifacts]
        raise ValueError(
            f"{path!r} is not declared by service {self.name!r}. "
            f"Declared artifacts: {declared}"
        )


def _load_services() -> dict[str, Service]:
    root = specs_dir()
    services: dict[str, Service] = {}
    for manifest_path in sorted(root.glob("*/service.yaml")):
        doc = yaml.safe_load(manifest_path.read_text()) or {}
        name = doc.get("name") or manifest_path.parent.name
        services[name] = Service(name, manifest_path.parent, doc)
    return services


def _service(name: str) -> Service:
    services = _load_services()
    if name not in services:
        raise ValueError(f"No service {name!r}. Available: {sorted(services)}")
    return services[name]


def _read(service: Service, path: str) -> str:
    """Read a declared artifact, confined to the service directory."""
    service.artifact(path)  # raises unless declared in the manifest
    candidate = (service.root / path).resolve()
    if not candidate.is_relative_to(service.root.resolve()):
        raise ValueError(f"Refusing to read outside {service.name}: {path}")
    if not candidate.is_file():
        raise FileNotFoundError(f"Declared but missing: {path}")
    return candidate.read_text()


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


@mcp.tool
def list_services() -> list[dict]:
    """List every service in the specs with its domain, owner and summary.

    Start here. Returns no artifact contents — use get_service next.
    """
    out = []
    for svc in _load_services().values():
        m = svc.manifest
        out.append(
            {
                "name": svc.name,
                "title": m.get("title", svc.name),
                "domain": m.get("domain", ""),
                "owner": m.get("owner", ""),
                "summary": (m.get("summary") or "").strip(),
                "artifact_count": len(svc.artifacts),
            }
        )
    return out


@mcp.tool
def get_service(name: str) -> dict:
    """Describe one service: its artifact index and event dependencies.

    Returns an index only, not file contents. Pick the artifact you need,
    then call get_artifact or get_acceptance_criteria.
    """
    svc = _service(name)
    m = svc.manifest
    return {
        "name": svc.name,
        "title": m.get("title", svc.name),
        "domain": m.get("domain", ""),
        "owner": m.get("owner", ""),
        "summary": (m.get("summary") or "").strip(),
        "produces": m.get("produces", []),
        "consumes": m.get("consumes", []),
        "artifacts": [
            {
                "kind": a["kind"],
                "path": a["path"],
                "version": a.get("version"),
                "gated": a.get("gated", a["kind"] in GATED_KINDS),
                "summary": (a.get("summary") or "").strip(),
            }
            for a in svc.artifacts
        ],
    }


# --------------------------------------------------------------------------
# Artifact access
# --------------------------------------------------------------------------


@mcp.tool
def get_artifact(service: str, path: str) -> dict:
    """Fetch one declared artifact by service and path.

    Gated artifacts (asyncapi, openapi, data-contract, feature) are the
    record. If the implementation disagrees with a gated artifact, the
    implementation is wrong — do not edit the artifact to make it pass.
    """
    svc = _service(service)
    meta = svc.artifact(path)
    gated = meta.get("gated", meta["kind"] in GATED_KINDS)
    return {
        "service": svc.name,
        "kind": meta["kind"],
        "path": path,
        "version": meta.get("version"),
        "gated": gated,
        "authority": "contract of record" if gated else "context, not binding",
        "content": _read(svc, path),
    }


@mcp.tool
def get_message_schema(service: str, message: str) -> dict:
    """Return one AsyncAPI message payload schema.

    Prefer this over fetching the whole document when you only need a shape.
    """
    svc = _service(service)
    for a in svc.artifacts:
        if a["kind"] != "asyncapi":
            continue
        doc = yaml.safe_load(_read(svc, a["path"])) or {}
        messages = doc.get("components", {}).get("messages", {})
        if message in messages:
            return {
                "service": svc.name,
                "message": message,
                "contract_version": a.get("version"),
                "payload": messages[message].get("payload", {}),
            }
    raise ValueError(f"No message {message!r} on service {service!r}")


@mcp.tool
def get_acceptance_criteria(service: str) -> dict:
    """Return the Gherkin feature files for a service.

    These are binding acceptance criteria. Implement toward them. If a
    scenario looks wrong, say so and stop rather than adjusting it.
    """
    svc = _service(service)
    features = [a for a in svc.artifacts if a["kind"] == "feature"]
    if not features:
        raise ValueError(f"Service {service!r} declares no feature files")
    return {
        "service": svc.name,
        "authority": "binding — implement toward these, do not amend them",
        "features": [
            {
                "path": a["path"],
                "summary": (a.get("summary") or "").strip(),
                "gherkin": _read(svc, a["path"]),
            }
            for a in features
        ],
    }


# --------------------------------------------------------------------------
# Graph queries
# --------------------------------------------------------------------------


@mcp.tool
def trace_channel(address: str) -> dict:
    """Find which services produce and consume a channel address.

    Use before changing a message shape: the consumers listed are what
    you will break.
    """
    producers, consumers = [], []
    for svc in _load_services().values():
        if address in svc.manifest.get("produces", []):
            producers.append(svc.name)
        if address in svc.manifest.get("consumes", []):
            consumers.append(svc.name)
    if not producers and not consumers:
        raise ValueError(f"No service references {address!r}")
    return {
        "address": address,
        "produced_by": sorted(producers),
        "consumed_by": sorted(consumers),
        "note": "Changing this payload breaks the consumers listed above.",
    }


@mcp.tool
def search_specs(query: str, kind: str | None = None) -> list[dict]:
    """Search artifact contents across all services, optionally by kind.

    Returns matching lines with context, not whole files. Valid kinds:
    asyncapi, openapi, data-contract, feature, doc.
    """
    needle = query.lower()
    hits = []
    for svc in _load_services().values():
        for a in svc.artifacts:
            if kind and a["kind"] != kind:
                continue
            try:
                text = _read(svc, a["path"])
            except FileNotFoundError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if needle in line.lower():
                    hits.append(
                        {
                            "service": svc.name,
                            "kind": a["kind"],
                            "path": a["path"],
                            "line": i,
                            "text": line.strip()[:200],
                        }
                    )
    return hits[:50]


def build_server() -> FastMCP:
    return mcp
