"""Fail when a gated artifact changes without its declared version moving.

Gated artifacts are contracts of record: AsyncAPI, OpenAPI, ODCS data
contracts, and Gherkin feature files. Docs are ungated and free to edit.

The manifest is the single source of truth for an artifact's version, so
there is no second place to forget to update.
"""

from __future__ import annotations

import subprocess
import sys

import yaml

GATED_KINDS = {"asyncapi", "openapi", "data-contract", "feature"}


def major(version: str | None) -> int:
    if not version:
        return -1
    return int(version.split(".")[0])


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


def blob(ref: str, path: str) -> str | None:
    try:
        return git("show", f"{ref}:{path}")
    except subprocess.CalledProcessError:
        return None


def merge_base(base: str) -> str | None:
    """Merge-base of base and HEAD, or None when the base ref doesn't exist
    (a fresh repo with no origin) - diff gates then have nothing to compare."""
    try:
        return git("merge-base", base, "HEAD").strip()
    except subprocess.CalledProcessError:
        return None


def list_manifests(specs_dir: str) -> list[str]:
    manifests = sorted(git("ls-files", f"{specs_dir}/*/service.yaml").splitlines())
    if not manifests:
        raise SystemExit(
            f"no service manifests found under {specs_dir}/*/service.yaml - "
            "wrong --specs-dir, or the manifests are not committed"
        )
    return manifests


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


def service_version(text: str | None) -> str | None:
    """Top-level contract-surface version of a manifest."""
    if text is None:
        return None
    doc = yaml.safe_load(text) or {}
    v = doc.get("version")
    return str(v) if v is not None else None


def run(base: str, specs_dir: str) -> int:
    # Diff the working tree against the merge-base so the gate also bites in
    # the pre-commit hook, not only on committed CI state.
    mb = merge_base(base)
    if mb is None:
        print(f"base ref '{base}' not found - nothing to diff against, skipping.")
        return 0
    changed = git("diff", "--name-only", mb).splitlines()

    failures: list[str] = []
    checked = 0

    for manifest_path in list_manifests(specs_dir):
        service_dir = manifest_path.rsplit("/", 1)[0]
        service = service_dir.split("/")[-1]

        manifest_text = open(manifest_path).read()
        base_text = blob(base, manifest_path)
        now = manifest_versions(manifest_text)
        before = manifest_versions(base_text)

        artifact_bumped = False
        artifact_major_bumped = False

        for rel, (kind, version_now) in now.items():
            full = f"{service_dir}/{rel}"
            if full not in changed:
                continue
            checked += 1
            _, version_before = before.get(rel, (kind, None))

            if version_before is None:
                print(f"new gated artifact: {full} @ {version_now}")
                artifact_bumped = True
            elif version_before == version_now:
                failures.append(
                    f"{full} ({kind}) changed but version stayed at {version_before}"
                )
            else:
                print(f"ok: {full} {version_before} -> {version_now}")
                artifact_bumped = True
                if major(version_now) > major(version_before):
                    artifact_major_bumped = True

        # An artifact silently dropped from the manifest is also drift.
        for rel in before.keys() - now.keys():
            failures.append(f"{service_dir}/{rel} was removed from {service}'s manifest")

        # The contract surface as a whole is versioned too: it is what gets
        # tagged and pinned by consumers, so it must move with its artifacts.
        svc_now = service_version(manifest_text)
        svc_before = service_version(base_text)
        if svc_before is None and svc_now is not None:
            if base_text is not None:
                print(f"new service version: {service} @ {svc_now}")
        elif artifact_bumped:
            if svc_now is None:
                failures.append(
                    f"{service}: gated artifact changed but the manifest has no "
                    "top-level version"
                )
            elif svc_now == svc_before:
                failures.append(
                    f"{service}: gated artifact bumped but the service version "
                    f"stayed at {svc_before} - bump the top-level version"
                )
            elif artifact_major_bumped and major(svc_now) <= major(svc_before):
                failures.append(
                    f"{service}: an artifact took a major bump but the service "
                    f"version only moved {svc_before} -> {svc_now} - a major "
                    "artifact change is a major surface change"
                )
            else:
                print(f"ok: {service} service version {svc_before} -> {svc_now}")

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
