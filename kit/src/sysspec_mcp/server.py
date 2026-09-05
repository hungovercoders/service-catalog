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
* Responses are bounded and self-describing: anything that can be cut
  carries a truncated flag and a count, never a silent cap. Every
  content-bearing tool has a mode that returns exactly one thing.
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


DEFAULT_MAX_BYTES = 50_000


def _bounded(text: str, max_bytes: int) -> tuple[str, bool, int]:
    """Cap text at max_bytes on a line boundary. Returns (text, truncated, total_bytes)."""
    raw = text.encode("utf-8")
    total = len(raw)
    if total <= max_bytes:
        return text, False, total
    cut = raw[:max_bytes].decode("utf-8", errors="ignore")
    if "\n" in cut:
        cut = cut[: cut.rfind("\n")]
    return cut, True, total


def _resolve_pointer(doc: object, pointer: str) -> object:
    """Resolve an RFC 6901 JSON pointer ('/components/schemas/Order') in a
    parsed document. '~1' escapes '/' and '~0' escapes '~', so an OpenAPI
    path key looks like '/paths/~1orders~1{order_id}'. A miss raises with
    the keys available at the deepest level that did resolve.
    """
    if not pointer.startswith("/"):
        raise ValueError(f"section must be a JSON pointer starting with '/': {pointer!r}")
    node = doc
    resolved = ""
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
            node = node[int(token)]
        else:
            if isinstance(node, dict):
                available = sorted(node)
            elif isinstance(node, list):
                available = [f"indexes 0..{len(node) - 1}"]
            else:
                available = []
            raise ValueError(
                f"{token!r} not found at {resolved or '/'!r} in this artifact. "
                f"Available there: {available}"
            )
        resolved += "/" + raw_token
    return node


def _split_gherkin(text: str) -> tuple[str, list[dict]]:
    """Split a .feature file into (header, scenarios). The header is
    everything before the first scenario — Feature line, description and
    Background — which a scenario needs to stand alone.
    """
    lines = text.splitlines(keepends=True)
    header_end = len(lines)
    scenarios: list[dict] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("Scenario:", "Scenario Outline:")):
            start = i
            while start > 0 and lines[start - 1].strip().startswith(("@", "#")):
                start -= 1
            if not scenarios:
                header_end = start
            else:
                scenarios[-1]["end"] = start
            name = stripped.split(":", 1)[1].strip()
            scenarios.append({"name": name, "start": start, "end": len(lines)})
    header = "".join(lines[:header_end]).rstrip("\n")
    return header, [
        {"name": s["name"], "gherkin": "".join(lines[s["start"] : s["end"]]).rstrip("\n")}
        for s in scenarios
    ]


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

    Returns an index only, not file contents. Then fetch narrowly:
    get_message_schema for one payload, get_acceptance_criteria with
    names_only/scenario/path filters, or get_artifact with section=,
    before pulling any whole file.
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
def get_artifact(
    service: str,
    path: str,
    section: str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict:
    """Fetch one declared artifact, or one section of it.

    Prefer the narrowest call that answers the question: get_message_schema
    for a single payload, or section= (an RFC 6901 JSON pointer such as
    '/components/schemas/Order' or '/paths/~1orders/post' — '~1' escapes
    '/') for one part of a YAML spec. Omit section only when you genuinely
    need the whole document. Responses are capped at max_bytes and say so
    via the truncated flag — never silently cut.

    Gated artifacts (asyncapi, openapi, data-contract, feature) are the
    record. If the implementation disagrees with a gated artifact, the
    implementation is wrong — do not edit the artifact to make it pass.
    """
    svc = _service(service)
    meta = svc.artifact(path)
    gated = meta.get("gated", meta["kind"] in GATED_KINDS)
    text = _read(svc, path)
    if section is not None:
        if meta["kind"] == "feature":
            raise ValueError(
                "section selectors only apply to YAML artifacts; use "
                "get_acceptance_criteria(scenario=...) for feature files"
            )
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError(f"{path} is not YAML; fetch it without section=: {exc}")
        node = _resolve_pointer(doc, section)
        text = yaml.safe_dump(node, sort_keys=False).rstrip("\n")
    content, truncated, total_bytes = _bounded(text, max_bytes)
    out = {
        "service": svc.name,
        "kind": meta["kind"],
        "path": path,
        "version": meta.get("version"),
        "gated": gated,
        "authority": "contract of record" if gated else "context, not binding",
        "section": section,
        "content": content,
        "truncated": truncated,
        "total_bytes": total_bytes,
    }
    if truncated:
        out["note"] = (
            f"Truncated at {max_bytes} of {total_bytes} bytes. Narrow with "
            "section= (e.g. '/components/messages/OrderPlaced', "
            "'/paths/~1orders/post') or raise max_bytes."
        )
    return out


@mcp.tool
def get_message_schema(service: str, message: str | None = None) -> dict:
    """Return one named payload schema — an AsyncAPI message or, failing
    that, an OpenAPI component schema.

    Call with no message first to list the names available on a service —
    the response carries names only, no schema bodies. This is the
    cheapest schema accessor for the caller; prefer it over get_artifact
    whenever you only need a shape.
    """
    svc = _service(service)
    asyncapi_messages: list[tuple[dict, str, dict]] = []
    openapi_schemas: list[tuple[dict, str, dict]] = []
    for a in svc.artifacts:
        if a["kind"] == "asyncapi":
            doc = yaml.safe_load(_read(svc, a["path"])) or {}
            for name, body in (doc.get("components", {}).get("messages", {}) or {}).items():
                asyncapi_messages.append((a, name, body))
        elif a["kind"] == "openapi":
            doc = yaml.safe_load(_read(svc, a["path"])) or {}
            for name, body in (doc.get("components", {}).get("schemas", {}) or {}).items():
                openapi_schemas.append((a, name, body))
    if message is None:
        return {
            "service": svc.name,
            "messages": [
                {"name": name, "path": a["path"], "contract_version": a.get("version")}
                for a, name, _ in asyncapi_messages
            ],
            "schemas": [
                {"name": name, "path": a["path"], "contract_version": a.get("version")}
                for a, name, _ in openapi_schemas
            ],
        }
    for a, name, body in asyncapi_messages:
        if name == message:
            return {
                "service": svc.name,
                "message": message,
                "source": "asyncapi",
                "contract_version": a.get("version"),
                "payload": body.get("payload", {}),
            }
    for a, name, body in openapi_schemas:
        if name == message:
            return {
                "service": svc.name,
                "message": message,
                "source": "openapi",
                "contract_version": a.get("version"),
                "payload": body,
            }
    raise ValueError(
        f"No message or schema {message!r} on service {service!r}. "
        f"Messages: {sorted(n for _, n, _ in asyncapi_messages)}. "
        f"Schemas: {sorted(n for _, n, _ in openapi_schemas)}."
    )


@mcp.tool
def get_acceptance_criteria(
    service: str,
    path: str | None = None,
    scenario: str | None = None,
    names_only: bool = False,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict:
    """Return Gherkin acceptance criteria for a service — narrowly.

    Start with names_only=True to see the scenario index, then fetch one
    scenario (scenario="substring of its title") or one file (path=...).
    Only omit all filters when you are about to implement the whole service.

    These are binding acceptance criteria. Implement toward them. If a
    scenario looks wrong, say so and stop rather than adjusting it.
    """
    svc = _service(service)
    features = [a for a in svc.artifacts if a["kind"] == "feature"]
    if not features:
        raise ValueError(f"Service {service!r} declares no feature files")
    if path is not None:
        svc.artifact(path)  # raises listing declared artifacts if unknown
        features = [a for a in features if a["path"] == path]
        if not features:
            declared = [a["path"] for a in svc.artifacts if a["kind"] == "feature"]
            raise ValueError(f"{path!r} is not a feature file. Features: {declared}")
    out: dict = {
        "service": svc.name,
        "authority": "binding — implement toward these, do not amend them",
        "truncated": False,
        "features": [],
    }
    budget = max_bytes
    for a in features:
        text = _read(svc, a["path"])
        summary = (a.get("summary") or "").strip()
        header, scenarios = _split_gherkin(text)
        if names_only:
            out["features"].append(
                {
                    "path": a["path"],
                    "summary": summary,
                    "scenarios": [s["name"] for s in scenarios],
                }
            )
            continue
        if scenario is not None:
            needle = scenario.lower()
            matched = [s for s in scenarios if needle in s["name"].lower()]
            if matched:
                out["features"].append(
                    {
                        "path": a["path"],
                        "summary": summary,
                        "header": header,
                        "matched": matched,
                        "total_scenarios": len(scenarios),
                    }
                )
            continue
        if len(text.encode("utf-8")) > budget:
            out["truncated"] = True
            out["features"].append(
                {
                    "path": a["path"],
                    "summary": summary,
                    "scenarios": [s["name"] for s in scenarios],
                    "gherkin_omitted": True,
                }
            )
            continue
        budget -= len(text.encode("utf-8"))
        out["features"].append({"path": a["path"], "summary": summary, "gherkin": text})
    if scenario is not None and not out["features"]:
        names = []
        for a in features:
            _, scenarios = _split_gherkin(_read(svc, a["path"]))
            names.extend(s["name"] for s in scenarios)
        raise ValueError(f"No scenario matching {scenario!r}. Scenarios: {names}")
    if out["truncated"]:
        out["note"] = (
            f"Some Gherkin bodies omitted to stay under {max_bytes} bytes. "
            "Fetch narrowly with path= or scenario=, or raise max_bytes."
        )
    return out


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
    note = "Changing this payload breaks the consumers listed above."
    if not producers and not consumers:
        note = (
            "No service produces or consumes this address. Check the spelling "
            "against get_service(...)['produces'/'consumes'] — addresses are "
            "dotted lowercase like 'orders.placed.v2'."
        )
    return {
        "address": address,
        "produced_by": sorted(producers),
        "consumed_by": sorted(consumers),
        "note": note,
    }


SEARCH_KINDS = {"asyncapi", "openapi", "data-contract", "feature", "doc"}


@mcp.tool
def search_specs(
    query: str,
    kind: str | None = None,
    service: str | None = None,
    limit: int = 20,
) -> dict:
    """Search artifact contents across services.

    Returns matching lines only (each capped at 200 chars), never whole
    files or surrounding context — follow up with get_artifact(section=...)
    on a hit's path. Narrow with kind= (asyncapi, openapi, data-contract,
    feature, doc) and service=; raise limit (max 100) only if truncated is
    true and you need more.
    """
    if kind is not None and kind not in SEARCH_KINDS:
        raise ValueError(f"Unknown kind {kind!r}. Valid kinds: {sorted(SEARCH_KINDS)}")
    if service is not None:
        _service(service)  # raises listing available services if unknown
    limit = max(1, min(limit, 100))
    needle = query.lower()
    hits = []
    total_matches = 0
    for svc in _load_services().values():
        if service and svc.name != service:
            continue
        for a in svc.artifacts:
            if kind and a["kind"] != kind:
                continue
            try:
                text = _read(svc, a["path"])
            except FileNotFoundError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if needle in line.lower():
                    total_matches += 1
                    if len(hits) < limit:
                        hits.append(
                            {
                                "service": svc.name,
                                "kind": a["kind"],
                                "path": a["path"],
                                "line": i,
                                "text": line.strip()[:200],
                            }
                        )
    return {
        "query": query,
        "kind": kind,
        "service": service,
        "hits": hits,
        "total_matches": total_matches,
        "returned": len(hits),
        "truncated": total_matches > len(hits),
    }


def build_server() -> FastMCP:
    return mcp
