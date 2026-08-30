"""The `specs` command - deterministic gates, lint, docs and mock
orchestration for a contract-first service specs. Run from the specs
repo's root."""

from __future__ import annotations

import argparse
import sys

from . import compat, docs_data, docs_gen, intent, lint as linters, manifest_lint, mocks, nullsvc, scaffold, surface, versioning


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    tail: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, tail = argv[:split], argv[split + 1:]

    parser = argparse.ArgumentParser(prog="sysspec", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="diff-based gates against a base ref")
    check_sub = check.add_subparsers(dest="gate", required=True)

    def diff_gate(name: str, help_: str, service: bool = True):
        p = check_sub.add_parser(name, help=help_)
        p.add_argument("--base", default="origin/main")
        p.add_argument("--specs-dir", default="specs")
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
    for name, help_ in [
        ("manifest", "manifests vs contracts and the specs graph"),
        ("specs", "spectral over the OpenAPI and AsyncAPI contracts"),
        ("features", "gherkin-lint over the acceptance criteria"),
        ("datacontracts", "datacontract-cli over the ODCS contracts"),
    ]:
        p = lint_sub.add_parser(name, help=help_)
        p.add_argument("--specs-dir", default="specs")
        p.add_argument("--service")

    docs = sub.add_parser("docs", help="generated documentation")
    docs_sub = docs.add_subparsers(dest="action", required=True)
    p = docs_sub.add_parser(
        "data", help="emit the normalized spec data the docs site renders from"
    )
    p.add_argument("--specs-dir", default="specs")
    p.add_argument("--site-dir", default="docs-site")
    p.add_argument("--mocks-dir", default="mocks",
                   help="directory of Microcks example files used for message examples")
    p = docs_sub.add_parser(
        "diagrams", help="parse every mermaid diagram with mermaid-cli"
    )
    p.add_argument("--specs-dir", default="specs")
    p.add_argument("--docs-dir", default="docs")
    p.add_argument("--site-dir", default="docs-site")

    p = sub.add_parser("init", help="scaffold a new spec repository")
    p.add_argument("dir", help="target directory (must be empty or absent)")
    p.add_argument("--org", required=True,
                   help="reverse-DNS event-type prefix, e.g. com.acme")
    p.add_argument("--sysspec-repo", default="hungovercoders/sysspec",
                   help="owner/repo whose reusable workflows the scaffold references")

    mk = sub.add_parser("mocks", help="Microcks mock stack, driven by the specs")
    mk_sub = mk.add_subparsers(dest="action", required=True)

    def mock_cmd(name: str, help_: str, scoped: bool = True):
        p = mk_sub.add_parser(name, help=help_)
        p.add_argument("--compose-file")
        if scoped:
            p.add_argument("--service")
            p.add_argument("--specs-dir", default="specs")
            p.add_argument("--mocks-dir", default="mocks")
            p.add_argument("--microcks-url", default="http://localhost:8585")
            p.add_argument("--async-minion-url", default="http://localhost:8081")
        return p

    mock_cmd("up", "start the stack", scoped=False)
    mock_cmd("down", "stop the stack", scoped=False)
    mock_cmd("load", "start the stack and load every service's specs and examples")
    mock_cmd("test", "smoke-test the mocks against the example files")
    p = mk_sub.add_parser("watch", help="print events from one mock channel")
    p.add_argument("--channel", required=True,
                   help="<Title>/<version>/<operation> as Microcks names them")
    p.add_argument("--async-minion-url", default="http://localhost:8081")

    ct = sub.add_parser(
        "contract",
        help="hold an implementation - or the loaded mocks - to the contracts",
    )
    ct_sub = ct.add_subparsers(dest="action", required=True)
    p = ct_sub.add_parser(
        "test",
        help="Microcks replays every spec operation and validates the responses"
             " and events; endpoint overrides target a real implementation,"
             " no overrides target the mocks (spec conformance)",
    )
    p.add_argument("--service")
    p.add_argument("--specs-dir", default="specs")
    p.add_argument("--microcks-url", default="http://localhost:8585")
    p.add_argument("--rest-endpoint",
                   help="test a real HTTP implementation instead of the mock")
    p.add_argument("--async-endpoint",
                   help="test a real event transport instead of the mock")

    nl = sub.add_parser(
        "null",
        help="falsifiability gate - the bound feature suite must fail"
             " entirely against a service that proves nothing",
    )
    nl_sub = nl.add_subparsers(dest="action", required=True)
    p = nl_sub.add_parser(
        "run",
        help="serve 200 {} to every request, run the suite command after --,"
             " fail unless zero scenarios pass",
    )
    p.add_argument("--port", type=int, default=9099)
    p.add_argument("--results", required=True,
                   help="cucumber-format JSON the suite command writes"
                        " (e.g. cucumber-js --format json:<file>)")
    p.add_argument("--timeout", type=int, default=300,
                   help="seconds before a hung suite is killed and failed")

    args = parser.parse_args(argv)

    if args.command == "check":
        if args.gate == "version":
            return versioning.run(args.base, args.specs_dir)
        if args.gate == "compat":
            return compat.run(args.base, args.service, args.specs_dir)
        if args.gate == "intent":
            return intent.run(args.base, args.service, args.specs_dir)
        if args.gate == "surface":
            return surface.run(
                args.base, args.version_file, args.json_key,
                [p for p in args.paths.split(",") if p],
            )
    if args.command == "lint":
        if args.target == "manifest":
            return manifest_lint.run(args.service, args.specs_dir)
        if args.target == "specs":
            return linters.specs(args.service, args.specs_dir)
        if args.target == "features":
            return linters.features(args.service, args.specs_dir)
        if args.target == "datacontracts":
            return linters.datacontracts(args.service, args.specs_dir)
    if args.command == "init":
        return scaffold.run(args.dir, args.org, args.sysspec_repo)
    if args.command == "docs":
        if args.action == "data":
            return docs_data.run(args.specs_dir, args.site_dir,
                                 mocks_dir=args.mocks_dir)
        if args.action == "diagrams":
            return docs_gen.check_diagrams(args.specs_dir, args.docs_dir,
                                           site_dir=args.site_dir)
    if args.command == "mocks":
        if args.action == "watch":
            return mocks.watch(args.channel, args.async_minion_url)
        compose = mocks.compose_file(args.compose_file)
        if args.action == "up":
            return mocks.up(compose)
        if args.action == "down":
            return mocks.down(compose)
        if args.action == "load":
            return mocks.load(args.service, args.specs_dir, args.mocks_dir,
                              args.microcks_url, args.async_minion_url, compose)
        if args.action == "test":
            return mocks.test(args.service, args.specs_dir, args.mocks_dir,
                              args.microcks_url, args.async_minion_url)
    if args.command == "contract":
        return mocks.contract(args.service, args.specs_dir,
                              args.microcks_url, args.rest_endpoint,
                              args.async_endpoint)
    if args.command == "null":
        return nullsvc.run(args.port, args.results, args.timeout, tail)
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
