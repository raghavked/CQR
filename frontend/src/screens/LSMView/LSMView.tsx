/**
 * LSM View — §7.5 — HERO VISUALIZATION
 * Radial proximity map: central query node, others positioned by semantic distance.
 * Live dashed threshold ring. Node size encodes proximity.
 * Parallel ranked list view on the right for accessibility.
 */

import React, { useEffect, useRef, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSessionStore, useLSMStore } from '../../stores';
import { api, type LSMNode } from '../../api/client';
import { Button, EmptyState, Spinner, Slider, MetricReadout, ProgressBar } from '../../components';

// ── Radial Canvas ──────────────────────────────────────────────────────────

const LSMCanvas: React.FC<{
  nodes: LSMNode[];
  threshold: number;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}> = ({ nodes, threshold, selectedId, onSelect }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const dashOffsetRef = useRef(0);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const { width, height } = canvas;
    const cx = width / 2;
    const cy = height / 2;
    const maxR = Math.min(width, height) * 0.42;

    ctx.clearRect(0, 0, width, height);

    // Background
    ctx.fillStyle = '#080808';
    ctx.fillRect(0, 0, width, height);

    // Threshold ring
    const thresholdR = maxR * threshold;
    ctx.beginPath();
    ctx.arc(cx, cy, thresholdR, 0, Math.PI * 2);
    ctx.strokeStyle = '#5588CC';
    ctx.lineWidth = 1;
    ctx.setLineDash([6, 4]);
    ctx.lineDashOffset = -dashOffsetRef.current;
    ctx.stroke();
    ctx.setLineDash([]);

    // Inner budget zone
    ctx.beginPath();
    ctx.arc(cx, cy, thresholdR, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(85, 136, 204, 0.04)';
    ctx.fill();

    // Nodes
    const maxScore = Math.max(...nodes.map((n) => n.proximity_score || 0), 0.01);

    nodes.forEach((node, i) => {
      const score = node.proximity_score ?? 0;
      // Position: angle evenly distributed, radius = (1 - score) * maxR
      const angle = (i / nodes.length) * Math.PI * 2 - Math.PI / 2;
      const r = (1 - score) * maxR;
      const x = cx + r * Math.cos(angle);
      const y = cy + r * Math.sin(angle);

      const nodeR = 3 + (score / maxScore) * 6;
      const inBudget = score >= threshold;
      const isSelected = node.node_id === selectedId;

      // Connection line to center
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(x, y);
      ctx.strokeStyle = inBudget ? 'rgba(85,136,204,0.2)' : 'rgba(42,42,42,0.5)';
      ctx.lineWidth = 0.5;
      ctx.stroke();

      // Node circle
      ctx.beginPath();
      ctx.arc(x, y, nodeR, 0, Math.PI * 2);
      ctx.fillStyle = inBudget ? '#5588CC' : '#2A2A2A';
      ctx.globalAlpha = isSelected ? 1 : inBudget ? 0.8 : 0.4;
      ctx.fill();

      if (isSelected) {
        ctx.strokeStyle = '#F0F0F0';
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    });

    // Center node
    ctx.beginPath();
    ctx.arc(cx, cy, 8, 0, Math.PI * 2);
    ctx.fillStyle = '#F0F0F0';
    ctx.fill();
    ctx.fillStyle = '#080808';
    ctx.font = '9px JetBrains Mono, monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('Q', cx, cy);
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';

    // Threshold label
    ctx.fillStyle = '#5588CC';
    ctx.font = '10px JetBrains Mono, monospace';
    ctx.fillText(`≥ ${threshold.toFixed(2)}`, cx + thresholdR + 4, cy);
  }, [nodes, threshold, selectedId]);

  // Animate threshold ring dash march
  useEffect(() => {
    let running = true;
    const animate = () => {
      if (!running) return;
      dashOffsetRef.current = (dashOffsetRef.current + 0.3) % 20;
      draw();
      animRef.current = requestAnimationFrame(animate);
    };
    animRef.current = requestAnimationFrame(animate);
    return () => { running = false; cancelAnimationFrame(animRef.current); };
  }, [draw]);

  // Click to select
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const handleClick = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const cx = canvas.width / 2;
      const cy = canvas.height / 2;
      const maxR = Math.min(canvas.width, canvas.height) * 0.42;

      let closest: LSMNode | null = null;
      let minDist = 16;
      nodes.forEach((node, i) => {
        const score = node.proximity_score ?? 0;
        const angle = (i / nodes.length) * Math.PI * 2 - Math.PI / 2;
        const r = (1 - score) * maxR;
        const x = cx + r * Math.cos(angle);
        const y = cy + r * Math.sin(angle);
        const d = Math.hypot(x - mx, y - my);
        if (d < minDist) { minDist = d; closest = node; }
      });
      onSelect(closest ? (closest as LSMNode).node_id : null);
    };
    canvas.addEventListener('click', handleClick);
    return () => canvas.removeEventListener('click', handleClick);
  }, [nodes, onSelect]);

  // Resize
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const observer = new ResizeObserver(() => {
      canvas.width = canvas.offsetWidth;
      canvas.height = canvas.offsetHeight;
    });
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

  return (
    <canvas
      ref={canvasRef}
      style={{ width: '100%', height: '100%', display: 'block' }}
      aria-label="LSM radial proximity map — use the ranked list on the right for accessible navigation"
    />
  );
};

// ── LSM View ───────────────────────────────────────────────────────────────

export const LSMView: React.FC = () => {
  const navigate = useNavigate();
  const { activeProject } = useSessionStore();
  const { proximityNodes, threshold, query, loading, error, setProximityNodes, setThreshold, setQuery, setLoading, setError } = useLSMStore();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [queryInput, setQueryInput] = useState('login');

  const load = useCallback(async (q: string, t: number) => {
    if (!activeProject) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.lsm.proximity(activeProject.id, q, t, 30);
      setProximityNodes(data);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [activeProject, setProximityNodes, setLoading, setError]);

  useEffect(() => { load(query || 'login', threshold); }, []);

  const handleThresholdChange = (v: number) => {
    setThreshold(v);
    load(query || queryInput, v);
  };

  const handleQuerySubmit = () => {
    setQuery(queryInput);
    load(queryInput, threshold);
  };

  const inBudget = proximityNodes.filter((n) => (n.proximity_score ?? 0) >= threshold);
  const outOfBudget = proximityNodes.filter((n) => (n.proximity_score ?? 0) < threshold);

  // Estimate token savings (each node ~50 tokens vs full file ~500)
  const rawTokens = proximityNodes.length * 500;
  const kgTokens = inBudget.length * 50;
  const savings = rawTokens > 0 ? Math.round((1 - kgTokens / rawTokens) * 100) : 0;

  if (!activeProject) {
    return <EmptyState title="No project open" description="Open a project to view LSM proximity." cta={{ label: 'Project Hub', onClick: () => navigate('/') }} />;
  }

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* Left controls */}
      <div style={{
        width: 200, flexShrink: 0, borderRight: 'var(--border)',
        background: 'var(--surface)', padding: 'var(--sp-3)',
        display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)', overflow: 'auto'
      }}>
        <div>
          <div className="label" style={{ marginBottom: 'var(--sp-2)' }}>Query Context</div>
          <input
            value={queryInput}
            onChange={(e) => setQueryInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleQuerySubmit()}
            placeholder="e.g. login, auth…"
            style={{
              width: '100%', background: 'var(--raised)', border: 'var(--border)',
              color: 'var(--text)', fontFamily: 'var(--font-mono)', fontSize: 11,
              padding: 'var(--sp-1) var(--sp-2)', outline: 'none', marginBottom: 'var(--sp-2)'
            }}
            className="selectable"
          />
          <Button variant="primary" onClick={handleQuerySubmit} loading={loading} style={{ width: '100%', fontSize: 11 }}>
            Map
          </Button>
        </div>

        <Slider
          min={0}
          max={1}
          step={0.01}
          value={threshold}
          onChange={handleThresholdChange}
          label="Threshold"
        />

        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-2)' }}>
          <div className="label">Budget</div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.8 }}>
            <div style={{ color: 'var(--status-info)' }}>{inBudget.length} in budget</div>
            <div>{outOfBudget.length} below threshold</div>
          </div>
        </div>
      </div>

      {/* Canvas */}
      <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        {loading ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
            <Spinner size={24} />
          </div>
        ) : error ? (
          <div style={{ padding: 'var(--sp-5)', color: 'var(--status-danger)', fontFamily: 'var(--font-mono)', fontSize: 12 }}>
            {error} <Button variant="ghost" onClick={() => load(query, threshold)}>Retry</Button>
          </div>
        ) : proximityNodes.length === 0 ? (
          <EmptyState title="Select a context to map" description="Enter a query and click Map to visualize semantic proximity." />
        ) : (
          <LSMCanvas
            nodes={proximityNodes}
            threshold={threshold}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        )}
      </div>

      {/* Right: ranked list + budget readout */}
      <div style={{
        width: 240, flexShrink: 0, borderLeft: 'var(--border)',
        background: 'var(--surface)', display: 'flex', flexDirection: 'column', overflow: 'hidden'
      }}>
        <div style={{ padding: 'var(--sp-2) var(--sp-3)', borderBottom: 'var(--border)', flexShrink: 0 }}>
          <span className="label">Context Ranking</span>
        </div>

        {/* Budget readout */}
        <div style={{ padding: 'var(--sp-3)', borderBottom: 'var(--border)', flexShrink: 0, display: 'flex', gap: 'var(--sp-3)' }}>
          <MetricReadout label="Savings" value={`${savings}%`} />
          <MetricReadout label="In Budget" value={inBudget.length} unit=" nodes" />
        </div>

        {/* Ranked list — accessible representation */}
        <div style={{ flex: 1, overflow: 'auto' }} role="list" aria-label="Nodes ranked by proximity">
          {proximityNodes
            .slice()
            .sort((a, b) => (b.proximity_score ?? 0) - (a.proximity_score ?? 0))
            .map((n) => {
              const score = n.proximity_score ?? 0;
              const inB = score >= threshold;
              const label = n.snippet.split(' ').slice(1).join(' ').slice(0, 40) || n.node_id.slice(0, 16);
              return (
                <div
                  key={n.node_id}
                  onClick={() => setSelectedId(n.node_id === selectedId ? null : n.node_id)}
                  style={{
                    padding: 'var(--sp-2) var(--sp-3)',
                    borderBottom: 'var(--border)',
                    cursor: 'pointer',
                    background: n.node_id === selectedId ? 'var(--raised)' : 'transparent',
                    display: 'flex', flexDirection: 'column', gap: 4,
                  }}
                  role="listitem"
                  tabIndex={0}
                  onKeyDown={(e) => e.key === 'Enter' && setSelectedId(n.node_id)}
                  aria-label={`${n.node_type}: ${label}, score ${score.toFixed(2)}, ${inB ? 'in budget' : 'below threshold'}`}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ fontSize: 11, color: inB ? 'var(--text)' : 'var(--text-muted)' }} className="truncate">
                      {label}
                    </span>
                    <span className="mono" style={{ fontSize: 10, color: inB ? 'var(--status-info)' : 'var(--text-muted)', flexShrink: 0, marginLeft: 4 }}>
                      {score.toFixed(2)}
                    </span>
                  </div>
                  {/* Score bar */}
                  <div style={{ height: 2, background: 'var(--line)', width: '100%' }}>
                    <div style={{ height: '100%', width: `${score * 100}%`, background: inB ? 'var(--status-info)' : 'var(--line)' }} />
                  </div>
                </div>
              );
            })}
        </div>
      </div>
    </div>
  );
};
