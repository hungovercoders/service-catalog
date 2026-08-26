"""Static linters over the catalog's specs, features and data contracts."""

from __future__ import annotations

import subprocess
import sys

from .mocks import service_dirs
from .pins import DATACONTRACT_CLI, GHERKIN_LINT, SPECTRAL_CLI


def specs(only: str | None, catalog_dir: str) -> int:
    files: list[str] = []
    for d in service_dirs(catalog_dir, only):
        for kind in ("asyncapi", "openapi"):
            files += sorted(str(p) for p in (d / kind).glob("*.y*ml"))
    if not files:
        print(f"no specs found for '{only or '*'}'", file=sys.stderr)
        return 1
    return subprocess.run(
        ["npx", "-y", SPECTRAL_CLI, "lint", *files, "--fail-severity=warn"]
    ).returncode


def features(only: str | None, catalog_dir: str) -> int:
    dirs = [
        str(d / "features")
        for d in service_dirs(catalog_dir, only)
        if (d / "features").is_dir()
    ]
    if not dirs:
        print(f"no feature directories for '{only or '*'}'")
        return 0
    return subprocess.run(["npx", "-y", GHERKIN_LINT, *dirs]).returncode


def datacontracts(only: str | None, catalog_dir: str) -> int:
    found = False
    for d in service_dirs(catalog_dir, only):
        for dc in sorted((d / "data-contracts").glob("*.y*ml")):
            found = True
            print(f"linting {dc}")
            rc = subprocess.run(
                ["uvx", "--from", DATACONTRACT_CLI, "datacontract", "lint", str(dc)]
            ).returncode
            if rc:
                return rc
    if not found:
        print(f"no data contracts for '{only or '*'}'")
    return 0
