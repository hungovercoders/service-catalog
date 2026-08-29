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

/** True when the service has a unified message reference page. */
export const hasMessages = (s: Service) =>
  s.operations.length > 0 || s.channels.length > 0 || s.data_products.length > 0;

/** Prefix a root-relative path with the configured base (GitHub Pages subpath). */
export const withBase = (path: string) =>
  `${import.meta.env.BASE_URL.replace(/\/$/, '')}${path}`;

/** URL of the raw artifact copied under public/ by `sysspec docs data`. */
export const artifactUrl = (service: string, artifact: Artifact | { path: string }) =>
  withBase(`/specs/${service}/${artifact.path}`);

/** Link target for a channel's message-reference entry. */
export const channelHref = (address: string) => {
  const producer = channelProducers[address];
  if (!producer) return null;
  return withBase(`/services/${producer.name}/messages/#${anchor(address)}`);
};
