"""Static linters over the catalog's specs, features and data contracts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .mocks import service_dirs
from .pins import DATACONTRACT_CLI, GHERKIN_LINT, SPECTRAL_CLI

# datacontract-cli validates a contract against the ODCS schema and offers no
# hook for house rules, so the naming half of the data-contract gate is
# Spectral - it lints any YAML, not just OpenAPI/AsyncAPI. A repo overrides
# the bundled default by dropping its own file at the root.
DC_RULESET_NAME = ".spectral-datacontracts.yaml"
DC_RULESET_DEFAULT = Path(__file__).parent / "data" / "spectral" / "datacontracts.yaml"


def spectral(files: list[str], ruleset: Path | None = None) -> int:
    args = ["npx", "-y", SPECTRAL_CLI, "lint", *files, "--fail-severity=warn"]
    if ruleset is not None:
        args += ["--ruleset", str(ruleset)]
    return subprocess.run(args).returncode


def specs(only: str | None, catalog_dir: str) -> int:
    files: list[str] = []
    for d in service_dirs(catalog_dir, only):
        for kind in ("asyncapi", "openapi"):
            files += sorted(str(p) for p in (d / kind).glob("*.y*ml"))
    if not files:
        print(f"no specs found for '{only or '*'}'", file=sys.stderr)
        return 1
    return spectral(files)


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
    files: list[str] = []
    for d in service_dirs(catalog_dir, only):
        files += sorted(str(dc) for dc in (d / "data-contracts").glob("*.y*ml"))
    if not files:
        print(f"no data contracts for '{only or '*'}'")
        return 0

    for dc in files:
        print(f"linting {dc}")
        rc = subprocess.run(
            ["uvx", "--from", DATACONTRACT_CLI, "datacontract", "lint", dc]
        ).returncode
        if rc:
            return rc

    override = Path(DC_RULESET_NAME)
    ruleset = override if override.is_file() else DC_RULESET_DEFAULT
    print(f"checking data contract naming against {ruleset}")
    return spectral(files, ruleset)
