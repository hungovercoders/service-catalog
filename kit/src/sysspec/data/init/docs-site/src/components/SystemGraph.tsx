import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Background,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
} from '@xyflow/react';
import dagre from '@dagrejs/dagre';
import '@xyflow/react/dist/style.css';

interface GraphService {
  name: string;
  title: string;
  domain: string;
  version: string | null;
  operations: { op_id: string }[];
  data_products: { stem: string; title: string }[];
}

interface Props {
  services: GraphService[];
  edges: { from: string; channel: string; to: string }[];
  unconsumed: { channel: string; producer: string }[];
  base: string;
  focus?: string;
  height?: number;
}

const NODE = { width: 190, height: 56 };

const nodeStyle = (kind: string, focused = false): React.CSSProperties => {
  const common: React.CSSProperties = {
    width: NODE.width,
    fontSize: 13,
    borderRadius: 6,
    padding: '8px 10px',
    border: '1.5px solid var(--sl-color-gray-4, #888)',
    background: 'var(--sl-color-bg, #fff)',
    color: 'var(--sl-color-text, #222)',
  };
  if (kind === 'service')
    return {
      ...common,
      borderColor: 'var(--sl-color-accent, #4c5cd6)',
      borderWidth: focused ? 3 : 2,
    };
  if (kind === 'data') return { ...common, borderRadius: 18, opacity: 0.9 };
  if (kind === 'clients') return { ...common, borderRadius: 24, textAlign: 'center' };
  return { ...common, borderStyle: 'dashed', opacity: 0.7 };
};

function buildGraph(props: Props): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [];
  const edges: Edge[] = [];

  // With a focus, only that service's edges appear; neighbours stay as
  // plain nodes and the surface (ops, data products) is the focus's own.
  const inFocus = (name: string) =>
    !props.focus ||
    name === props.focus ||
    props.edges.some(
      (e) =>
        (e.from === name && e.to === props.focus) ||
        (e.to === name && e.from === props.focus),
    );
  const services = props.services.filter((s) => inFocus(s.name));
  const channelEdges = props.edges.filter(
    (e) => !props.focus || e.from === props.focus || e.to === props.focus,
  );
  const unconsumed = props.unconsumed.filter(
    (u) => !props.focus || u.producer === props.focus,
  );
  const owns = (name: string) => !props.focus || name === props.focus;

  const anyOps = services.some((s) => owns(s.name) && s.operations.length > 0);
  if (anyOps) {
    nodes.push({
      id: 'clients',
      data: { label: 'Clients' },
      position: { x: 0, y: 0 },
      style: nodeStyle('clients'),
    });
  }
  for (const s of services) {
    nodes.push({
      id: s.name,
      data: {
        label: (
          <div>
            <strong>{s.title}</strong>
            <div style={{ fontSize: 11, opacity: 0.75 }}>
              {s.domain} · v{s.version ?? '—'}
            </div>
          </div>
        ),
      },
      position: { x: 0, y: 0 },
      style: nodeStyle('service', s.name === props.focus),
    });
    if (!owns(s.name)) continue;
    for (const op of s.operations) {
      edges.push({
        id: `op-${s.name}-${op.op_id}`,
        source: 'clients',
        target: s.name,
        label: op.op_id,
        style: { opacity: 0.7 },
      });
    }
    for (const d of s.data_products) {
      const id = `dp-${s.name}-${d.stem}`;
      nodes.push({
        id,
        data: { label: d.title },
        position: { x: 0, y: 0 },
        style: nodeStyle('data'),
      });
      edges.push({ id: `e-${id}`, source: s.name, target: id, style: { opacity: 0.7 } });
    }
  }
  for (const e of channelEdges) {
    edges.push({
      id: `ch-${e.channel}-${e.to}`,
      source: e.from,
      target: e.to,
      label: e.channel,
      animated: true,
    });
  }
  for (const u of unconsumed) {
    const id = `sink-${u.channel}`;
    nodes.push({
      id,
      data: { label: 'no consumer yet' },
      position: { x: 0, y: 0 },
      style: nodeStyle('sink'),
    });
    edges.push({
      id: `ch-${u.channel}`,
      source: u.producer,
      target: id,
      label: u.channel,
      style: { strokeDasharray: '5 4' },
    });
  }

  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'LR', nodesep: 40, ranksep: 90 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of nodes) g.setNode(n.id, { ...NODE });
  for (const e of edges) g.setEdge(e.source, e.target);
  dagre.layout(g);
  for (const n of nodes) {
    const pos = g.node(n.id);
    n.position = { x: pos.x - NODE.width / 2, y: pos.y - NODE.height / 2 };
  }
  return { nodes, edges };
}

export default function SystemGraph(props: Props) {
  const { nodes, edges } = useMemo(() => buildGraph(props), [props]);
  const [selected, setSelected] = useState<string | null>(null);
  const [theme, setTheme] = useState<'light' | 'dark'>('light');

  useEffect(() => {
    const root = document.documentElement;
    const read = () => setTheme(root.dataset.theme === 'dark' ? 'dark' : 'light');
    read();
    const observer = new MutationObserver(read);
    observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);

  const connected = useMemo(() => {
    if (!selected) return null;
    const ids = new Set([selected]);
    for (const e of edges) {
      if (e.source === selected || e.target === selected) {
        ids.add(e.source);
        ids.add(e.target);
      }
    }
    return ids;
  }, [selected, edges]);

  const shownNodes = nodes.map((n) => ({
    ...n,
    style: {
      ...n.style,
      opacity: connected && !connected.has(n.id) ? 0.25 : (n.style?.opacity as number) ?? 1,
    },
  }));
  const shownEdges = edges.map((e) => ({
    ...e,
    style: {
      ...e.style,
      opacity: connected && !(e.source === selected || e.target === selected) ? 0.15 : 1,
    },
    labelStyle: {
      opacity: connected && !(e.source === selected || e.target === selected) ? 0.3 : 1,
    },
  }));

  const isService = useCallback(
    (id: string) => props.services.some((s) => s.name === id),
    [props.services],
  );

  return (
    <div
      style={{
        height: props.height ?? 520,
        border: '1px solid var(--sl-color-gray-5, #ddd)',
        borderRadius: 8,
      }}
    >
      <ReactFlow
        nodes={shownNodes}
        edges={shownEdges}
        colorMode={theme}
        fitView
        nodesConnectable={false}
        nodesDraggable
        onNodeClick={(_evt, node) => setSelected(selected === node.id ? null : node.id)}
        onNodeDoubleClick={(_evt, node) => {
          if (isService(node.id) && node.id !== props.focus)
            window.location.assign(`${props.base}/services/${node.id}/`);
        }}
        onPaneClick={() => setSelected(null)}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={18} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
