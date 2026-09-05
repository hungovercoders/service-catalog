"""Entry point for the sysspec MCP server.

Two ways to serve the same read-only spec tools:

* stdio (default) — how the Claude plugin and local `.mcp.json` wire it up.
* http — streamable HTTP for a hosted deployment, so remote clients can
  connect with just a URL. Stateless, so it works behind load balancers
  and serverless platforms without session affinity.

Flags win over environment variables; the environment variables exist so
container platforms (which prefer env over argv) can configure the server
without a wrapper script.
"""

from __future__ import annotations

import argparse
import os

from sysspec_mcp.server import build_server


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="sysspec-mcp",
        description="Serve the sysspec read-only MCP tools over stdio or HTTP.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=_env("SYSSPEC_MCP_TRANSPORT", "stdio"),
        help="stdio for local clients (default); http for a URL-addressable "
        "server (env: SYSSPEC_MCP_TRANSPORT)",
    )
    parser.add_argument(
        "--host",
        default=_env("HOST", "127.0.0.1"),
        help="http only: interface to bind, e.g. 0.0.0.0 in a container "
        "(default 127.0.0.1; env: HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(_env("PORT", "8080")),
        help="http only: port to bind (default 8080; env: PORT)",
    )
    parser.add_argument(
        "--path",
        default=_env("SYSSPEC_MCP_PATH", "/mcp"),
        help="http only: URL path the MCP endpoint is served at "
        "(default /mcp; env: SYSSPEC_MCP_PATH)",
    )
    parser.add_argument(
        "--allowed-hosts",
        default=_env("SYSSPEC_MCP_ALLOWED_HOSTS", ""),
        help="http only: comma-separated Host header values to accept, e.g. "
        "mcp.example.com. When set, requests carrying any other Host are "
        "rejected with 421; unset, only loopback binds are guarded "
        "(env: SYSSPEC_MCP_ALLOWED_HOSTS)",
    )
    args = parser.parse_args(argv)

    server = build_server()
    if args.transport == "stdio":
        server.run()
        return

    allowed_hosts = [h.strip() for h in args.allowed_hosts.split(",") if h.strip()]
    server.run(
        transport="http",
        host=args.host,
        port=args.port,
        path=args.path,
        stateless_http=True,
        # fastmcp's default host_origin_protection setting is False, which
        # silently ignores allowed_hosts; "auto" enforces the allowlist when
        # one is given and guards loopback binds against DNS rebinding.
        host_origin_protection="auto",
        allowed_hosts=allowed_hosts or None,
    )


if __name__ == "__main__":
    main()
