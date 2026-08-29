import data from '../data/specs.json';

export interface SchemaRow {
  field: string;
  type: string;
  required: boolean;
  constraints: string;
}

export interface Operation {
  op_id: string;
  kind: 'command' | 'query';
  method: string;
  path: string;
  summary: string;
  description: string;
  artifact_path: string;
  artifact_version: string | null;
  parameters: { name: string; in: string; type: string; required: boolean }[];
  request_rows: SchemaRow[];
  responses: { status: string; description: string }[];
  response_body: { status: string; rows: SchemaRow[] } | null;
  examples: { case: string; request: string | null; status: string | null; response: string | null }[];
}

export interface ChannelMessage {
  name: string;
  title: string;
  event_type: string | null;
  data_rows: SchemaRow[];
  envelope_rows: SchemaRow[];
}

export interface Channel {
  address: string;
  producer: string;
  producer_title: string;
  artifact_path: string;
  artifact_version: string | null;
  description: string;
  consumers: string[];
  sequence_mermaid: string;
  messages: ChannelMessage[];
  examples: { case: string; payload: string }[];
}

export interface Release {
  version: string;
  date: string | null;
  unreleased: boolean;
  tag?: string;
  sha?: string;
  commits: string[];
}

export interface ArtifactRelease {
  version: string;
  date: string | null;
  service_version: string;
}

export interface Artifact {
  kind: 'openapi' | 'asyncapi' | 'data-contract' | 'feature' | 'doc';
  path: string;
  stem: string;
  version: string | null;
  gated: boolean;
  summary: string;
  history: ArtifactRelease[];
  odcs?: Record<string, any>;
  er_mermaid?: string;
  text?: string;
  feature?: Feature;
  label?: string;
}

export interface GherkinStep {
  keyword?: string;
  phase?: 'given' | 'when' | 'then';
  text?: string;
  table?: string[][];
  heading?: string;
  comment?: string;
  prose?: string;
}

export interface Feature {
  title: string;
  description: string[];
  background: GherkinStep[];
  scenarios: { title: string; steps: GherkinStep[] }[];
}

export interface Service {
  name: string;
  title: string;
  version: string | null;
  domain: string;
  owner: string;
  summary: string;
  implementation_repo: string | null;
  artifacts: Artifact[];
  produces: string[];
  consumes: string[];
  operations: Operation[];
  data_products: { stem: string; title: string }[];
  channels: Channel[];
  changelog: Release[];
  latest_release: Release | null;
  ahead: boolean;
}

export const services = data.services as Service[];
export const edges = data.edges as { from: string; channel: string; to: string }[];
export const unconsumed = data.unconsumed as { channel: string; producer: string }[];

/** Deterministic heading id for a channel address or stem (dots → dashes). */
export const anchor = (address: string) => address.replaceAll('.', '-');

/** address → producing service, across the whole spec suite. */
export const channelProducers: Record<string, Service> = Object.fromEntries(
  services.flatMap((s) => s.channels.map((c) => [c.address, s])),
);

export const serviceByName: Record<string, Service> = Object.fromEntries(
  services.map((s) => [s.name, s]),
);

/** Reading order for a service's artifacts: contract → events → data → criteria → context. */
export const KIND_ORDER: Artifact['kind'][] = [
  'openapi',
  'asyncapi',
  'data-contract',
  'feature',
  'doc',
];

export const KIND_LABELS: Record<Artifact['kind'], string> = {
  openapi: 'API Contract',
  asyncapi: 'Events',
  'data-contract': 'Data',
  feature: 'Acceptance criteria',
  doc: 'Context',
};

export const sortedArtifacts = (s: Service) =>
  [...s.artifacts].sort(
    (a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind),
  );

/** Prefix a root-relative path with the configured base (GitHub Pages subpath). */
export const withBase = (path: string) =>
  `${import.meta.env.BASE_URL.replace(/\/$/, '')}${path}`;

/** URL of the raw artifact copied under public/ by `sysspec docs data`. */
export const artifactUrl = (service: string, artifact: Artifact | { path: string }) =>
  withBase(`/specs/${service}/${artifact.path}`);

/** Rendered page for an artifact (features share one page per service). */
export const artifactPage = (service: string, artifact: Artifact) =>
  artifact.kind === 'feature'
    ? withBase(`/services/${service}/features/`)
    : withBase(`/services/${service}/${artifact.stem}/`);

/** Link target for a channel: its producer's rendered AsyncAPI page. */
export const channelHref = (address: string) => {
  const producer = channelProducers[address];
  if (!producer) return null;
  const channel = producer.channels.find((c) => c.address === address);
  const stem = channel?.artifact_path.split('/').pop()?.replace(/\.[^.]+$/, '');
  if (!stem) return withBase(`/services/${producer.name}/`);
  return withBase(`/services/${producer.name}/${stem}/`);
};
