# Serving the MCP server over a URL

The plugin and `.mcp.json` routes run `sysspec-mcp` over stdio on each
developer's machine. This directory is the third distribution channel: the
same server, the same read-only tools, behind a streamable-HTTP URL — for
clients that can't spawn a local process (claude.ai remote sessions, CI
agents, teammates who shouldn't need `uv`).

Nothing here changes the spec authority model. The image bakes in this
repo's `specs/` at build time, so a deploy is a snapshot: redeploy when
`main` changes (the same trigger as the docs site).

## Run it anywhere

`sysspec-mcp` grows an HTTP mode (kit >= 0.24.0):

```bash
SPECS_DIR=./specs sysspec-mcp --transport http --host 0.0.0.0 --port 8080
```

or via env (`SYSSPEC_MCP_TRANSPORT=http`, `HOST`, `PORT`,
`SYSSPEC_MCP_PATH`, `SYSSPEC_MCP_ALLOWED_HOSTS`) — see `sysspec-mcp --help`.
It serves stateless streamable HTTP at `/mcp`, so it works behind load
balancers and scale-to-zero platforms without session affinity.

The [Dockerfile](Dockerfile) packages that with this repo's specs. Build
from the repo root:

```bash
docker build -f deploy/mcp/Dockerfile -t sysspec-mcp .
docker run --rm -p 8080:8080 sysspec-mcp
```

That image runs unchanged on any container host (Cloud Run, Fly.io, ...).

## Cloudflare

[cloudflare/](cloudflare/) deploys the image on Cloudflare Containers — a
tiny Worker routes requests to the container, which sleeps when idle:

```bash
cd deploy/mcp/cloudflare
npm install
npx wrangler deploy   # needs `wrangler login` and a Workers paid plan
```

Wrangler builds the Dockerfile (repo root as context) and prints the URL;
the MCP endpoint is `https://sysspec-mcp.<account>.workers.dev/mcp`.

Note Containers requires the Workers *paid* plan. If that's a blocker, the
free-tier alternative is porting `kit/src/sysspec_mcp/server.py` to a
TypeScript Worker with specs as static assets — a second implementation to
keep in sync, which is why it isn't the route taken here.

## Connect a client

```bash
claude mcp add sysspec --scope project --transport http \
  https://sysspec-mcp.<account>.workers.dev/mcp
```

or in `.mcp.json`:

```json
{
  "mcpServers": {
    "sysspec": {
      "type": "http",
      "url": "https://sysspec-mcp.<account>.workers.dev/mcp"
    }
  }
}
```

The URL gives you the tools only; the skills still come from the plugin
install (`/plugin install sysspec@hungovercoders`), which keeps its local
stdio server — the two coexist under different server names, so pick one
per project to avoid duplicate tools.

## Hardening

The tools are read-only and reads are confined to declared artifacts, so
the blast radius of an open endpoint is "someone reads the specs". If the
specs are not public:

- put Cloudflare Access (or any authenticating proxy) in front of the
  Worker, or wire fastmcp's auth providers into `build_server()`;
- set `SYSSPEC_MCP_ALLOWED_HOSTS=mcp.example.com` (in the Dockerfile env)
  to reject requests carrying any other Host header.
