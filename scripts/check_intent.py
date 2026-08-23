"""Require every added schema element to be named in the service's features.

A schema addition carries intent: nobody adds a message, payload property,
endpoint or parameter hoping it stays unused. The compat gate rightly passes
additions, so without this gate that intent rots silently. Every element
added versus the base ref must appear, by name, somewhere in the service's
feature files. There is no escape hatch: if it is not worth a scenario, it
is not worth adding to the contract yet.

Known limitation: new enum values are not gated (they carry no unique name).

Usage: python scripts/check_intent.py [base-ref] [service]
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from check_version_bump import blob, git, manifest_versions

BACKTICK = re.compile(r"`([^`]+)`")
PROSE_SUFFIX = re.compile(r"/(description|summary|title|examples)$")

# oasdiff changelog check ids that introduce a named element, and where the
# name lives. "backtick" means the last backtick-quoted token in the text
# (the first can be the parameter location).
OASDIFF_GATED = {
    "endpoint-added": "operationId",
    "new-optional-request-property": "backtick",
    "new-required-request-property": "backtick",
    "new-required-request-property-with-default": "backtick",
    "response-optional-property-added": "backtick",
    "response-required-property-added": "backtick",
    "new-optional-request-parameter": "backtick",
    "new-required-request-parameter": "backtick",
}


def write_tmp(text: str, suffix: str) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False)
    f.write(text)
    f.close()
    return f.name


def empty_base(kind: str, current_text: str) -> str:
    """A synthetic empty spec so a brand-new file gates everything it adds."""
    doc = yaml.safe_load(current_text) or {}
    if kind == "openapi":
        skeleton = {
            "openapi": doc.get("openapi", "3.0.3"),
            "info": doc.get("info", {}),
            "paths": {},
        }
    else:
        skeleton = {
            "asyncapi": doc.get("asyncapi", "3.0.0"),
            "info": doc.get("info", {}),
            "channels": {},
            "operations": {},
            "components": {"messages": {}},
        }
    return yaml.safe_dump(skeleton)


def openapi_added(base_file: str, current: str) -> set[str]:
    run = subprocess.run(
        ["oasdiff", "changelog", base_file, current, "--format", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    tokens: set[str] = set()
    for entry in json.loads(run.stdout or "[]") or []:
        source = OASDIFF_GATED.get(entry.get("id"))
        if source is None:
            continue
        if source == "operationId":
            tokens.add(entry.get("operationId") or entry.get("path") or "")
        else:
            ticks = BACKTICK.findall(entry.get("text", ""))
            if ticks:
                tokens.add(ticks[-1])
    return {t for t in tokens if t}


def asyncapi_added(base_file: str, current: str) -> set[str]:
    run = subprocess.run(
        ["npx", "-y", "@asyncapi/cli@5.0.7", "diff", base_file, current,
         "--format", "json", "--no-error"],
        capture_output=True,
        text=True,
        check=True,
    )
    changes = json.loads(run.stdout).get("changes", [])
    channels = (yaml.safe_load(Path(current).read_text()) or {}).get("channels") or {}
    tokens: set[str] = set()
    for c in changes:
        path = c.get("path", "")
        if c.get("action") != "add":
            continue
        if (
            "x-parser" in path
            or path.startswith(("/info", "/servers", "/tags", "/defaultContentType"))
            or path.startswith("/operations/")  # wiring; its channel and message are gated
            or "/bindings" in path
            or "/required/" in path  # accompanies a property add, which is gated
            or "/enum/" in path
            or PROSE_SUFFIX.search(path)
        ):
            continue
        if m := re.fullmatch(r"/components/messages/([^/]+)", path):
            tokens.add(m.group(1))
        elif m := re.fullmatch(r"/channels/([^/]+)", path):
            tokens.add((channels.get(m.group(1)) or {}).get("address") or m.group(1))
        elif "/payload/" in path:
            # Every property name after the payload, nested included. The
            # same add echoes under channels/operations/components - the set
            # dedupes it.
            segments = path.split("/")
            payload_at = segments.index("payload")
            for i, segment in enumerate(segments[payload_at:-1], start=payload_at):
                if segment == "properties":
                    tokens.add(segments[i + 1])
    return {t for t in tokens if t}


ADDED = {"openapi": openapi_added, "asyncapi": asyncapi_added}


def mentioned(token: str, corpus: str) -> bool:
    left = r"\b" if re.match(r"\w", token) else ""
    right = r"\b" if re.search(r"\w$", token) else ""
    return re.search(left + re.escape(token) + right, corpus) is not None


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    only = sys.argv[2] if len(sys.argv) > 2 else None
    merge_base = git("merge-base", base, "HEAD").strip()
    changed = set(git("diff", "--name-only", merge_base).splitlines())

    failures: list[str] = []
    checked = 0

    for manifest_path in sorted(git("ls-files", "catalog/*/service.yaml").splitlines()):
        service_dir = manifest_path.rsplit("/", 1)[0]
        service = service_dir.split("/")[-1]
        if only and service != only:
            continue

        corpus = "\n".join(
            p.read_text() for p in sorted(Path(service_dir, "features").glob("*.feature"))
        )

        for rel, (kind, _) in manifest_versions(Path(manifest_path).read_text()).items():
            full = f"{service_dir}/{rel}"
            extract = ADDED.get(kind)
            if full not in changed or extract is None:
                continue
            checked += 1
            base_text = blob(merge_base, full) or empty_base(kind, Path(full).read_text())
            base_file = write_tmp(base_text, Path(rel).suffix)
            tokens = sorted(extract(base_file, full))
            Path(base_file).unlink(missing_ok=True)
            for token in tokens:
                if mentioned(token, corpus):
                    print(f"intent ok: {full} adds '{token}', named in features")
                else:
                    failures.append(
                        f"{service}: new schema element '{token}' ({rel}) is not "
                        f"mentioned in {service_dir}/features/*.feature - state "
                        "the behaviour in a scenario (and bump the feature "
                        "artifact version)"
                    )

    if failures:
        print("\nSchema additions with no stated behaviour:\n  "
              + "\n  ".join(failures), file=sys.stderr)
        print(
            "\nEvery added element must be named in the service's features. "
            "There is no escape hatch: if it is not worth a scenario, it is "
            "not worth adding to the contract.",
            file=sys.stderr,
        )
        return 1
    print(f"\n{checked} changed spec(s) checked for stated intent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
