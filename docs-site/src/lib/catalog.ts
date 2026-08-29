import data from '../data/catalog.json';

export interface Artifact {
  kind: 'openapi' | 'asyncapi' | 'data-contract' | 'feature' | 'doc';
  path: string;
  stem: string;
  version: string | null;
  gated: boolean;
  summary: string;
  odcs?: Record<string, any>;
  text?: string;
}

export interface Service {
  name: string;
  title: string;
  version: string | null;
  domain: string;
  owner: string;
  summary: string;
  artifacts: Artifact[];
  produces: string[];
  consumes: string[];
}

export interface Edge {
  from: string;
  channel: string;
  to: string;
}

export const services = data.services as Service[];
export const edges = data.edges as Edge[];
export const unconsumed = data.unconsumed as { channel: string; producer: string }[];

/** Prefix a root-relative path with the configured base (GitHub Pages subpath). */
export const withBase = (path: string) =>
  `${import.meta.env.BASE_URL.replace(/\/$/, '')}${path}`;

/** URL of the raw artifact copied under public/ by `catalog docs data`. */
export const artifactUrl = (service: string, artifact: Artifact) =>
  withBase(`/catalog/${service}/${artifact.path}`);
