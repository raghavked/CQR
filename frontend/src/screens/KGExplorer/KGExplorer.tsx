/**
 * KG Explorer — §7.4 — HERO VISUALIZATION
 * Canvas/WebGL force-directed graph of the Knowledge Graph.
 * Vulnerability paths rendered as dashed danger-colored edges.
 * Parallel list view for accessibility.
 */

import React, { useEffect, useRef, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import * as d3 from 'd3';
import { useSessionStore, useKGStore, useScanStore } from '../../stores';
import { api, type KGNode, type KGEdge, type SecurityFinding } from '../../api/client';
import { Button, EmptyState, Spinner, StatusChip, Panel } from '../../components';

// ── Node color by type ─────────────────────────────────────────────────────

function nodeColor(type: string): string {
  switch (type) {
    case 'File':     return '#5588CC';
    case 'Function': return '#44AA66';
    case 'EnvRef':   return '#CC8833';
    case 'Class':    return '#CC4444';
    default:         return '#8A8A8A';
  }
}

function nodeRadius(type: string): number {
  return type === 'File' ? 10 : type === 'Class' ? 8 : 5;
}

// ── Canvas KG Graph ────────────────────────────────────────────────────────

interface SimNode extends d3.SimulationNodeDatum {
  id: string;
  type: string;
  label: string;
  x?: number;
  y?: number;
}

interface SimLink extends d3.SimulationLinkDatum<SimNode> {
  source: SimNode | string;
  target: SimNode | string;
  edge_type: string;
  isVulnPath?: boolean;
}

const KGCanvas: React.FC<{
  nodes: KGNode[];
  edges: KGEdge[];
  findings: SecurityFinding[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}> = ({ nodes, edges, findings, selectedId, onSelect }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const simRef = useRef<d3.Simulation<SimNode, SimLink> | null>(null);
  const transformRef = useRef<d3.ZoomTransform>(d3.zoomIdentity);
  const simNodesRef = useRef<SimNode[]>([]);
  const simLinksRef = useRef<SimLink[]>([]);
  const animFrameRef = useRef<number>(0);

  // Build vulnerability path node sets
  const vulnNodeIds = new Set<string>();
  const vulnEdgePairs = new Set<string>();
  findings.forEach((f) => {
    f.node_path.forEach((id) => vulnNodeIds.add(id));
    for (let i = 0; i < f.node_path.length - 1; i++) {
      vulnEdgePairs.add(`${f.node_path[i]}-${f.node_path[i + 1]}`);
    }
  });

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const { width, height } = canvas;
    const t = transformRef.current;

    ctx.clearRect(0, 0, width, height);
    ctx.save();
    ctx.translate(t.x, t.y);
    ctx.scale(t.k, t.k);

    const simNodes = simNodesRef.current;
    const simLinks = simLinksRef.current;
    const nodeMap = new Map(simNodes.map((n) => [n.id, n]));

    // Draw edges
    simLinks.forEach((link) => {
      const src = typeof link.source === 'string' ? nodeMap.get(link.source) : link.source;
      const tgt = typeof link.target === 'string' ? nodeMap.get(link.target) : link.target;
      if (!src?.x || !tgt?.x) return;

      const isVuln = vulnEdgePairs.has(`${src.id}-${tgt.id}`) || vulnEdgePairs.has(`${tgt.id}-${src.id}`);

      ctx.beginPath();
      ctx.moveTo(src.x, src.y!);
      ctx.lineTo(tgt.x, tgt.y!);

      if (isVuln) {
        ctx.strokeStyle = '#CC4444';
        ctx.lineWidth = 1.5 / t.k;
        ctx.setLineDash([4, 3]);
        ctx.globalAlpha = 0.9;
      } else {
        ctx.strokeStyle = '#2A2A2A';
        ctx.lineWidth = 0.8 / t.k;
        ctx.setLineDash([]);
        ctx.globalAlpha = selectedId ? 0.3 : 0.7;
      }
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    });

    // Draw nodes
    simNodes.forEach((node) => {
      if (!node.x) return;
      const r = nodeRadius(node.type);
      const isSelected = node.id === selectedId;
      const isVuln = vulnNodeIds.has(node.id);

      ctx.beginPath();
      ctx.arc(node.x, node.y!, r, 0, Math.PI * 2);
      ctx.fillStyle = isVuln ? '#CC4444' : nodeColor(node.type);
      ctx.globalAlpha = selectedId && !isSelected ? 0.3 : 1;
      ctx.fill();

      if (isSelected) {
        ctx.strokeStyle = '#F0F0F0';
        ctx.lineWidth = 1.5 / t.k;
        ctx.stroke();
      }
      ctx.globalAlpha = 1;

      // Label for selected or large nodes
      if (isSelected || (node.type === 'File' && t.k > 0.5)) {
        ctx.fillStyle = '#F0F0F0';
        ctx.font = `${10 / t.k}px JetBrains Mono, monospace`;
        ctx.globalAlpha = isSelected ? 1 : 0.6;
        ctx.fillText(node.label.slice(0, 20), node.x + r + 3, node.y! + 4);
        ctx.globalAlpha = 1;
      }
    });

    ctx.restore();
  }, [selectedId, vulnNodeIds, vulnEdgePairs]);

  // Build simulation
  useEffect(() => {
    if (nodes.length === 0) return;

    const simNodes: SimNode[] = nodes.slice(0, 2000).map((n) => ({
      id: n.id,
      type: n.type,
      label: String(n.properties['n.path'] || n.properties['n.name'] || n.properties['n.id'] || n.id).split('/').pop() || n.id,
    }));

    const nodeIds = new Set(simNodes.map((n) => n.id));
    const simLinks: SimLink[] = edges
      .filter((e) => nodeIds.has(e.from_id) && nodeIds.has(e.to_id))
      .slice(0, 5000)
      .map((e) => ({
        source: e.from_id,
        target: e.to_id,
        edge_type: e.edge_type,
        isVulnPath: vulnEdgePairs.has(`${e.from_id}-${e.to_id}`),
      }));

    simNodesRef.current = simNodes;
    simLinksRef.current = simLinks;

    const canvas = canvasRef.current;
    if (!canvas) return;
    const { width, height } = canvas;

    const sim = d3.forceSimulation<SimNode>(simNodes)
      .force('link', d3.forceLink<SimNode, SimLink>(simLinks).id((d) => d.id).distance(40).strength(0.3))
      .force('charge', d3.forceManyBody().strength(-60))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius((d) => nodeRadius((d as SimNode).type) + 2))
      .alphaDecay(0.02)
      .on('tick', () => {
        cancelAnimationFrame(animFrameRef.current);
        animFrameRef.current = requestAnimationFrame(draw);
      });

    simRef.current = sim;

    // Stop after 2s
    setTimeout(() => sim.alphaTarget(0).stop(), 2000);

    return () => { sim.stop(); cancelAnimationFrame(animFrameRef.current); };
  }, [nodes, edges, draw, vulnEdgePairs]);

  // Zoom + pan
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const zoom = d3.zoom<HTMLCanvasElement, unknown>()
      .scaleExtent([0.1, 10])
      .on('zoom', (event) => {
        transformRef.current = event.transform;
        requestAnimationFrame(draw);
      });

    d3.select(canvas).call(zoom);

    // Click to select
    const handleClick = (event: MouseEvent) => {
      const t = transformRef.current;
      const rect = canvas.getBoundingClientRect();
      const mx = (event.clientX - rect.left - t.x) / t.k;
      const my = (event.clientY - rect.top - t.y) / t.k;

      let closest: SimNode | null = null;
      let minDist = 20;
      simNodesRef.current.forEach((n) => {
        if (!n.x) return;
        const d = Math.hypot(n.x - mx, (n.y || 0) - my);
        if (d < minDist) { minDist = d; closest = n; }
      });
      onSelect(closest ? (closest as SimNode).id : null);
    };

    canvas.addEventListener('click', handleClick);
    return () => canvas.removeEventListener('click', handleClick);
  }, [draw, onSelect]);

  // Resize
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const observer = new ResizeObserver(() => {
      canvas.width = canvas.offsetWidth;
      canvas.height = canvas.offsetHeight;
      requestAnimationFrame(draw);
    });
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [draw]);

  return (
    <canvas
      ref={canvasRef}
      style={{ width: '100%', height: '100%', display: 'block', cursor: 'crosshair' }}
      aria-label="Knowledge Graph visualization — use list view for accessible navigation"
    />
  );
};

// ── Node Detail Panel ──────────────────────────────────────────────────────

const NodeDetail: React.FC<{ node: KGNode | null; onOpenIDE: (path: string) => void }> = ({ node, onOpenIDE }) => {
  const navigate = useNavigate();
  if (!node) return (
    <div style={{ padding: 'var(--sp-4)', color: 'var(--text-muted)', fontSize: 12 }}>
      Click a node to inspect it
    </div>
  );

  const props = node.properties;
  const path = String(props['n.path'] || props['n.name'] || '');

  return (
    <div style={{ padding: 'var(--sp-3)', display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
      <div>
        <div className="label" style={{ marginBottom: 4 }}>Type</div>
        <StatusChip label={node.type} severity={node.type === 'EnvRef' ? 'warning' : 'info'} />
      </div>
      {path && (
        <div>
          <div className="label" style={{ marginBottom: 4 }}>Path</div>
          <div className="mono truncate" style={{ fontSize: 11 }}>{path}</div>
        </div>
      )}
      <div>
        <div className="label" style={{ marginBottom: 4 }}>ID</div>
        <div className="mono" style={{ fontSize: 10, color: 'var(--text-muted)' }}>{node.id}</div>
      </div>
      {path && (
        <Button variant="ghost" onClick={() => navigate('/ide')} style={{ fontSize: 11 }}>
          Open in IDE →
        </Button>
      )}
    </div>
  );
};

// ── KG Explorer ────────────────────────────────────────────────────────────

export const KGExplorer: React.FC = () => {
  const navigate = useNavigate();
  const { activeProject } = useSessionStore();
  const { nodes, edges, selectedNodeId, loading, error, setGraph, setSelectedNode, setLoading, setError } = useKGStore();
  const { findings } = useScanStore();
  const [viewMode, setViewMode] = useState<'graph' | 'list'>('graph');
  const [filterType, setFilterType] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState('');

  const load = useCallback(async () => {
    if (!activeProject) return;
    setLoading(true);
    setError(null);
    try {
      const [nodesData, edgesData] = await Promise.all([
        api.kg.nodes(activeProject.id),
        api.kg.edges(activeProject.id),
      ]);
      setGraph(nodesData, edgesData);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [activeProject, setGraph, setLoading, setError]);

  useEffect(() => { load(); }, [load]);

  if (!activeProject) {
    return <EmptyState title="No project open" description="Open a project to explore its Knowledge Graph." cta={{ label: 'Project Hub', onClick: () => navigate('/') }} />;
  }

  const selectedNode = nodes.find((n) => n.id === selectedNodeId) || null;
  const nodeTypes = ['all', ...Array.from(new Set(nodes.map((n) => n.type)))];

  const filteredNodes = nodes.filter((n) => {
    if (filterType !== 'all' && n.type !== filterType) return false;
    if (searchQuery) {
      const label = String(n.properties['n.path'] || n.properties['n.name'] || n.id).toLowerCase();
      return label.includes(searchQuery.toLowerCase());
    }
    return true;
  });

  const vulnCount = findings.length;

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* Left controls */}
      <div style={{
        width: 200, flexShrink: 0, borderRight: 'var(--border)',
        background: 'var(--surface)', padding: 'var(--sp-3)',
        display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)', overflow: 'auto'
      }}>
        <div>
          <div className="label" style={{ marginBottom: 'var(--sp-2)' }}>View</div>
          <div style={{ display: 'flex', gap: 'var(--sp-1)' }}>
            <Button variant={viewMode === 'graph' ? 'primary' : 'ghost'} onClick={() => setViewMode('graph')} style={{ flex: 1, fontSize: 11 }}>Graph</Button>
            <Button variant={viewMode === 'list' ? 'primary' : 'ghost'} onClick={() => setViewMode('list')} style={{ flex: 1, fontSize: 11 }}>List</Button>
          </div>
        </div>

        <div>
          <div className="label" style={{ marginBottom: 'var(--sp-2)' }}>Node Type</div>
          {nodeTypes.map((t) => (
            <div
              key={t}
              onClick={() => setFilterType(t)}
              style={{
                padding: '3px var(--sp-2)', cursor: 'pointer', fontSize: 12,
                color: filterType === t ? 'var(--text)' : 'var(--text-muted)',
                background: filterType === t ? 'var(--raised)' : 'transparent',
              }}
            >
              {t}
            </div>
          ))}
        </div>

        <div>
          <div className="label" style={{ marginBottom: 'var(--sp-2)' }}>Search</div>
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Filter nodes…"
            style={{
              width: '100%', background: 'var(--raised)', border: 'var(--border)',
              color: 'var(--text)', fontFamily: 'var(--font-mono)', fontSize: 11,
              padding: 'var(--sp-1) var(--sp-2)', outline: 'none'
            }}
            className="selectable"
          />
        </div>

        <div style={{ marginTop: 'auto' }}>
          <div className="label" style={{ marginBottom: 'var(--sp-2)' }}>Stats</div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.8 }}>
            <div>{nodes.length} nodes</div>
            <div>{edges.length} edges</div>
            {vulnCount > 0 && <div style={{ color: 'var(--status-danger)' }}>{vulnCount} vulnerabilities</div>}
          </div>
        </div>

        <Button variant="ghost" onClick={load} style={{ fontSize: 11 }}>Refresh</Button>
      </div>

      {/* Main canvas / list */}
      <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        {loading ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
            <Spinner size={24} />
          </div>
        ) : error ? (
          <div style={{ padding: 'var(--sp-5)', color: 'var(--status-danger)', fontFamily: 'var(--font-mono)', fontSize: 12 }}>
            {error} <Button variant="ghost" onClick={load}>Retry</Button>
          </div>
        ) : nodes.length === 0 ? (
          <EmptyState title="No graph data" description="Index a repository to build the Knowledge Graph." />
        ) : viewMode === 'graph' ? (
          <KGCanvas
            nodes={filterType === 'all' ? nodes : filteredNodes}
            edges={edges}
            findings={findings}
            selectedId={selectedNodeId}
            onSelect={setSelectedNode}
          />
        ) : (
          /* Accessible list view */
          <div style={{ overflow: 'auto', height: '100%' }} role="tree" aria-label="Knowledge Graph node list">
            {filteredNodes.map((n) => {
              const label = String(n.properties['n.path'] || n.properties['n.name'] || n.id).split('/').pop() || n.id;
              return (
                <div
                  key={n.id}
                  onClick={() => setSelectedNode(n.id)}
                  style={{
                    padding: 'var(--sp-2) var(--sp-4)',
                    borderBottom: 'var(--border)',
                    cursor: 'pointer',
                    background: n.id === selectedNodeId ? 'var(--raised)' : 'transparent',
                    display: 'flex', alignItems: 'center', gap: 'var(--sp-3)',
                    fontSize: 12,
                  }}
                  role="treeitem"
                  tabIndex={0}
                  onKeyDown={(e) => e.key === 'Enter' && setSelectedNode(n.id)}
                >
                  <StatusChip label={n.type} severity={n.type === 'EnvRef' ? 'warning' : 'info'} />
                  <span className="mono truncate">{label}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Right detail panel */}
      <div style={{
        width: 220, flexShrink: 0, borderLeft: 'var(--border)',
        background: 'var(--surface)', overflow: 'auto'
      }}>
        <div style={{ padding: 'var(--sp-2) var(--sp-3)', borderBottom: 'var(--border)' }}>
          <span className="label">Node Detail</span>
        </div>
        <NodeDetail node={selectedNode} onOpenIDE={() => navigate('/ide')} />
      </div>
    </div>
  );
};
