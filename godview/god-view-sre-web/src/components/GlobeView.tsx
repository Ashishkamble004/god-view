import {
  ChevronDown,
  ChevronUp,
  Grid,
  Layers,
  Network,
  Search,
  Workflow,
  X,
} from 'lucide-react';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { cockpitApi } from '../api/client';
import type {
  ApprovalCard,
  CostAndDriftOverview,
  CourtAgent,
  EpisodicIncident,
  FullBusinessContext,
  PerceptionReport,
  TopologyEdge,
  TopologyNode,
  TopologyResponse,
  TopologyTier,
} from '../api/types';
import { Badge, Button, Card, formatCurrency } from './ui';

export interface GlobeViewProps {
  initialForce2D?: boolean;
  topology: TopologyResponse | null;
  selectedNodeId?: string | null;
  selectedJourneyName?: string | null;
  court?: CourtAgent[] | null;
  incidents?: EpisodicIncident[] | null;
  approvals?: ApprovalCard[] | null;
  perceive?: PerceptionReport | null;
  cost?: CostAndDriftOverview | null;
  streamStatus?: 'connected' | 'reconnecting' | 'polling' | 'disconnected';
  onSelectNode?: (nodeId: string) => void;
  onSelectIncident?: (incidentId: string) => void;
  onSelectJourney?: (journeyName: string) => void;
  onSelectAgent?: (agentId: string) => void;
}

type ViewMode = 'mesh' | 'domains' | 'matrix';
type StatusFilter = 'all' | 'connected' | 'red' | 'green';

const TIER_ORDER: TopologyTier[] = [
  'edge',
  'compute',
  'messaging',
  'data',
  'platform',
  'observer',
  'external',
];

const TIER_META: Record<
  TopologyTier,
  { label: string; subtitle: string; accent: string; bg: string; border: string }
> = {
  edge: {
    label: 'Edge & Ingress Security',
    subtitle: 'Load Balancers, Backend Services, Cloud Armor WAF, API Gateways',
    accent: 'var(--md-sys-color-primary)',
    bg: 'var(--md-sys-color-surface-container-low)',
    border: 'var(--md-sys-color-outline-variant)',
  },
  compute: {
    label: 'Compute & Microservices',
    subtitle: 'Cloud Run Services, GKE Workloads, Compute Engine VMs',
    accent: 'var(--md-sys-color-secondary)',
    bg: 'var(--md-sys-color-surface-container-low)',
    border: 'var(--md-sys-color-outline-variant)',
  },
  messaging: {
    label: 'Event Bus & Messaging Mesh',
    subtitle: 'Pub/Sub Topics, Push/Pull Subscriptions, Event Routers',
    accent: 'var(--gv-color-warning)',
    bg: 'var(--md-sys-color-surface-container-low)',
    border: 'var(--md-sys-color-outline-variant)',
  },
  data: {
    label: 'Stateful Data & Ledger Stores',
    subtitle: 'Cloud SQL, Firestore Databases, BigQuery Datasets, Cloud Storage',
    accent: 'var(--gv-color-healthy)',
    bg: 'var(--md-sys-color-surface-container-low)',
    border: 'var(--md-sys-color-outline-variant)',
  },
  platform: {
    label: 'AI & Platform Runtime',
    subtitle: 'Vertex AI Reasoning Engines, Specialist Agents, Secret Manager',
    accent: 'var(--md-sys-color-tertiary)',
    bg: 'var(--md-sys-color-surface-container-low)',
    border: 'var(--md-sys-color-outline-variant)',
  },
  observer: {
    label: 'Sovereign Observer Control Plane',
    subtitle: 'Self-excluded God-View Sovereign & Cockpit infrastructure (I-05)',
    accent: 'var(--md-sys-color-on-surface-variant)',
    bg: 'var(--md-sys-color-surface-container-low)',
    border: 'var(--md-sys-color-outline-variant)',
  },
  external: {
    label: 'External Partner Endpoints',
    subtitle: 'Outbound third-party APIs & external dependencies',
    accent: 'var(--gv-color-warning-text)',
    bg: 'var(--md-sys-color-surface-container-low)',
    border: 'var(--md-sys-color-outline-variant)',
  },
};

function statusDotColor(status: TopologyNode['status']): string {
  if (status === 'red') return 'var(--gv-color-incident)';
  if (status === 'amber') return 'var(--gv-color-warning)';
  if (status === 'green') return 'var(--gv-color-healthy)';
  return 'var(--gv-color-healthy)';
}

function statusBadgeVariant(
  status: TopologyNode['status']
): 'danger' | 'warning' | 'success' | 'outline' {
  if (status === 'red') return 'danger';
  if (status === 'amber') return 'warning';
  return 'success';
}

function formatKindLabel(kind: string): string {
  return (kind || 'service').replace(/_/g, ' ');
}

function shortNodeName(raw: string): string {
  if (!raw) return '';
  if (raw.includes('__') && raw.includes('.')) {
    return raw.split('.', 2)[1] || raw;
  }
  return raw;
}

export const GlobeView: React.FC<GlobeViewProps> = ({
  initialForce2D = false,
  topology,
  selectedNodeId,
  selectedJourneyName,
  court: _court,
  incidents: _incidents,
  approvals: _approvals,
  perceive,
  cost,
  streamStatus = 'connected',
  onSelectNode,
  onSelectIncident: _onSelectIncident,
  onSelectJourney,
  onSelectAgent,
}) => {
  const [viewMode, setViewMode] = useState<ViewMode>(
    initialForce2D ? 'matrix' : 'mesh'
  );
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [kindFilter, setKindFilter] = useState<string>('all');
  const [activeNodeId, setActiveNodeId] = useState<string | null>(
    selectedNodeId ?? null
  );
  const [activeJourney, setActiveJourney] = useState<string | null>(
    selectedJourneyName ?? null
  );
  const [expandedTiers, setExpandedTiers] = useState<Record<string, boolean>>({});
  const [businessContext, setBusinessContext] =
    useState<FullBusinessContext | null>(null);
  const [loadingContext, setLoadingContext] = useState(false);

  const meshContainerRef = useRef<HTMLDivElement | null>(null);
  const nodeRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const [svgLines, setSvgLines] = useState<
    Array<{
      id: string;
      x1: number;
      y1: number;
      x2: number;
      y2: number;
      kind: string;
      highlighted: boolean;
      hasError: boolean;
    }>
  >([]);

  useEffect(() => {
    if (selectedNodeId !== undefined) {
      setActiveNodeId(selectedNodeId);
    }
  }, [selectedNodeId]);

  useEffect(() => {
    if (selectedJourneyName !== undefined) {
      setActiveJourney(selectedJourneyName);
    }
  }, [selectedJourneyName]);

  const nodes = useMemo(() => topology?.nodes ?? [], [topology]);
  const edges = useMemo(() => topology?.edges ?? [], [topology]);
  const journeys = useMemo(() => topology?.journeys ?? [], [topology]);

  // Set of node IDs that participate in at least one discovered edge or journey or open incident
  const connectedNodeIds = useMemo(() => {
    const s = new Set<string>();
    for (const e of edges) {
      s.add(e.src);
      s.add(e.dst);
    }
    for (const j of journeys) {
      for (const st of j.steps || []) {
        s.add(st.node);
      }
    }
    for (const n of nodes) {
      if (n.incidents_open > 0 || n.tier === 'edge' || n.tier === 'observer') {
        s.add(n.id);
      }
    }
    return s;
  }, [edges, journeys, nodes]);

  // Journey step lookup map: node_id -> 1-based step index
  const journeyStepMap = useMemo(() => {
    const map = new Map<string, { stepIndex: number; operation: string }>();
    if (!activeJourney) return map;
    const found = journeys.find((j) => j.name === activeJourney);
    if (!found) return map;
    found.steps.forEach((st, idx) => {
      map.set(st.node, { stepIndex: idx + 1, operation: st.operation });
      const byShort = nodes.find((n) => n.name === st.node);
      if (byShort) {
        map.set(byShort.id, { stepIndex: idx + 1, operation: st.operation });
      }
    });
    return map;
  }, [activeJourney, journeys, nodes]);

  // Distinct resource kinds for quick filter pills
  const kindCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const n of nodes) {
      counts[n.kind] = (counts[n.kind] || 0) + 1;
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  }, [nodes]);

  // Filtered nodes
  const filteredNodes = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return nodes.filter((n) => {
      if (statusFilter === 'connected' && !connectedNodeIds.has(n.id)) {
        return false;
      }
      if (statusFilter === 'red' && n.status !== 'red' && n.incidents_open === 0) {
        return false;
      }
      if (statusFilter === 'green' && n.status === 'red') {
        return false;
      }
      if (kindFilter !== 'all' && n.kind !== kindFilter) {
        return false;
      }
      if (q) {
        const hay = `${n.name} ${n.kind} ${n.tier} ${n.business?.domain || ''} ${
          n.business?.role_summary || ''
        }`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [nodes, searchQuery, statusFilter, kindFilter, connectedNodeIds]);

  // Group filtered nodes by tier
  const nodesByTier = useMemo(() => {
    const map: Record<TopologyTier, TopologyNode[]> = {
      edge: [],
      compute: [],
      messaging: [],
      data: [],
      platform: [],
      observer: [],
      external: [],
    };
    for (const n of filteredNodes) {
      const t = map[n.tier] ? n.tier : 'compute';
      map[t].push(n);
    }
    // Sort within each tier: incidents first, then connected nodes, then alphabetical
    for (const t of TIER_ORDER) {
      map[t].sort((a, b) => {
        if (b.incidents_open !== a.incidents_open) {
          return b.incidents_open - a.incidents_open;
        }
        const aConn = connectedNodeIds.has(a.id) ? 1 : 0;
        const bConn = connectedNodeIds.has(b.id) ? 1 : 0;
        if (bConn !== aConn) return bConn - aConn;
        return a.name.localeCompare(b.name);
      });
    }
    return map;
  }, [filteredNodes, connectedNodeIds]);

  // Group filtered nodes by autonomous domain & resource family for 'domains' view
  const domainClusters = useMemo(() => {
    const clusters: Record<
      string,
      {
        domain: string;
        nodes: TopologyNode[];
        healthyCount: number;
        incidentCount: number;
        kinds: Set<string>;
      }
    > = {};

    for (const n of filteredNodes) {
      const dom =
        n.business?.domain && n.business.domain.trim()
          ? n.business.domain.trim()
          : TIER_META[n.tier]?.label || 'Platform Services';
      if (!clusters[dom]) {
        clusters[dom] = {
          domain: dom,
          nodes: [],
          healthyCount: 0,
          incidentCount: 0,
          kinds: new Set<string>(),
        };
      }
      clusters[dom].nodes.push(n);
      clusters[dom].kinds.add(n.kind);
      if (n.status === 'red' || n.incidents_open > 0) {
        clusters[dom].incidentCount += 1;
      } else {
        clusters[dom].healthyCount += 1;
      }
    }
    return Object.values(clusters).sort(
      (a, b) => b.incidentCount - a.incidentCount || b.nodes.length - a.nodes.length
    );
  }, [filteredNodes]);

  const selectedNode = useMemo(
    () => nodes.find((n) => n.id === activeNodeId || n.name === activeNodeId) ?? null,
    [nodes, activeNodeId]
  );

  // Fetch rich BusinessContext when a node is selected
  useEffect(() => {
    if (!selectedNode) {
      setBusinessContext(null);
      return;
    }
    let cancelled = false;
    setLoadingContext(true);
    cockpitApi
      .getBusinessContext(selectedNode.id)
      .then((ctx) => {
        if (!cancelled) setBusinessContext(ctx);
      })
      .catch(() => {
        if (!cancelled) setBusinessContext(null);
      })
      .finally(() => {
        if (!cancelled) setLoadingContext(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedNode]);

  // Compute SVG dependency lines in 'mesh' view
  useEffect(() => {
    if (viewMode !== 'mesh') return;
    const computeLines = () => {
      const container = meshContainerRef.current;
      if (!container) return;
      const cRect = container.getBoundingClientRect();
      const nextLines: typeof svgLines = [];
      for (const e of edges) {
        const srcEl = nodeRefs.current[e.src];
        const dstEl = nodeRefs.current[e.dst];
        if (!srcEl || !dstEl) continue;
        const sRect = srcEl.getBoundingClientRect();
        const dRect = dstEl.getBoundingClientRect();
        if (sRect.width === 0 || dRect.width === 0) continue;

        const x1 = sRect.left + sRect.width / 2 - cRect.left;
        const y1 = sRect.bottom - cRect.top;
        const x2 = dRect.left + dRect.width / 2 - cRect.left;
        const y2 = dRect.top - cRect.top;

        const isSelectedEdge =
          activeNodeId === e.src ||
          activeNodeId === e.dst ||
          (journeyStepMap.has(e.src) && journeyStepMap.has(e.dst));

        nextLines.push({
          id: e.id,
          x1,
          y1,
          x2,
          y2,
          kind: e.kind,
          highlighted: isSelectedEdge,
          hasError: (e.error_ratio ?? 0) > 0.01,
        });
      }
      setSvgLines(nextLines);
    };

    const timer = setTimeout(computeLines, 60);
    window.addEventListener('resize', computeLines);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('resize', computeLines);
    };
  }, [
    viewMode,
    edges,
    filteredNodes,
    expandedTiers,
    activeNodeId,
    journeyStepMap,
  ]);

  const handleSelectNode = (node: TopologyNode) => {
    const nextId = activeNodeId === node.id ? null : node.id;
    setActiveNodeId(nextId);
    if (nextId && onSelectNode) {
      onSelectNode(nextId);
    }
  };

  // Summary metrics
  const totalNodes = nodes.length;
  const redNodesCount = nodes.filter(
    (n) => n.status === 'red' || n.incidents_open > 0
  ).length;
  const healthyNodesCount = totalNodes - redNodesCount;

  // Connected edges for selected node
  const selectedNodeEdges = useMemo(() => {
    if (!selectedNode) return { inbound: [] as TopologyEdge[], outbound: [] as TopologyEdge[] };
    return {
      inbound: edges.filter(
        (e) => e.dst === selectedNode.id || e.dst === selectedNode.name
      ),
      outbound: edges.filter(
        (e) => e.src === selectedNode.id || e.src === selectedNode.name
      ),
    };
  }, [selectedNode, edges]);

  return (
    <div className="space-y-4">
      {/* Top Executive Telemetry & Mode Switcher Bar */}
      <Card className="border-outline-variant bg-surface p-4 shadow-none">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-md bg-primary-container text-on-primary-container">
              <Network className="h-5 w-5" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="font-title text-base font-medium tracking-tight text-on-surface">
                  Autonomous Estate Topology &amp; Service Mesh
                </h2>
                <Badge variant="info">
                  {topology?.env_id || perceive?.env_id || 'live-estate'}
                </Badge>
                <Badge
                  variant={streamStatus === 'connected' ? 'success' : 'outline'}
                  dot
                >
                  {streamStatus === 'connected'
                    ? '6 Senses Live'
                    : 'Polling Sync'}
                </Badge>
              </div>
              <p className="text-xs text-on-surface-variant">
                100% self-discovered cloud resources, call-graph dependencies,
                inferred business roles, and specialist agent assignments
              </p>
            </div>
          </div>

          {/* KPI Strip */}
          <div className="flex flex-wrap items-center gap-3">
            <div className="rounded-sm border border-outline-variant bg-surface-container-low px-3 py-1.5 text-xs">
              <span className="text-on-surface-variant">Discovered Resources:</span>{' '}
              <strong className="font-mono font-medium text-on-surface num-tabular">
                {totalNodes}
              </strong>
            </div>
            <div className="rounded-sm border border-outline-variant bg-healthy-container px-3 py-1.5 text-xs">
              <span className="text-on-healthy-container">Healthy:</span>{' '}
              <strong className="font-mono font-medium text-on-healthy-container num-tabular">
                {healthyNodesCount}
              </strong>
            </div>
            <div className="rounded-sm border border-outline-variant bg-incident-container px-3 py-1.5 text-xs">
              <span className="text-on-incident-container">Incident Nodes:</span>{' '}
              <strong className="font-mono font-medium text-on-incident-container num-tabular">
                {redNodesCount}
              </strong>
            </div>
            <div className="rounded-sm border border-outline-variant bg-primary-container px-3 py-1.5 text-xs">
              <span className="text-on-primary-container">Verified Edges:</span>{' '}
              <strong className="font-mono font-medium text-on-primary-container num-tabular">
                {edges.length}
              </strong>
            </div>
            {cost && (
              <div className="rounded-sm border border-outline-variant bg-surface-container-low px-3 py-1.5 text-xs">
                <span className="text-on-surface-variant">Court Spend:</span>{' '}
                <strong className="font-mono font-medium text-on-surface num-tabular">
                  {formatCurrency(cost.daily_court_spent_usd ?? 0)} /{' '}
                  {formatCurrency(cost.daily_court_ceiling_usd ?? 5)}
                </strong>
              </div>
            )}
          </div>
        </div>

        {/* Filter, Journey Highlighter, and View Mode Toolbar */}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-outline-variant pt-3">
          {/* Search + Status + Kind Filters */}
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-[230px]">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-on-surface-variant" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search 115+ services, kinds, domains..."
                className="w-full rounded-full border border-outline-variant bg-surface-container-low py-1.5 pl-8 pr-7 text-xs text-on-surface placeholder:text-on-surface-variant focus:border-primary focus:bg-surface focus:outline-none focus:ring-1 focus:ring-primary"
              />
              {searchQuery && (
                <button
                  type="button"
                  aria-label="Clear search query"
                  onClick={() => setSearchQuery('')}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-on-surface-variant hover:text-on-surface"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>

            <div className="inline-flex rounded-full border border-outline-variant bg-surface-container-low p-0.5 text-xs">
              {(
                [
                  { id: 'all', label: `All (${totalNodes})` },
                  {
                    id: 'connected',
                    label: `Active Mesh (${connectedNodeIds.size})`,
                  },
                  { id: 'red', label: `Incidents (${redNodesCount})` },
                  { id: 'green', label: `Healthy (${healthyNodesCount})` },
                ] as const
              ).map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setStatusFilter(tab.id)}
                  className={`rounded-full px-3 py-1 font-label font-medium transition ${
                    statusFilter === tab.id
                      ? 'bg-secondary-container text-on-secondary-container'
                      : 'text-on-surface-variant hover:text-on-surface'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            <select
              aria-label="Filter by resource kind"
              value={kindFilter}
              onChange={(e) => setKindFilter(e.target.value)}
              className="rounded-full border border-outline-variant bg-surface px-3 py-1.5 font-label text-xs font-medium text-on-surface focus:border-primary focus:outline-none"
            >
              <option value="all">All Resource Kinds ({kindCounts.length})</option>
              {kindCounts.map(([k, count]) => (
                <option key={k} value={k}>
                  {formatKindLabel(k)} ({count})
                </option>
              ))}
            </select>

            {journeys.length > 0 && (
              <select
                aria-label="Highlight Business Journey Path"
                value={activeJourney ?? ''}
                onChange={(e) => {
                  const val = e.target.value || null;
                  setActiveJourney(val);
                  if (val && onSelectJourney) onSelectJourney(val);
                }}
                className="rounded-full border border-outline-variant bg-primary-container px-3 py-1.5 font-label text-xs font-medium text-on-primary-container focus:border-primary focus:outline-none"
              >
                <option value="">Highlight Journey Flow ({journeys.length})...</option>
                {journeys.map((j) => (
                  <option key={j.name} value={j.name}>
                    Journey: {j.name} ({j.steps.length} steps)
                  </option>
                ))}
              </select>
            )}
          </div>

          {/* 3 View Modes */}
          <div className="inline-flex items-center rounded-full border border-outline-variant bg-surface-container-low p-0.5 text-xs">
            <button
              type="button"
              onClick={() => setViewMode('mesh')}
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 font-label font-medium transition ${
                viewMode === 'mesh'
                  ? 'bg-secondary-container text-on-secondary-container'
                  : 'text-on-surface-variant hover:text-on-surface'
              }`}
            >
              <Workflow className="h-3.5 w-3.5" />
              Service Mesh DAG
            </button>
            <button
              type="button"
              onClick={() => setViewMode('domains')}
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 font-label font-medium transition ${
                viewMode === 'domains'
                  ? 'bg-secondary-container text-on-secondary-container'
                  : 'text-on-surface-variant hover:text-on-surface'
              }`}
            >
              <Layers className="h-3.5 w-3.5" />
              Domain Clusters ({domainClusters.length})
            </button>
            <button
              type="button"
              onClick={() => setViewMode('matrix')}
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 font-label font-medium transition ${
                viewMode === 'matrix'
                  ? 'bg-secondary-container text-on-secondary-container'
                  : 'text-on-surface-variant hover:text-on-surface'
              }`}
            >
              <Grid className="h-3.5 w-3.5" />
              Full Estate Matrix ({filteredNodes.length})
            </button>
          </div>
        </div>
      </Card>

      {/* Main Content Split: Canvas on Left, Node Inspector Drawer on Right when active */}
      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-12">
        <div className={selectedNode ? 'xl:col-span-8' : 'xl:col-span-12'}>
          {/* VIEW 1: SERVICE MESH & TIER TOPOLOGY DAG */}
          {viewMode === 'mesh' && (
            <div
              ref={meshContainerRef}
              className="relative space-y-4 rounded-xl border border-outline-variant bg-surface p-5 shadow-none"
            >
              {/* SVG Dependency Overlay */}
              <svg
                className="pointer-events-none absolute inset-0 h-full w-full overflow-visible"
                style={{ zIndex: 1 }}
              >
                <defs>
                  <marker
                    id="edge-arrow-normal"
                    viewBox="0 0 10 10"
                    refX="7"
                    refY="5"
                    markerWidth="5"
                    markerHeight="5"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 1 L 10 5 L 0 9 z" fill="var(--md-sys-color-outline)" />
                  </marker>
                  <marker
                    id="edge-arrow-active"
                    viewBox="0 0 10 10"
                    refX="7"
                    refY="5"
                    markerWidth="6"
                    markerHeight="6"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 1 L 10 5 L 0 9 z" fill="var(--md-sys-color-primary)" />
                  </marker>
                </defs>
                {svgLines.map((ln) => {
                  const dy = Math.max(28, Math.abs(ln.y2 - ln.y1) * 0.45);
                  const d = `M ${ln.x1} ${ln.y1} C ${ln.x1} ${
                    ln.y1 + dy
                  }, ${ln.x2} ${ln.y2 - dy}, ${ln.x2} ${ln.y2}`;
                  return (
                    <path
                      key={ln.id}
                      d={d}
                      fill="none"
                      stroke={
                        ln.hasError
                          ? 'var(--gv-color-incident)'
                          : ln.highlighted
                          ? 'var(--md-sys-color-primary)'
                          : 'var(--md-sys-color-outline-variant)'
                      }
                      strokeWidth={ln.highlighted ? 2.2 : 1.25}
                      strokeDasharray={ln.highlighted ? undefined : '4 3'}
                      opacity={ln.highlighted ? 0.95 : 0.65}
                      markerEnd={
                        ln.highlighted
                          ? 'url(#edge-arrow-active)'
                          : 'url(#edge-arrow-normal)'
                      }
                    />
                  );
                })}
              </svg>

              {/* Architectural Tier Lanes */}
              <div className="relative space-y-4" style={{ zIndex: 2 }}>
                {TIER_ORDER.map((tier) => {
                  const tierNodes = nodesByTier[tier] || [];
                  if (tierNodes.length === 0) return null;
                  const meta = TIER_META[tier];
                  const isExpanded = Boolean(expandedTiers[tier]);
                  const defaultCap = statusFilter === 'all' && !searchQuery ? 10 : 50;
                  const visibleNodes = isExpanded
                    ? tierNodes
                    : tierNodes.slice(0, defaultCap);
                  const hiddenCount = tierNodes.length - visibleNodes.length;

                  return (
                    <div
                      key={tier}
                      className="rounded-lg border p-3.5 transition"
                      style={{
                        backgroundColor: meta.bg,
                        borderColor: meta.border,
                      }}
                    >
                      <div className="mb-2.5 flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span
                            className="h-2.5 w-2.5 rounded-full"
                            style={{ backgroundColor: meta.accent }}
                          />
                          <h3 className="font-title text-xs font-medium tracking-wide text-on-surface">
                            {meta.label}
                          </h3>
                          <span className="rounded-full border border-outline-variant bg-surface px-2 py-0.5 font-mono text-[11px] font-medium text-on-surface-variant num-tabular">
                            {tierNodes.length} resources
                          </span>
                          <span className="hidden text-xs text-on-surface-variant md:inline">
                            · {meta.subtitle}
                          </span>
                        </div>

                        {tierNodes.length > defaultCap && (
                          <button
                            type="button"
                            onClick={() =>
                              setExpandedTiers((prev) => ({
                                ...prev,
                                [tier]: !prev[tier],
                              }))
                            }
                            className="inline-flex items-center gap-1 rounded-full border border-outline-variant bg-surface px-3 py-1 font-label text-xs font-medium text-primary hover:bg-primary-container hover:text-on-primary-container"
                          >
                            {isExpanded ? (
                              <>
                                <ChevronUp className="h-3.5 w-3.5" />
                                Show Compact ({defaultCap})
                              </>
                            ) : (
                              <>
                                <ChevronDown className="h-3.5 w-3.5" />
                                Expand All {tierNodes.length} Resources (+
                                {hiddenCount} more)
                              </>
                            )}
                          </button>
                        )}
                      </div>

                      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
                        {visibleNodes.map((node) => {
                          const isSelected = activeNodeId === node.id;
                          const jStep = journeyStepMap.get(node.id);
                          const isConnected = connectedNodeIds.has(node.id);

                          return (
                            <button
                              key={node.id}
                              ref={(el) => {
                                nodeRefs.current[node.id] = el;
                              }}
                              type="button"
                              onClick={() => handleSelectNode(node)}
                              className={`group relative flex flex-col justify-between rounded-md border bg-surface p-2.5 text-left transition ${
                                isSelected
                                  ? 'border-primary ring-2 ring-primary'
                                  : jStep
                                  ? 'border-primary bg-primary-container/30 ring-1 ring-primary'
                                  : node.incidents_open > 0 || node.status === 'red'
                                  ? 'border-incident bg-incident-container/40'
                                  : 'border-outline-variant hover:border-primary'
                              }`}
                            >
                              <div>
                                <div className="flex items-start justify-between gap-1.5">
                                  <div className="flex items-center gap-1.5 min-w-0">
                                    <span
                                      className="h-2 w-2 shrink-0 rounded-full"
                                      style={{
                                        backgroundColor: statusDotColor(
                                          node.status
                                        ),
                                      }}
                                    />
                                    <span
                                      title={node.name}
                                      className="truncate font-mono text-xs font-medium text-on-surface group-hover:text-primary"
                                    >
                                      {node.name}
                                    </span>
                                  </div>
                                  {jStep && (
                                    <span className="shrink-0 rounded-full bg-primary px-2 py-0.5 font-mono text-[10px] font-medium text-on-primary">
                                      Step #{jStep.stepIndex}
                                    </span>
                                  )}
                                </div>

                                <div className="mt-1 flex flex-wrap items-center gap-1">
                                  <span className="rounded-full bg-surface-container px-2 py-0.5 font-mono text-[10px] font-medium text-on-surface-variant">
                                    {formatKindLabel(node.kind)}
                                  </span>
                                  {isConnected && (
                                    <span className="rounded-full bg-primary-container px-2 py-0.5 text-[10px] font-medium text-on-primary-container">
                                      mesh-linked
                                    </span>
                                  )}
                                  {node.incidents_open > 0 && (
                                    <span className="rounded-full bg-incident-container px-2 py-0.5 font-mono text-[10px] font-medium text-on-incident-container">
                                      {node.incidents_open} inc
                                    </span>
                                  )}
                                </div>

                                {node.business?.role_summary && (
                                  <p className="mt-1.5 line-clamp-2 text-[11px] leading-snug text-on-surface-variant">
                                    {node.business.role_summary}
                                  </p>
                                )}
                              </div>

                              <div className="mt-2 flex items-center justify-between border-t border-outline-variant pt-1.5 font-mono text-[10px] text-on-surface-variant num-tabular">
                                <span>
                                  {node.golden?.rps != null
                                    ? `${node.golden.rps.toFixed(1)} rps`
                                    : node.business?.domain || node.tier}
                                </span>
                                <span>
                                  {node.golden?.p95_ms != null
                                    ? `p95 ${Math.round(node.golden.p95_ms)}ms`
                                    : 'active'}
                                </span>
                              </div>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* VIEW 2: AUTONOMOUS DOMAIN & RESOURCE CLUSTERS */}
          {viewMode === 'domains' && (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              {domainClusters.map((cluster) => (
                <Card
                  key={cluster.domain}
                  className="flex flex-col justify-between border-outline-variant bg-surface p-4 shadow-none"
                >
                  <div>
                    <div className="flex items-start justify-between gap-2 border-b border-outline-variant pb-3">
                      <div>
                        <h3 className="font-title text-sm font-medium text-on-surface">
                          {cluster.domain}
                        </h3>
                        <p className="mt-0.5 text-xs text-on-surface-variant">
                          Kinds:{' '}
                          {Array.from(cluster.kinds)
                            .map(formatKindLabel)
                            .join(', ')}
                        </p>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <Badge variant="success">
                          {cluster.healthyCount} healthy
                        </Badge>
                        {cluster.incidentCount > 0 && (
                          <Badge variant="danger">
                            {cluster.incidentCount} alert
                          </Badge>
                        )}
                      </div>
                    </div>

                    <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
                      {cluster.nodes.map((node) => (
                        <button
                          key={node.id}
                          type="button"
                          onClick={() => handleSelectNode(node)}
                          className={`flex items-center justify-between rounded-sm border px-2.5 py-2 text-left text-xs transition ${
                            activeNodeId === node.id
                              ? 'border-primary bg-primary-container font-medium text-on-primary-container'
                              : 'border-outline-variant bg-surface-container-low hover:border-primary hover:bg-surface'
                          }`}
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            <span
                              className="h-2 w-2 shrink-0 rounded-full"
                              style={{
                                backgroundColor: statusDotColor(node.status),
                              }}
                            />
                            <span className="truncate font-mono text-on-surface">
                              {node.name}
                            </span>
                          </div>
                          <span className="ml-2 shrink-0 rounded-full bg-surface px-2 py-0.5 font-mono text-[10px] text-on-surface-variant">
                            {formatKindLabel(node.kind)}
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                </Card>
              ))}
            </div>
          )}

          {/* VIEW 3: FULL ESTATE MATRIX (ALL 115+ NODES) */}
          {viewMode === 'matrix' && (
            <Card className="overflow-hidden border-outline-variant bg-surface shadow-none">
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-left text-xs">
                  <thead>
                    <tr className="border-b border-outline-variant bg-surface-container-low font-label text-[11px] font-medium text-on-surface-variant">
                      <th className="px-3 py-2.5">Resource Name</th>
                      <th className="px-3 py-2.5">Tier &amp; Kind</th>
                      <th className="px-3 py-2.5">Status</th>
                      <th className="px-3 py-2.5">Inferred Domain &amp; Role</th>
                      <th className="px-3 py-2.5">Golden Signals</th>
                      <th className="px-3 py-2.5">Specialist Agent</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-outline-variant">
                    {filteredNodes.map((node) => (
                      <tr
                        key={node.id}
                        onClick={() => handleSelectNode(node)}
                        className={`cursor-pointer transition hover:bg-surface-container-low ${
                          activeNodeId === node.id ? 'bg-secondary-container/40' : ''
                        }`}
                      >
                        <td className="px-3 py-2.5 font-mono font-medium text-on-surface">
                          <div className="flex items-center gap-2">
                            <span
                              className="h-2 w-2 rounded-full"
                              style={{
                                backgroundColor: statusDotColor(node.status),
                              }}
                            />
                            {node.name}
                          </div>
                        </td>
                        <td className="px-3 py-2.5">
                          <span className="rounded-full bg-surface-container px-2 py-0.5 font-mono text-[11px] text-on-surface-variant">
                            {node.tier} · {formatKindLabel(node.kind)}
                          </span>
                        </td>
                        <td className="px-3 py-2.5">
                          <Badge variant={statusBadgeVariant(node.status)}>
                            {node.incidents_open > 0
                              ? `${node.incidents_open} incident`
                              : node.status === 'red'
                              ? 'degraded'
                              : 'healthy'}
                          </Badge>
                        </td>
                        <td className="max-w-md px-3 py-2.5 text-on-surface">
                          <div className="font-medium text-on-surface">
                            {node.business?.domain || node.tier}
                          </div>
                          <div className="truncate text-[11px] text-on-surface-variant">
                            {node.business?.role_summary}
                          </div>
                        </td>
                        <td className="px-3 py-2.5 font-mono text-[11px] text-on-surface-variant num-tabular">
                          {node.golden?.rps != null
                            ? `${node.golden.rps.toFixed(1)} rps · ${Math.round(
                                node.golden.p95_ms ?? 0
                              )}ms`
                            : '0 tokens at rest'}
                        </td>
                        <td className="px-3 py-2.5 font-mono text-[11px] text-primary">
                          {node.agent_id || 'sovereign-observer'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </div>

        {/* Slide-Over Node Inspector Drawer */}
        {selectedNode && (
          <Card className="sticky top-4 rounded-xl border-outline-variant bg-surface p-5 shadow-elev-2 xl:col-span-4">
            <div className="flex items-start justify-between gap-2 border-b border-outline-variant pb-3">
              <div>
                <div className="flex items-center gap-2">
                  <span
                    className="h-2.5 w-2.5 rounded-full"
                    style={{
                      backgroundColor: statusDotColor(selectedNode.status),
                    }}
                  />
                  <h3 className="font-mono text-sm font-medium text-on-surface">
                    {selectedNode.name}
                  </h3>
                </div>
                <p className="mt-0.5 font-mono text-[11px] text-on-surface-variant">
                  {selectedNode.id}
                </p>
              </div>
              <button
                type="button"
                aria-label="Close Node Inspector"
                onClick={() => setActiveNodeId(null)}
                className="rounded-full p-1.5 text-on-surface-variant hover:bg-surface-container-low hover:text-on-surface"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="mt-3 space-y-4 text-xs">
              {/* Badges */}
              <div className="flex flex-wrap gap-1.5">
                <Badge variant="info">{selectedNode.tier} tier</Badge>
                <Badge variant="outline">
                  {formatKindLabel(selectedNode.kind)}
                </Badge>
                <Badge variant={statusBadgeVariant(selectedNode.status)}>
                  {selectedNode.status === 'red' ? 'Incident Active' : 'Healthy'}
                </Badge>
              </div>

              {/* Autonomous Inferred Business Context */}
              <div className="rounded-md border border-outline-variant bg-surface-container-low p-3">
                <div className="flex items-center justify-between">
                  <span className="font-title font-medium text-on-surface">
                    Autonomous Business Context
                  </span>
                  <span className="rounded-full bg-primary-container px-2 py-0.5 font-mono text-[11px] font-medium text-on-primary-container num-tabular">
                    conf{' '}
                    {Math.round(
                      (businessContext?.confidence ??
                        selectedNode.business?.confidence ??
                        0.85) * 100
                    )}
                    %
                  </span>
                </div>
                <p className="mt-1 font-medium text-on-surface">
                  Domain:{' '}
                  {businessContext?.domain ||
                    selectedNode.business?.domain ||
                    selectedNode.tier}
                </p>
                <p className="mt-1 text-on-surface-variant">
                  {loadingContext
                    ? 'Loading inferred business context...'
                    : businessContext?.role_summary ||
                      selectedNode.business?.role_summary ||
                      'Autonomously discovered cloud resource'}
                </p>
              </div>

              {/* Upstream & Downstream Call Graph Edges */}
              <div>
                <h4 className="font-title font-medium text-on-surface">
                  Call-Graph &amp; Dependency Edges (
                  {selectedNodeEdges.inbound.length +
                    selectedNodeEdges.outbound.length}
                  )
                </h4>
                <div className="mt-1.5 space-y-1.5">
                  {selectedNodeEdges.inbound.map((e) => (
                    <div
                      key={e.id}
                      className="flex items-center justify-between rounded-sm border border-outline-variant bg-surface-container-low px-2.5 py-1.5 font-mono text-[11px]"
                    >
                      <span className="truncate text-on-surface">
                        ← {shortNodeName(e.src)}
                      </span>
                      <Badge variant="outline">{e.kind}</Badge>
                    </div>
                  ))}
                  {selectedNodeEdges.outbound.map((e) => (
                    <div
                      key={e.id}
                      className="flex items-center justify-between rounded-sm border border-outline-variant bg-surface-container-low px-2.5 py-1.5 font-mono text-[11px]"
                    >
                      <span className="truncate text-on-surface">
                        → {shortNodeName(e.dst)}
                      </span>
                      <Badge variant="outline">{e.kind}</Badge>
                    </div>
                  ))}
                  {selectedNodeEdges.inbound.length === 0 &&
                    selectedNodeEdges.outbound.length === 0 && (
                      <p className="text-on-surface-variant">
                        Standalone infrastructure resource (monitored via 0-token
                        SenseAdapter)
                      </p>
                    )}
                </div>
              </div>

              {/* Specialist Agent Assignment */}
              {selectedNode.agent_id && (
                <div className="flex items-center justify-between rounded-md border border-outline-variant bg-surface-container-low p-2.5">
                  <div>
                    <div className="text-[11px] text-on-surface-variant">
                      Owning Court Specialist
                    </div>
                    <div className="font-mono font-medium text-on-surface">
                      {selectedNode.agent_id}
                    </div>
                  </div>
                  {onSelectAgent && (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => onSelectAgent(selectedNode.agent_id!)}
                    >
                      Inspect Agent
                    </Button>
                  )}
                </div>
              )}
            </div>
          </Card>
        )}
      </div>
    </div>
  );
};
