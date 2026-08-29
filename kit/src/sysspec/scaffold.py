"""Scaffold a new specs repository.

The generated repo owns only its specs: everything substantive sits
behind versioned references - the sysspec pypi pin (Renovate bumps
it), the reusable workflows (@v<major> floating tag) and the Claude Code
plugin. Templates live as package data; dotfiles are stored without the
leading dot so packaging tools cannot drop them.
"""

from __future__ import annotations

import re
from importlib import metadata, resources
from pathlib import Path

RENAMES = {
    "gitignore": ".gitignore",
    "gherkin-lintrc": ".gherkin-lintrc",
    "spectral.yaml": ".spectral.yaml",
    "mcp.json": ".mcp.json",
    "github": ".github",
}

ORG_RE = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")

# Build artifacts and generated data that live inside the docs-site
# template in a development checkout (the repo root symlinks docs-site
# into the package data). Published wheels exclude them at build time;
# this skip covers editable installs.
SKIP = {
    "docs-site/node_modules",
    "docs-site/.astro",
    "docs-site/src/data/specs.json",
    "docs-site/public/specs",
}


def _copy(node, target: Path, subs: dict[str, str], rel: str = "") -> list[Path]:
    written: list[Path] = []
    for child in node.iterdir():
        child_rel = f"{rel}/{child.name}" if rel else child.name
        if child_rel in SKIP:
            continue
        out = target / RENAMES.get(child.name, child.name)
        if child.is_dir():
            out.mkdir(parents=True, exist_ok=True)
            written += _copy(child, out, subs, child_rel)
        else:
            text = child.read_text()
            for key, value in subs.items():
                text = text.replace(key, value)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text)
            written.append(out)
    return written


def run(target_dir: str, org: str, sysspec_repo: str) -> int:
    if not ORG_RE.fullmatch(org):
        raise SystemExit(
            f"--org must be reverse-DNS (e.g. com.acme), got {org!r}"
        )
    target = Path(target_dir)
    if target.exists() and any(target.iterdir()):
        raise SystemExit(f"{target} exists and is not empty")
    target.mkdir(parents=True, exist_ok=True)

    kit_version = metadata.version("sysspec")
    subs = {
        "__ORG__": org,
        "__KIT_VERSION__": kit_version,
        "__KIT_MAJOR__": f"v{kit_version.split('.')[0]}",
        "__SYSSPEC_REPO_SLUG__": sysspec_repo,
    }
    written = _copy(resources.files("sysspec") / "data/init", target, subs)
    for path in sorted(written):
        print(f"  {path.relative_to(target)}")
    print(
        f"\nscaffolded {len(written)} file(s) into {target} "
        f"(sysspec {kit_version}, org {org})\n\n"
        "Next steps:\n"
        "  git init && git add -A && git commit -m 'chore: scaffold specs'\n"
        "  mise install                # pinned toolchain\n"
        "  task ci                     # gates + mock cycle, green from the start\n"
        "  Replace the greeter starter service with your first real one.\n"
        "  Enable Renovate and GitHub Pages on the repository."
    )
    return 0
