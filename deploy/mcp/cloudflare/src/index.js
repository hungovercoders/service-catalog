// Worker shim: routes every request to the sysspec-mcp container.
// The server is stateless (no session affinity needed), so a single
// container instance behind the Durable Object is enough.
import { Container, getContainer } from "@cloudflare/containers";

export class SysspecMcp extends Container {
  defaultPort = 8080;
  sleepAfter = "15m"; // scale to zero when idle; cold start is a pip-less boot
}

export default {
  async fetch(request, env) {
    return getContainer(env.SYSSPEC_MCP).fetch(request);
  },
};
