"""Fail CI when a gated artifact changes without its declared version moving.

Gated artifacts are contracts of record: AsyncAPI, OpenAPI, ODCS data
contracts, and Gherkin feature files. Docs are ungated and free to edit.

The manifest is the single source of truth for an artifact's version, so
there is no second place to forget to update.

Usage: python scripts/check_version_bump.py origin/main
"""

from __future__ import annotations

import subprocess
import sys

import yaml

GATED_KINDS = {"asyncapi", "openapi", "data-contract", "feature"}


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


def blob(ref: str, path: str) -> str | None:
    try:
        return git("show", f"{ref}:{path}")
    except subprocess.CalledProcessError:
        return None


def manifest_versions(text: str | None) -> dict[str, tuple[str, str | None]]:
    """Map artifact path -> (kind, version) for gated artifacts."""
    if text is None:
        return {}
    doc = yaml.safe_load(text) or {}
    out = {}
    for a in doc.get("artifacts", []) or []:
        kind = a.get("kind")
        if a.get("gated", kind in GATED_KINDS):
            out[a["path"]] = (kind, a.get("version"))
    return out


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    changed = git("diff", "--name-only", f"{base}...HEAD").splitlines()

    failures: list[str] = []
    checked = 0

    for manifest_path in sorted(git("ls-files", "catalog/*/service.yaml").splitlines()):
        service_dir = manifest_path.rsplit("/", 1)[0]
        service = service_dir.split("/")[-1]

        now = manifest_versions(open(manifest_path).read())
        before = manifest_versions(blob(base, manifest_path))

        for rel, (kind, version_now) in now.items():
            full = f"{service_dir}/{rel}"
            if full not in changed:
                continue
            checked += 1
            _, version_before = before.get(rel, (kind, None))

            if version_before is None:
                print(f"new gated artifact: {full} @ {version_now}")
            elif version_before == version_now:
                failures.append(
                    f"{full} ({kind}) changed but version stayed at {version_before}"
                )
            else:
                print(f"ok: {full} {version_before} -> {version_now}")

        # An artifact silently dropped from the manifest is also drift.
        for rel in before.keys() - now.keys():
            failures.append(f"{service_dir}/{rel} was removed from {service}'s manifest")

    if failures:
        print("\nGated artifact drift:\n  " + "\n  ".join(failures), file=sys.stderr)
        print(
            "\nBump the version in service.yaml, or revert. Contracts and "
            "feature files are not edited to match implementations.",
            file=sys.stderr,
        )
        return 1

    print(f"\n{checked} gated artifact(s) changed, all versioned correctly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
