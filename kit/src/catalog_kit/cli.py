"""The `catalog` command - deterministic gates, lint and docs for a
contract-first service catalog. Run from the catalog repo's root."""

from __future__ import annotations

import argparse

from . import compat, docs_gen, intent, manifest_lint, surface, versioning


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="catalog", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="diff-based gates against a base ref")
    check_sub = check.add_subparsers(dest="gate", required=True)

    def diff_gate(name: str, help_: str, service: bool = True):
        p = check_sub.add_parser(name, help=help_)
        p.add_argument("--base", default="origin/main")
        p.add_argument("--catalog-dir", default="catalog")
        if service:
            p.add_argument("--service")
        return p

    diff_gate("version", "gated artifact changes must bump their versions",
              service=False)
    diff_gate("compat", "breaking contract changes must take a major bump")
    diff_gate("intent", "added schema elements must be named in features")

    p = check_sub.add_parser(
        "surface", help="changes under the named paths must bump a version file"
    )
    p.add_argument("--base", default="origin/main")
    p.add_argument("--version-file", required=True)
    p.add_argument("--json-key", default="version")
    p.add_argument("--paths", required=True,
                   help="comma-separated path prefixes that form the surface")

    lint = sub.add_parser("lint", help="static consistency checks")
    lint_sub = lint.add_subparsers(dest="target", required=True)
    p = lint_sub.add_parser("manifest",
                            help="manifests vs contracts and the catalog graph")
    p.add_argument("--catalog-dir", default="catalog")
    p.add_argument("--service")

    docs = sub.add_parser("docs", help="generated documentation")
    docs_sub = docs.add_subparsers(dest="action", required=True)
    p = docs_sub.add_parser("generate", help="emit site pages and rendered HTML")
    p.add_argument("--catalog-dir", default="catalog")
    p.add_argument("--docs-dir", default="docs")
    p.add_argument("--no-html", action="store_true",
                   help="skip the AsyncAPI/data-contract HTML rendering")

    args = parser.parse_args(argv)

    if args.command == "check":
        if args.gate == "version":
            return versioning.run(args.base, args.catalog_dir)
        if args.gate == "compat":
            return compat.run(args.base, args.service, args.catalog_dir)
        if args.gate == "intent":
            return intent.run(args.base, args.service, args.catalog_dir)
        if args.gate == "surface":
            return surface.run(
                args.base, args.version_file, args.json_key,
                [p for p in args.paths.split(",") if p],
            )
    if args.command == "lint":
        return manifest_lint.run(args.service, args.catalog_dir)
    if args.command == "docs":
        return docs_gen.run(args.catalog_dir, args.docs_dir, html=not args.no_html)
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
