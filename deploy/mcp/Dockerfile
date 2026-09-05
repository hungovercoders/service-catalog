# Serve this repo's specs over streamable-HTTP MCP.
#
# Build from the repo root so the kit source and specs are in context:
#
#   docker build -f deploy/mcp/Dockerfile -t sysspec-mcp .
#   docker run --rm -p 8080:8080 sysspec-mcp
#
# The endpoint is http://localhost:8080/mcp. The same image is what
# deploy/mcp/cloudflare/wrangler.jsonc deploys to Cloudflare Containers.
FROM python:3.12-slim

WORKDIR /app
COPY kit /tmp/kit
RUN pip install --no-cache-dir /tmp/kit && rm -rf /tmp/kit
COPY specs /app/specs

# Host-allowlist hardening for a public hostname is opt-in:
# add SYSSPEC_MCP_ALLOWED_HOSTS=mcp.example.com (comma-separated).
ENV SPECS_DIR=/app/specs \
    SYSSPEC_MCP_TRANSPORT=http \
    HOST=0.0.0.0 \
    PORT=8080

EXPOSE 8080
CMD ["sysspec-mcp"]
