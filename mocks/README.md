# Mocks

Microcks serves every catalog service from one stack: REST mocks from the
OpenAPI contracts, ambient WebSocket events from the AsyncAPI contracts.

```sh
task mocks:load             # start the stack and load all services
task mocks:test             # smoke-test the mocks
task mocks:load SERVICE=orders
```

REST mocks: `http://localhost:8585/rest/<info.title>/<version>/...` (spaces
in the title become `+`). Event channels:
`ws://localhost:8081/api/ws/<info.title>/<version>/<operationName>`.

## Spike outcome (GRI-111)

- **AsyncAPI 3.0 imports cleanly into Microcks 1.15.** The importer bug that
  pinned pizza-pattern to 2.6 (microcks#2273, examples lost on multi-message
  operations) does not bite: catalog channels are single-message.
- **Async mocking needs `ws` channel bindings.** The specs carried no
  servers/bindings, so the minion had nothing to produce on. Added
  `bindings: {ws: {}}` per channel — additive, minor bumps (orders asyncapi
  1.4.0, payments asyncapi 0.6.0). With bindings plus event examples the
  minion publishes each example every ~3s.
- **Example artifacts** (`mocks/*.examples.yaml`, Microcks `APIExamples`)
  hold the fixtures so the gated specs don't need example payloads: REST
  operations use a `body:` key and a quoted `status:`; event messages use
  `eventMessage.payload`. The `metadata.name`/`version` must match the
  spec's `info.title`/`info.version` or the upload lands on the wrong
  service — the manifest/spec version lint keeps that honest.
