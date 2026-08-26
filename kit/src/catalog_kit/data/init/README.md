# Service catalog

The contract of record for this domain's services: AsyncAPI, OpenAPI, ODCS
data contracts and Gherkin acceptance criteria, versioned and gated. Built
on [catalog-kit](https://github.com/__CATALOG_REPO_SLUG__).

- `task ci` is the definition of green - the same gates run locally, in the
  pre-commit hook and in CI.
- Gated artifacts are never edited to make an implementation pass. Bump the
  artifact and service versions in `service.yaml` with every change; merges
  to main publish each changed service as a `<service>/v<version>` git tag
  that implementation and consumer repos pin.
- `task mocks:load` stands up Microcks mocks of every service so UIs and
  consumers can build before implementations exist.
- The machinery arrives by reference and stays current via Renovate: the
  `catalog-kit==` pin in `Taskfile.yml` / `.mcp.json`, and the reusable
  workflows under `.github/workflows/`.
- Agents get the same catalog over MCP (`.mcp.json`) and the deeper
  processes via the service-catalog plugin's skills.

The `greeter` service is scaffold output - replace it with your first real
service.
