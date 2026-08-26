"""Classify gated contract changes and require a MAJOR bump for breaking ones.

Composes with the version gate rather than copying pizza-pattern's
"a version bump opts out" rule: here every gated change already requires a
bump, so opting out on any bump would make this gate vacuous. Instead the
bump's size must match the change's nature - breaking changes need a new
major version in the service manifest (the single source of truth).

Classification: `oasdiff breaking` for OpenAPI; a structural diff via
`@asyncapi/cli diff` for AsyncAPI (removals and edits are breaking,
additions are fine; parser noise and prose fields are ignored). ODCS data
contracts and feature files have no reliable differ and stay covered by
the version gate alone.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from .pins import ASYNCAPI_CLI
from .versioning import (
    GATED_KINDS,
    blob,
    git,
    list_manifests,
    major,
    manifest_versions,
    merge_base,
    service_version,
)

PROSE_PATH = re.compile(r"/(description|summary|title)$")


def write_tmp(text: str, suffix: str) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False)
    f.write(text)
    f.close()
    return f.name


def openapi_breaking(base_file: str, current: str) -> tuple[bool, str]:
    run = subprocess.run(
        ["oasdiff", "breaking", base_file, current, "--fail-on", "ERR"],
        capture_output=True,
        text=True,
    )
    return run.returncode != 0, (run.stdout + run.stderr).strip()


def asyncapi_breaking(base_file: str, current: str) -> tuple[bool, str]:
    run = subprocess.run(
        ["npx", "-y", ASYNCAPI_CLI, "diff", base_file, current,
         "--format", "json", "--no-error"],
        capture_output=True,
        text=True,
        check=True,
    )
    changes = json.loads(run.stdout).get("changes", [])
    bad = [
        f"{c['action']} {c['path']}"
        for c in changes
        if c["action"] in {"remove", "edit"}
        and "x-parser" not in c["path"]
        and not c["path"].startswith("/info/")
        and not PROSE_PATH.search(c["path"])
    ]
    return bool(bad), "\n".join(bad)


CLASSIFIERS = {"openapi": openapi_breaking, "asyncapi": asyncapi_breaking}


def run(base: str, only: str | None, catalog_dir: str) -> int:
    mb = merge_base(base)
    if mb is None:
        print(f"base ref '{base}' not found - nothing to diff against, skipping.")
        return 0
    changed = set(git("diff", "--name-only", mb).splitlines())

    failures: list[str] = []
    checked = 0

    for manifest_path in list_manifests(catalog_dir):
        service_dir = manifest_path.rsplit("/", 1)[0]
        if only and service_dir.split("/")[-1] != only:
            continue

        manifest_text = Path(manifest_path).read_text()
        base_manifest_text = blob(mb, manifest_path)
        now = manifest_versions(manifest_text)
        before = manifest_versions(base_manifest_text)
        svc_breaking = False

        for rel, (kind, version_now) in now.items():
            full = f"{service_dir}/{rel}"
            if full not in changed or kind not in GATED_KINDS:
                continue
            base_text = blob(mb, full)
            if base_text is None:
                print(f"new artifact, nothing to compare: {full}")
                continue
            classify = CLASSIFIERS.get(kind)
            if classify is None:
                print(f"unclassified ({kind}) - version gate only: {full}")
                continue

            checked += 1
            base_file = write_tmp(base_text, Path(rel).suffix)
            breaking, detail = classify(base_file, full)
            Path(base_file).unlink(missing_ok=True)
            if not breaking:
                print(f"additive ok: {full}")
                continue

            svc_breaking = True
            _, version_before = before.get(rel, (kind, None))
            if major(version_now) > major(version_before):
                print(f"breaking ok (major bump {version_before} -> {version_now}): {full}")
            else:
                failures.append(
                    f"{full}: breaking change requires a major bump "
                    f"(version {version_before} -> {version_now})\n"
                    + "\n".join(f"    {line}" for line in detail.splitlines()[:20])
                )

        # A breaking artifact breaks the whole contract surface: the service
        # version consumers pin must also take a major bump.
        if svc_breaking:
            svc_now = service_version(manifest_text)
            svc_before = service_version(base_manifest_text)
            if svc_before is not None and major(svc_now) <= major(svc_before):
                failures.append(
                    f"{service_dir}: breaking change requires a service major bump "
                    f"(version {svc_before} -> {svc_now})"
                )
            elif svc_before is not None:
                print(
                    f"breaking ok (service major bump {svc_before} -> {svc_now}): "
                    f"{service_dir}"
                )

    if failures:
        print("\nBreaking contract changes without a major bump:\n  "
              + "\n  ".join(failures), file=sys.stderr)
        return 1
    print(f"\n{checked} classifiable artifact(s) checked for compatibility.")
    return 0
