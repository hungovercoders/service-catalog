"""Require a plugin version bump when the installed plugin surface changes.

The repo doubles as a Claude Code plugin: the MCP server, the skills and
the plugin manifests are what a marketplace install actually ships. Any
change to that surface without a semver-greater `version` in
.claude-plugin/plugin.json means installs silently lag the repo.

Usage: python scripts/check_plugin_version.py [base-ref]
"""

from __future__ import annotations

import json
import sys

from check_version_bump import blob, git

MANIFEST = ".claude-plugin/plugin.json"
SURFACE = ("server/", "skills/", ".claude-plugin/", ".mcp.json")


def version_of(text: str | None) -> tuple[int, ...] | None:
    if text is None:
        return None
    return tuple(int(p) for p in json.loads(text)["version"].split("."))


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    merge_base = git("merge-base", base, "HEAD").strip()
    changed = [
        f for f in git("diff", "--name-only", merge_base).splitlines()
        if f.startswith(SURFACE)
    ]
    if not changed:
        print("plugin surface unchanged - no bump needed.")
        return 0

    before = version_of(blob(merge_base, MANIFEST))
    now = version_of(open(MANIFEST).read())
    if before is None:
        print(f"new plugin manifest @ {'.'.join(map(str, now))}")
        return 0
    if now > before:
        print(
            f"plugin surface changed ({len(changed)} file(s)), version "
            f"{'.'.join(map(str, before))} -> {'.'.join(map(str, now))} - ok"
        )
        return 0

    print(
        "Plugin surface changed without a version bump:\n  "
        + "\n  ".join(changed[:20])
        + f"\n\n{MANIFEST} version is {'.'.join(map(str, now))} "
        f"(base {'.'.join(map(str, before))}) - bump it semver-greater in "
        "the same change.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
