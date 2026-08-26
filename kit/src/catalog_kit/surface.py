"""Require a version bump when a declared surface changes.

A "surface" is any set of paths whose contents ship somewhere - a plugin,
a package, a template bundle. Any change under those paths without a
semver-greater version in the named JSON file means installs silently lag
the repo.
"""

from __future__ import annotations

import json
import sys

from .versioning import blob, git, merge_base


def version_of(text: str | None, key: str) -> tuple[int, ...] | None:
    if text is None:
        return None
    return tuple(int(p) for p in json.loads(text)[key].split("."))


def run(base: str, version_file: str, json_key: str, paths: list[str]) -> int:
    mb = merge_base(base)
    if mb is None:
        print(f"base ref '{base}' not found - nothing to diff against, skipping.")
        return 0
    prefixes = tuple(paths)
    changed = [
        f for f in git("diff", "--name-only", mb).splitlines()
        if f.startswith(prefixes)
    ]
    if not changed:
        print("surface unchanged - no bump needed.")
        return 0

    before = version_of(blob(mb, version_file), json_key)
    now = version_of(open(version_file).read(), json_key)
    if before is None:
        print(f"new surface manifest @ {'.'.join(map(str, now))}")
        return 0
    if now > before:
        print(
            f"surface changed ({len(changed)} file(s)), version "
            f"{'.'.join(map(str, before))} -> {'.'.join(map(str, now))} - ok"
        )
        return 0

    print(
        "Surface changed without a version bump:\n  "
        + "\n  ".join(changed[:20])
        + f"\n\n{version_file} version is {'.'.join(map(str, now))} "
        f"(base {'.'.join(map(str, before))}) - bump it semver-greater in "
        "the same change.",
        file=sys.stderr,
    )
    return 1
