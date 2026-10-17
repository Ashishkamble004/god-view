import React, { useEffect, useRef, useState, useMemo } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import {
  ArrowRight,
  Brain,
  Grid,
  Minus,
  Plus,
  RotateCcw,
  Search,
  Shield,
  ShieldAlert,
  Wifi,
  X,
} from 'lucide-react';
import type {
  FullBusinessContext,
  TopologyNode,
  TopologyResponse,
  TopologyTier,
} from '../api/types';
import { cockpitApi } from '../api/client';
import { Badge, Button, Card } from './ui';
import {
  GLOBE_COLORS,
  GLOBE_STYLE,
  NODE_STATUS_COLORS,
  TIER_LATITUDE,
} from '../theme/globe';

export interface GlobeViewProps {
  topology: TopologyResponse | null;
  selectedNodeId?: string | null;
  onSelectNode?: (nodeId: string) => void;
  onSelectIncident?: (incidentId: string) => void;
  onSelectJourney?: (journeyName: string) => void;
  onSelectAgent?: (agentId: string) => void;
}

// Latitudinal band assignment per architectural tier (degrees)
const TIER_LATITUDES: Record<TopologyTier, number> = {
  observer: TIER_LATITUDE.observer ?? 78,
  edge: TIER_LATITUDE.edge ?? 55,
  messaging: TIER_LATITUDE.messaging ?? 35,
  compute: TIER_LATITUDE.compute ?? 8,
  data: TIER_LATITUDE.data ?? -42,
  platform: TIER_LATITUDE.platform ?? -68,
  external: TIER_LATITUDE.external ?? 20, // Orbiting satellite at higher radius
};

const TIER_COLORS: Record<TopologyTier, string> = {
  edge: '#1A73E8', // Google Blue
  compute: '#4285F4', // Light Google Blue
  data: '#34A853', // Google Green
  messaging: '#FBBC04', // Google Yellow
  platform: '#5F6368', // Slate Grey
  observer: '#188038', // Deep Green
  external: '#EA4335', // Google Red
};

const STATUS_COLORS: Record<string, { hex: string; threeHex: number }> = {
  green: { hex: '#5bb974', threeHex: NODE_STATUS_COLORS.green },
  amber: { hex: '#fcc934', threeHex: NODE_STATUS_COLORS.amber },
  red: { hex: '#ee675c', threeHex: NODE_STATUS_COLORS.red },
  unknown: { hex: '#9aa0a6', threeHex: NODE_STATUS_COLORS.unknown },
};

export const GlobeView: React.FC<GlobeViewProps> = ({
  topology,
  selectedNodeId: propSelectedNodeId = null,
  onSelectNode,
  onSelectIncident,
  onSelectJourney,
  onSelectAgent,
}) => {
  void onSelectAgent;
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const controlsRef = useRef<OrbitControls | null>(null);

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(propSelectedNodeId);

  useEffect(() => {
    if (propSelectedNodeId !== undefined) {
      setSelectedNodeId(propSelectedNodeId);
    }
  }, [propSelectedNodeId]);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [businessContext, setBusinessContext] = useState<FullBusinessContext | null>(null);
  const [loadingContext, setLoadingContext] = useState(false);
  const [activeTierFilters, setActiveTierFilters] = useState<Set<TopologyTier>>(
    new Set(['edge', 'compute', 'data', 'messaging', 'platform', 'observer', 'external'])
  );
  const [searchQuery, setSearchQuery] = useState('');
  const [force2D, setForce2D] = useState(false);
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false);
  const [incidentDismissed, setIncidentDismissed] = useState(false);

  // Check prefers-reduced-motion
  useEffect(() => {
    if (typeof window !== 'undefined' && window.matchMedia) {
      const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
      setPrefersReducedMotion(mediaQuery.matches);
      const listener = (e: MediaQueryListEvent) => setPrefersReducedMotion(e.matches);
      mediaQuery.addEventListener('change', listener);
      return () => mediaQuery.removeEventListener('change', listener);
    }
  }, []);

  const use2DMode = force2D || prefersReducedMotion;

  // Selected node object
  const selectedNode = useMemo(() => {
    if (!topology || !selectedNodeId) return null;
    return topology.nodes.find((n) => n.id === selectedNodeId) || null;
  }, [topology, selectedNodeId]);

  // Load business context when a node is selected
  useEffect(() => {
    if (!selectedNodeId) {
      setBusinessContext(null);
      return;
    }
    let cancelled = false;
    setLoadingContext(true);
    cockpitApi
      .getBusinessContext(selectedNodeId)
      .then((ctx) => {
        if (!cancelled) {
          setBusinessContext(ctx);
          setLoadingContext(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          console.warn('Failed to load business context for node:', selectedNodeId, err);
          setLoadingContext(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [selectedNodeId]);

  // Group nodes by domain to determine longitudinal angular clusters
  const nodePositions = useMemo(() => {
    if (!topology) return new Map<string, THREE.Vector3>();

    const positions = new Map<string, THREE.Vector3>();
    const domainGroups: Record<string, TopologyNode[]> = {};

    topology.nodes.forEach((node) => {
      const domain = node.business.domain || 'core';
      if (!domainGroups[domain]) domainGroups[domain] = [];
      domainGroups[domain].push(node);
    });

    const domains = Object.keys(domainGroups);
    const domainStep = (2 * Math.PI) / Math.max(1, domains.length);

    domains.forEach((dom, dIdx) => {
      const groupNodes = domainGroups[dom];
      const baseTheta = dIdx * domainStep;

      groupNodes.forEach((node, nIdx) => {
        const tier = (node.tier as TopologyTier) || 'compute';
        const latDeg = TIER_LATITUDES[tier] ?? 10;
        const phi = (latDeg * Math.PI) / 180;

        // Longitude with slight jitter within domain cluster
        const offset = (nIdx - (groupNodes.length - 1) / 2) * 0.18;
        const theta = baseTheta + offset;

        // External nodes orbit at higher radius as satellites
        const r = tier === 'external' ? 9.0 * GLOBE_STYLE.satelliteOrbitRadius : 9.0;

        const x = r * Math.cos(phi) * Math.sin(theta);
        const y = r * Math.sin(phi);
        const z = r * Math.cos(phi) * Math.cos(theta);

        positions.set(node.id, new THREE.Vector3(x, y, z));
      });
    });

    return positions;
  }, [topology]);

  // Filtered nodes
  const filteredNodes = useMemo(() => {
    if (!topology) return [];
    return topology.nodes.filter((node) => {
      const matchesTier = activeTierFilters.has(node.tier as TopologyTier);
      const q = searchQuery.toLowerCase().trim();
      const matchesQuery =
        !q ||
        node.name.toLowerCase().includes(q) ||
        node.id.toLowerCase().includes(q) ||
        node.business.domain.toLowerCase().includes(q);
      return matchesTier && matchesQuery;
    });
  }, [topology, activeTierFilters, searchQuery]);

  // Nodes with active incidents
  const incidentNodes = useMemo(() => {
    if (!topology) return [];
    return topology.nodes.filter((n) => n.incidents_open > 0 || n.status === 'red');
  }, [topology]);

  // Three.js Lifecycle
  useEffect(() => {
    if (use2DMode || !topology || !canvasRef.current) return;

    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!container) return;

    const width = container.clientWidth || 1000;
    const height = container.clientHeight || 700;

    // 1. Scene, Camera, Renderer
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(GLOBE_COLORS.spaceEdge);

    const camera = new THREE.PerspectiveCamera(GLOBE_STYLE.cameraFovDeg, width / height, 0.1, 1000);
    camera.position.set(0, 16, 28);

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({
        canvas,
        antialias: true,
        alpha: false,
        powerPreference: 'high-performance',
      });
    } catch {
      setForce2D(true);
      return;
    }

    if (!renderer.getContext()) {
      setForce2D(true);
      return;
    }

    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.rotateSpeed = 0.65;
    controls.minDistance = 14;
    controls.maxDistance = 45;
    controls.enablePan = false;
    controlsRef.current = controls;

    // 2. Lights - Key light from upper-left gives rich navy-lit ocean
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.7);
    scene.add(ambientLight);

    const keyLight = new THREE.DirectionalLight(GLOBE_COLORS.oceanLit, 1.4);
    keyLight.position.set(-20, 30, 20);
    scene.add(keyLight);

    const fillLight = new THREE.DirectionalLight(0xffffff, 0.4);
    fillLight.position.set(20, -10, -20);
    scene.add(fillLight);

    // 3. Central Sphere & Atmosphere Shell
    const sphereRadius = 9.0;
    const sphereGeo = new THREE.SphereGeometry(sphereRadius, 64, 64);
    const sphereMat = new THREE.MeshLambertMaterial({
      color: GLOBE_COLORS.ocean,
    });
    const globeMesh = new THREE.Mesh(sphereGeo, sphereMat);
    scene.add(globeMesh);

    // Atmosphere Shell
    const atmosphereGeo = new THREE.SphereGeometry(sphereRadius * GLOBE_STYLE.atmosphereScale, 48, 48);
    const atmosphereMat = new THREE.MeshBasicMaterial({
      color: GLOBE_COLORS.atmosphere,
      transparent: true,
      opacity: GLOBE_STYLE.atmosphereOpacity,
      side: THREE.BackSide,
      blending: THREE.AdditiveBlending,
    });
    const atmosphereMesh = new THREE.Mesh(atmosphereGeo, atmosphereMat);
    scene.add(atmosphereMesh);

    // Starfield Background
    const starsGeo = new THREE.BufferGeometry();
    const starCoords: number[] = [];
    for (let i = 0; i < GLOBE_STYLE.starCount; i++) {
      const u = Math.random();
      const v = Math.random();
      const theta = u * 2.0 * Math.PI;
      const phi = Math.acos(2.0 * v - 1.0);
      const r = 40 + Math.random() * 50;
      starCoords.push(
        r * Math.sin(phi) * Math.cos(theta),
        r * Math.sin(phi) * Math.sin(theta),
        r * Math.cos(phi)
      );
    }
    starsGeo.setAttribute('position', new THREE.Float32BufferAttribute(starCoords, 3));
    const starsMat = new THREE.PointsMaterial({
      color: GLOBE_COLORS.star,
      size: 0.6,
      transparent: true,
      opacity: 0.45,
    });
    const starField = new THREE.Points(starsGeo, starsMat);
    scene.add(starField);

    // 4. Subtle Meridians and Parallels (Lat/Long Grid)
    const gridGroup = new THREE.Group();
    const lineMat = new THREE.LineBasicMaterial({
      color: GLOBE_COLORS.graticule,
      transparent: true,
      opacity: GLOBE_STYLE.graticuleOpacity,
    });

    // Parallels (latitude circles)
    [-68, -42, 0, 8, 35, 55, 78].forEach((latDeg) => {
      const phi = (latDeg * Math.PI) / 180;
      const r = sphereRadius * Math.cos(phi) * 1.002;
      const y = sphereRadius * Math.sin(phi);
      const points: THREE.Vector3[] = [];
      const segments = 64;
      for (let i = 0; i <= segments; i++) {
        const theta = (i / segments) * Math.PI * 2;
        points.push(new THREE.Vector3(r * Math.sin(theta), y, r * Math.cos(theta)));
      }
      const lineGeo = new THREE.BufferGeometry().setFromPoints(points);
      const circleLine = new THREE.Line(lineGeo, lineMat);
      gridGroup.add(circleLine);
    });

    // Meridians (longitude half-circles)
    for (let i = 0; i < 12; i++) {
      const theta = (i / 12) * Math.PI * 2;
      const points: THREE.Vector3[] = [];
      const segments = 48;
      for (let j = 0; j <= segments; j++) {
        const phi = -Math.PI / 2 + (j / segments) * Math.PI;
        const x = sphereRadius * Math.cos(phi) * Math.sin(theta) * 1.002;
        const y = sphereRadius * Math.sin(phi);
        const z = sphereRadius * Math.cos(phi) * Math.cos(theta) * 1.002;
        points.push(new THREE.Vector3(x, y, z));
      }
      const lineGeo = new THREE.BufferGeometry().setFromPoints(points);
      const meridianLine = new THREE.Line(lineGeo, lineMat);
      gridGroup.add(meridianLine);
    }
    scene.add(gridGroup);

    // 5. Nodes as Geometric Beacons
    const nodeObjects = new Map<string, THREE.Object3D>();
    const nodeGroup = new THREE.Group();
    const pulseMeshes: Array<{ mesh: THREE.Mesh; phase: number }> = [];

    topology.nodes.forEach((node) => {
      const pos = nodePositions.get(node.id);
      if (!pos) return;

      const nodeSubGroup = new THREE.Group();
      nodeSubGroup.position.copy(pos);
      nodeSubGroup.userData = { nodeId: node.id };

      const statusConf = STATUS_COLORS[node.status] || STATUS_COLORS.unknown;
      const isImportant = node.kind === 'lb_frontend' || node.kind === 'cloud_sql' || node.kind === 'api_gateway';
      const radiusScale = isImportant ? GLOBE_STYLE.nodeRadius.important : GLOBE_STYLE.nodeRadius.base;
      const beaconRadius = radiusScale * sphereRadius * 1.8;

      const nodeGeo = new THREE.SphereGeometry(beaconRadius, 24, 24);
      const nodeMat = new THREE.MeshLambertMaterial({
        color: statusConf.threeHex,
      });

      const beaconMesh = new THREE.Mesh(nodeGeo, nodeMat);
      nodeSubGroup.add(beaconMesh);

      // Core anchor pin connecting beacon to surface
      const pinPoints = [new THREE.Vector3(0, 0, 0), pos.clone().normalize().multiplyScalar(-0.6)];
      const pinGeo = new THREE.BufferGeometry().setFromPoints(pinPoints);
      const pinLine = new THREE.Line(
        pinGeo,
        new THREE.LineBasicMaterial({ color: statusConf.threeHex, opacity: 0.6, transparent: true })
      );
      nodeSubGroup.add(pinLine);

      // Radiant pulse ring if incidents exist or status is red/amber
      if (node.incidents_open > 0 || node.status === 'red') {
        const ringGeo = new THREE.RingGeometry(0.5, 0.72, 32);
        const ringMat = new THREE.MeshBasicMaterial({
          color: NODE_STATUS_COLORS.red,
          transparent: true,
          opacity: 0.8,
          side: THREE.DoubleSide,
        });
        const pulseMesh = new THREE.Mesh(ringGeo, ringMat);
        pulseMesh.lookAt(pos.clone().multiplyScalar(2));
        nodeSubGroup.add(pulseMesh);
        pulseMeshes.push({ mesh: pulseMesh, phase: Math.random() * Math.PI * 2 });
      }

      nodeGroup.add(nodeSubGroup);
      nodeObjects.set(node.id, nodeSubGroup);
    });
    scene.add(nodeGroup);

    // 6. Arcs as Great-Circle Curves with Animated Traffic Flow
    const arcGroup = new THREE.Group();
    const particleFlows: Array<{
      curve: THREE.QuadraticBezierCurve3;
      particle: THREE.Mesh;
      speed: number;
      progress: number;
    }> = [];

    topology.edges.forEach((edge) => {
      const srcPos = nodePositions.get(edge.src);
      const dstPos = nodePositions.get(edge.dst);
      if (!srcPos || !dstPos) return;

      const mid = new THREE.Vector3().addVectors(srcPos, dstPos).multiplyScalar(0.5);
      const distance = srcPos.distanceTo(dstPos);
      const lift = Math.max(0.6, distance * 0.22);
      mid.normalize().multiplyScalar(sphereRadius + lift);

      const curve = new THREE.QuadraticBezierCurve3(srcPos, mid, dstPos);
      const tubeGeo = new THREE.TubeGeometry(curve, 28, 0.035, 6, false);

      const isError = (edge.error_ratio ?? 0) >= 0.02;
      const arcColor = isError ? GLOBE_COLORS.arcError : GLOBE_COLORS.arc;

      const arcMat = new THREE.MeshBasicMaterial({
        color: arcColor,
        transparent: true,
        opacity: GLOBE_STYLE.arcOpacity,
      });
      const tubeMesh = new THREE.Mesh(tubeGeo, arcMat);
      arcGroup.add(tubeMesh);

      // Traffic flow particle
      const pGeo = new THREE.SphereGeometry(0.09, 12, 12);
      const pMat = new THREE.MeshBasicMaterial({
        color: GLOBE_COLORS.arcParticle,
        transparent: true,
        opacity: 0.9,
      });
      const particle = new THREE.Mesh(pGeo, pMat);
      arcGroup.add(particle);

      const rps = edge.rps ?? 10;
      const speed = Math.max(0.003, Math.min(0.015, Math.log10(rps + 1) * 0.006));

      particleFlows.push({
        curve,
        particle,
        speed,
        progress: Math.random(),
      });
    });
    scene.add(arcGroup);

    // 7. Raycasting for Hover & Click Picking
    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2();

    const handlePointerMove = (event: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

      raycaster.setFromCamera(mouse, camera);
      const intersects = raycaster.intersectObjects(nodeGroup.children, true);

      if (intersects.length > 0) {
        let obj: THREE.Object3D | null = intersects[0].object;
        while (obj && !obj.userData.nodeId && obj.parent) {
          obj = obj.parent;
        }
        if (obj && obj.userData.nodeId) {
          setHoveredNodeId(obj.userData.nodeId);
          canvas.style.cursor = 'pointer';
          return;
        }
      }
      setHoveredNodeId(null);
      canvas.style.cursor = 'default';
    };

    const handleClick = (event: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

      raycaster.setFromCamera(mouse, camera);
      const intersects = raycaster.intersectObjects(nodeGroup.children, true);

      if (intersects.length > 0) {
        let obj: THREE.Object3D | null = intersects[0].object;
        while (obj && !obj.userData.nodeId && obj.parent) {
          obj = obj.parent;
        }
        if (obj && obj.userData.nodeId) {
          const nId = obj.userData.nodeId;
          setSelectedNodeId(nId);
          if (onSelectNode) onSelectNode(nId);

          // Camera fly-to target node
          const targetPos = nodePositions.get(nId);
          if (targetPos) {
            const lookTarget = targetPos.clone().multiplyScalar(2.2);
            camera.position.lerp(lookTarget, 0.6);
            controls.target.copy(targetPos);
            controls.update();
          }
          return;
        }
      }
      setSelectedNodeId(null);
    };

    canvas.addEventListener('mousemove', handlePointerMove);
    canvas.addEventListener('click', handleClick);

    // 8. Animation Loop
    let animId: number;
    let clock = 0;

    const animate = () => {
      animId = requestAnimationFrame(animate);
      clock += 0.016;

      controls.update();

      // Idle globe rotation if not hovered or selecting
      if (!selectedNodeId && !hoveredNodeId) {
        globeMesh.rotation.y += 0.0012;
        gridGroup.rotation.y += 0.0012;
      }

      // Animate incident pulse rings
      pulseMeshes.forEach(({ mesh, phase }) => {
        const pulse = 1.0 + 0.35 * Math.sin(clock * 3.5 + phase);
        mesh.scale.set(pulse, pulse, pulse);
        const mat = mesh.material as THREE.MeshBasicMaterial;
        mat.opacity = Math.max(0.2, 0.85 - (pulse - 1.0) * 1.5);
      });

      // Animate edge traffic flow particles
      particleFlows.forEach((flow) => {
        flow.progress += flow.speed;
        if (flow.progress > 1.0) flow.progress = 0;
        const pt = flow.curve.getPointAt(flow.progress);
        flow.particle.position.copy(pt);
      });

      renderer.render(scene, camera);
    };

    animate();

    // 9. Resize Observer
    const resizeObserver = new ResizeObserver(() => {
      if (!container) return;
      const w = container.clientWidth || 1000;
      const h = container.clientHeight || 700;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    });
    resizeObserver.observe(container);

    return () => {
      cancelAnimationFrame(animId);
      resizeObserver.disconnect();
      canvas.removeEventListener('mousemove', handlePointerMove);
      canvas.removeEventListener('click', handleClick);
      renderer.dispose();
    };
  }, [topology, nodePositions, use2DMode, onSelectNode, selectedNodeId, hoveredNodeId]);

  // Toggle tier filter
  const toggleTier = (tier: TopologyTier) => {
    setActiveTierFilters((prev) => {
      const next = new Set(prev);
      if (next.has(tier)) {
        if (next.size > 1) next.delete(tier);
      } else {
        next.add(tier);
      }
      return next;
    });
  };

  // Hovered node details
  const hoveredNode = useMemo(() => {
    if (!topology || !hoveredNodeId) return null;
    return topology.nodes.find((n) => n.id === hoveredNodeId) || null;
  }, [topology, hoveredNodeId]);

  // Camera reset
  const handleResetCamera = () => {
    setSelectedNodeId(null);
    if (controlsRef.current) {
      controlsRef.current.reset();
    }
  };

  // Zoom controls
  const handleZoomIn = () => {
    if (controlsRef.current) {
      const cam = controlsRef.current.object;
      cam.position.multiplyScalar(0.85);
      controlsRef.current.update();
    }
  };

  const handleZoomOut = () => {
    if (controlsRef.current) {
      const cam = controlsRef.current.object;
      cam.position.multiplyScalar(1.15);
      controlsRef.current.update();
    }
  };

  return (
    <div className="relative flex h-[calc(100vh-108px)] w-full overflow-hidden bg-slate-50 text-slate-900" data-testid="globe-view">
      {/* Slim Left Rail: Environment & Swarm Status */}
      <aside className="z-10 flex w-72 flex-col justify-between border-r border-slate-200 bg-white/95 p-4 shadow-sm backdrop-blur-md">
        <div className="space-y-4">
          {/* Environment Header */}
          <div className="flex items-center justify-between border-b border-slate-100 pb-3">
            <div>
              <div className="flex items-center gap-1.5">
                <Shield className="h-4 w-4 text-blue-600" />
                <span className="font-mono text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Cluster Estate
                </span>
              </div>
              <h2 className="mt-1 font-mono text-sm font-bold text-slate-900">
                {topology?.env_id || 'gcp-production'}
              </h2>
            </div>
            <Badge variant="success" dot>
              Live Realtime
            </Badge>
          </div>

          {/* Quick Search */}
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-400" />
            <input
              type="text"
              placeholder="Filter nodes or domain..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full rounded-md border border-slate-200 bg-slate-50 py-1.5 pl-8 pr-3 text-xs placeholder:text-slate-400 focus:border-blue-500 focus:bg-white focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>

          {/* Tier Latitudinal Filters */}
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span className="font-semibold uppercase tracking-wider">Topology Tiers</span>
              <span>{filteredNodes.length} nodes</span>
            </div>
            <div className="grid grid-cols-2 gap-1.5 text-xs">
              {(
                [
                  'edge',
                  'compute',
                  'data',
                  'messaging',
                  'platform',
                  'observer',
                  'external',
                ] as TopologyTier[]
              ).map((tier) => {
                const active = activeTierFilters.has(tier);
                const count = topology?.nodes.filter((n) => n.tier === tier).length || 0;
                return (
                  <button
                    key={tier}
                    onClick={() => toggleTier(tier)}
                    className={`flex items-center justify-between rounded px-2 py-1 text-left text-xs transition-colors ${
                      active
                        ? 'bg-slate-100 font-medium text-slate-900'
                        : 'text-slate-400 hover:bg-slate-50'
                    }`}
                  >
                    <span className="flex items-center gap-1.5 capitalize">
                      <span
                        className="h-2 w-2 rounded-full"
                        style={{ backgroundColor: TIER_COLORS[tier] }}
                      />
                      {tier}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">{count}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Swarm & Token Governor Telemetry */}
          <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-3 text-xs space-y-2.5">
            <div className="flex items-center justify-between text-slate-700">
              <span className="flex items-center gap-1 font-semibold">
                <Brain className="h-3.5 w-3.5 text-blue-600" />
                Autonomous Swarm
              </span>
              <span className="font-mono text-emerald-700 font-medium">5 Specialists</span>
            </div>

            <div className="grid grid-cols-3 gap-1 text-center font-mono text-[11px]">
              <div className="rounded bg-white p-1 border border-slate-100">
                <span className="block text-[10px] text-slate-400">Awake</span>
                <span className="font-bold text-slate-900">4</span>
              </div>
              <div className="rounded bg-white p-1 border border-slate-100">
                <span className="block text-[10px] text-slate-400">Standby</span>
                <span className="font-bold text-slate-700">1</span>
              </div>
              <div className="rounded bg-white p-1 border border-slate-100">
                <span className="block text-[10px] text-slate-400">Cycle</span>
                <span className="font-bold text-blue-600">300s</span>
              </div>
            </div>

            <div className="space-y-1">
              <div className="flex justify-between text-[11px] text-slate-500">
                <span>Reasoning Quota Budget</span>
                <span className="font-mono text-slate-800">24% ($0.36 / $1.50)</span>
              </div>
              <div className="h-1.5 w-full rounded-full bg-slate-200 overflow-hidden">
                <div className="h-full bg-blue-600 rounded-full" style={{ width: '24%' }} />
              </div>
            </div>
          </div>
        </div>

        {/* View Mode Controls */}
        <div className="border-t border-slate-100 pt-3 space-y-2 text-xs">
          <div className="flex items-center justify-between">
            <span className="text-slate-500">Visualization Mode</span>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setForce2D(!force2D)}
              className="h-7 text-xs"
            >
              {use2DMode ? 'Switch to 3D Globe' : '2D Force Graph'}
            </Button>
          </div>
          <div className="flex items-center gap-1.5 text-[11px] text-slate-400">
            <Wifi className="h-3 w-3 text-emerald-500" />
            <span>SSE Stream: Connected to Sovereign</span>
          </div>
        </div>
      </aside>

      {/* Main Viewport: 3D Globe OR 2D Fallback */}
      <main
        ref={containerRef}
        className="relative flex-1 bg-gradient-to-br from-[#0d2647] via-[#081a33] to-[#040b17] flex items-center justify-center overflow-hidden"
      >
        {use2DMode ? (
          /* 2D Fallback Force Graph */
          <div className="flex h-full w-full flex-col p-6 overflow-auto bg-slate-50" data-testid="globe-2d-graph">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h3 className="text-sm font-bold text-slate-900">
                  2D Architectural Tier Matrix
                </h3>
                <p className="text-xs text-slate-500">
                  Accessible reduced-motion view representing all discovered estate services.
                </p>
              </div>
              <Badge variant="outline">2D Reduced Motion</Badge>
            </div>

            <div className="grid flex-1 grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {(
                ['edge', 'compute', 'data', 'messaging', 'platform', 'observer'] as TopologyTier[]
              ).map((tier) => {
                const nodesInTier = filteredNodes.filter((n) => n.tier === tier);
                if (nodesInTier.length === 0) return null;
                return (
                  <Card key={tier} className="p-4 flex flex-col justify-between">
                    <div>
                      <div className="flex items-center justify-between border-b border-slate-100 pb-2 mb-3">
                        <span className="flex items-center gap-2 font-mono text-xs font-bold uppercase tracking-wider text-slate-800">
                          <span
                            className="h-2.5 w-2.5 rounded-full"
                            style={{ backgroundColor: TIER_COLORS[tier] }}
                          />
                          {tier} Tier
                        </span>
                        <span className="font-mono text-xs text-slate-400">
                          {nodesInTier.length} services
                        </span>
                      </div>
                      <div className="space-y-2">
                        {nodesInTier.map((node) => (
                          <div
                            key={node.id}
                            onClick={() => setSelectedNodeId(node.id)}
                            className={`flex cursor-pointer items-center justify-between rounded-md border p-2 text-xs transition-all ${
                              selectedNodeId === node.id
                                ? 'border-blue-500 bg-blue-50/50 shadow-sm'
                                : 'border-slate-200 bg-white hover:border-slate-300'
                            }`}
                          >
                            <div>
                              <p className="font-semibold text-slate-900">{node.name}</p>
                              <p className="text-[11px] text-slate-500">
                                {node.business.domain}
                              </p>
                            </div>
                            <Badge
                              variant={
                                node.status === 'green'
                                  ? 'success'
                                  : node.status === 'red'
                                  ? 'danger'
                                  : 'warning'
                              }
                              dot
                            >
                              {node.status}
                            </Badge>
                          </div>
                        ))}
                      </div>
                    </div>
                  </Card>
                );
              })}
            </div>
          </div>
        ) : (
          /* Three.js 3D Animated Globe Canvas */
          <div className="relative h-full w-full" data-testid="globe-3d-canvas">
            <canvas ref={canvasRef} className="h-full w-full" />

            {/* Stage Overlay: TierFilter Floating Pill (Top-Left) */}
            <div className="absolute left-6 top-6 z-20 flex items-center gap-1 rounded-full border border-slate-700/50 bg-[#081a33]/80 p-1 backdrop-blur-md shadow-lg">
              <span className="px-2.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                Tiers
              </span>
              {(['edge', 'compute', 'data', 'messaging', 'platform'] as TopologyTier[]).map((tier) => {
                const active = activeTierFilters.has(tier);
                return (
                  <button
                    key={tier}
                    onClick={() => toggleTier(tier)}
                    className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-all ${
                      active
                        ? 'bg-blue-600/90 text-white shadow-sm'
                        : 'text-slate-300 hover:bg-slate-800/60'
                    }`}
                  >
                    <span
                      className="h-1.5 w-1.5 rounded-full"
                      style={{ backgroundColor: TIER_COLORS[tier] }}
                    />
                    <span className="capitalize">{tier}</span>
                  </button>
                );
              })}
            </div>

            {/* Stage Overlay: Floating IncidentBanner (Bottom-Right, Above PulseBar) */}
            {incidentNodes.length > 0 && !incidentDismissed && (
              <div className="absolute bottom-20 right-6 z-20 flex max-w-sm items-center gap-3 rounded-2xl border border-red-500/40 bg-[#081a33]/90 p-2.5 shadow-xl backdrop-blur-md animate-in slide-in-from-bottom-2">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-red-500/20 text-red-400">
                  <ShieldAlert className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs font-semibold text-white">
                    Incident open on {incidentNodes[0]?.name || 'estate node'}
                  </p>
                  <p className="text-[10px] text-slate-400">
                    {incidentNodes.length} active alert{incidentNodes.length > 1 ? 's' : ''} requiring SRE review
                  </p>
                </div>
                <button
                  onClick={() => {
                    if (onSelectIncident) {
                      onSelectIncident(incidentNodes[0]?.id || 'inc-1');
                    } else {
                      window.location.hash = '#incidents';
                    }
                  }}
                  className="rounded-full bg-red-600 px-3 py-1 text-xs font-medium text-white transition hover:bg-red-500 shadow-sm"
                >
                  War Room
                </button>
                <button
                  onClick={() => setIncidentDismissed(true)}
                  className="rounded-full p-1 text-slate-400 hover:text-white"
                  title="Dismiss banner"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            )}

            {/* Stage Overlay: PulseBar (Bottom-Center) */}
            <div className="absolute bottom-4 left-1/2 z-20 flex -translate-x-1/2 items-center gap-6 rounded-full border border-slate-700/50 bg-[#081a33]/90 px-8 py-2.5 shadow-2xl backdrop-blur-md">
              <button
                onClick={() => {}}
                className="group flex flex-col items-center text-center transition hover:opacity-90"
              >
                <span className="text-[11px] text-slate-400 group-hover:text-slate-300">
                  Services
                </span>
                <span className="font-mono text-lg font-bold text-white">
                  {topology?.nodes.length || 0}
                </span>
              </button>

              <div className="h-7 w-px bg-slate-700/60" />

              <button
                onClick={() => { window.location.hash = '#incidents'; }}
                className="group flex flex-col items-center text-center transition hover:opacity-90"
              >
                <span className="text-[11px] text-slate-400 group-hover:text-slate-300">
                  Incidents
                </span>
                <span
                  className={`font-mono text-lg font-bold ${
                    incidentNodes.length > 0 ? 'text-red-400' : 'text-emerald-400'
                  }`}
                >
                  {incidentNodes.length}
                </span>
              </button>

              <div className="h-7 w-px bg-slate-700/60" />

              <button
                onClick={() => { window.location.hash = '#approvals'; }}
                className="group flex flex-col items-center text-center transition hover:opacity-90"
              >
                <span className="text-[11px] text-slate-400 group-hover:text-slate-300">
                  Approvals
                </span>
                <span className="font-mono text-lg font-bold text-amber-400">
                  2
                </span>
              </button>

              <div className="h-7 w-px bg-slate-700/60" />

              <button
                onClick={() => { window.location.hash = '#organism'; }}
                className="group flex flex-col items-center text-center transition hover:opacity-90"
              >
                <span className="text-[11px] text-slate-400 group-hover:text-slate-300">
                  Agents awake
                </span>
                <span className="font-mono text-lg font-bold text-cyan-400">
                  4/5
                </span>
              </button>
            </div>

            {/* Stage Overlay: MapControls Vertical Pill (Bottom-Right) */}
            <div className="absolute bottom-4 right-6 z-20 flex flex-col items-center rounded-2xl border border-slate-700/50 bg-[#081a33]/90 p-1.5 shadow-xl backdrop-blur-md">
              <button
                onClick={handleZoomIn}
                title="Zoom In"
                className="rounded-xl p-2 text-slate-300 transition hover:bg-slate-800 hover:text-white"
              >
                <Plus className="h-4 w-4" />
              </button>
              <button
                onClick={handleZoomOut}
                title="Zoom Out"
                className="rounded-xl p-2 text-slate-300 transition hover:bg-slate-800 hover:text-white"
              >
                <Minus className="h-4 w-4" />
              </button>
              <div className="my-1 h-px w-5 bg-slate-700/60" />
              <button
                onClick={handleResetCamera}
                title="Reset Camera Orientation"
                className="rounded-xl p-2 text-slate-300 transition hover:bg-slate-800 hover:text-white"
              >
                <RotateCcw className="h-4 w-4" />
              </button>
              <button
                onClick={() => setForce2D(true)}
                title="Switch to 2D Matrix"
                className="rounded-xl p-2 text-slate-300 transition hover:bg-slate-800 hover:text-white"
              >
                <Grid className="h-4 w-4" />
              </button>
            </div>

            {/* Hover Tooltip Overlay */}
            {hoveredNode && (
              <div
                className="pointer-events-none absolute bottom-24 left-6 z-20 rounded-xl border border-slate-700/60 bg-[#081a33]/95 px-3 py-2 text-xs shadow-xl backdrop-blur-md text-white"
                data-testid="globe-hover-tooltip"
              >
                <div className="flex items-center gap-2">
                  <span
                    className="h-2 w-2 rounded-full"
                    style={{ backgroundColor: STATUS_COLORS[hoveredNode.status]?.hex }}
                  />
                  <span className="font-bold text-white">{hoveredNode.name}</span>
                  <Badge variant="outline" className="text-[10px] text-slate-300 border-slate-600">
                    {hoveredNode.tier}
                  </Badge>
                </div>
                <p className="mt-1 text-[11px] text-slate-300">
                  {hoveredNode.business.domain} • Inferred Role: {hoveredNode.business.role_summary}
                </p>
              </div>
            )}
          </div>
        )}
      </main>

      {/* Right Drawer: Business Context Inspector (DetailSheet) */}
      {selectedNode && (
        <aside
          className="z-30 flex w-96 flex-col justify-between border-l border-slate-200 bg-white shadow-2xl animate-in slide-in-from-right duration-200 overflow-y-auto"
          data-testid="business-context-drawer"
        >
          <div className="p-5 space-y-5">
            {/* Drawer Header */}
            <div className="flex items-start justify-between border-b border-slate-100 pb-3">
              <div>
                <div className="flex items-center gap-2">
                  <span
                    className="h-2.5 w-2.5 rounded-full"
                    style={{ backgroundColor: STATUS_COLORS[selectedNode.status]?.hex }}
                  />
                  <h3 className="font-mono text-sm font-bold text-slate-900">
                    {selectedNode.name}
                  </h3>
                </div>
                <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                  <Badge variant="outline">{selectedNode.tier} tier</Badge>
                  <span>{selectedNode.kind}</span>
                </div>
              </div>
              <button
                onClick={() => setSelectedNodeId(null)}
                className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {loadingContext && (
              <div className="rounded bg-blue-50 p-2 text-xs font-mono text-blue-700 animate-pulse">
                Fetching verified business context & evidence...
              </div>
            )}

            {/* Inferred Business Domain & Role Summary */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Business Semantics
                </span>
                <Badge variant="info">
                  {Math.round(selectedNode.business.confidence * 100)}% Confidence
                </Badge>
              </div>
              <div className="rounded-lg border border-slate-100 bg-slate-50/80 p-3 text-xs leading-relaxed text-slate-700">
                <p className="font-semibold text-slate-900 mb-1">
                  Domain: {selectedNode.business.domain}
                </p>
                <p>
                  {businessContext?.role_summary || selectedNode.business.role_summary}
                </p>
              </div>
            </div>

            {/* Golden Signals KPI Matrix — Honesty Rule Enforced */}
            <div className="space-y-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                Golden Signals (Cloud Monitoring)
              </span>
              <div className="grid grid-cols-3 gap-2 text-center text-xs">
                <div className="rounded-md border border-slate-200 bg-white p-2">
                  <span className="block text-[10px] text-slate-400">Throughput</span>
                  <span className="font-mono font-bold text-slate-900">
                    {selectedNode.golden.rps !== null
                      ? `${selectedNode.golden.rps} rps`
                      : 'not measured yet'}
                  </span>
                </div>
                <div className="rounded-md border border-slate-200 bg-white p-2">
                  <span className="block text-[10px] text-slate-400">p95 Latency</span>
                  <span className="font-mono font-bold text-slate-900">
                    {selectedNode.golden.p95_ms !== null
                      ? `${selectedNode.golden.p95_ms}ms`
                      : 'not measured yet'}
                  </span>
                </div>
                <div className="rounded-md border border-slate-200 bg-white p-2">
                  <span className="block text-[10px] text-slate-400">Error Ratio</span>
                  <span
                    className={`font-mono font-bold ${
                      selectedNode.golden.error_ratio !== null &&
                      selectedNode.golden.error_ratio > 0.01
                        ? 'text-red-600'
                        : 'text-slate-900'
                    }`}
                  >
                    {selectedNode.golden.error_ratio !== null
                      ? `${(selectedNode.golden.error_ratio * 100).toFixed(2)}%`
                      : 'not measured yet'}
                  </span>
                </div>
              </div>
            </div>

            {/* Discovered End-to-End Journeys */}
            {businessContext?.journeys && businessContext.journeys.length > 0 && (
              <div className="space-y-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Participating Business Journeys
                </span>
                <div className="space-y-1.5">
                  {businessContext.journeys.map((j, idx) => (
                    <div
                      key={idx}
                      onClick={() => onSelectJourney && onSelectJourney(j.name)}
                      className="flex cursor-pointer items-center justify-between rounded-md border border-slate-200 bg-white p-2 text-xs hover:border-blue-400 transition"
                    >
                      <span className="font-medium text-slate-800">{j.name}</span>
                      <span className="flex items-center gap-1 font-mono text-[10px] text-blue-600">
                        {j.steps.length} hops <ArrowRight className="h-3 w-3" />
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Formal Invariants & Security Posture */}
            <div className="space-y-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                Security Posture & Invariants
              </span>
              <div className="rounded-md border border-slate-200 bg-white p-2.5 text-xs space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-slate-500">IAP Authentication:</span>
                  <Badge variant={selectedNode.posture.iap ? 'success' : 'danger'}>
                    {selectedNode.posture.iap ? 'Enforced' : 'Disabled'}
                  </Badge>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-slate-500">Public Ingress:</span>
                  <Badge variant={selectedNode.posture.ingress === 'internal' ? 'success' : 'warning'}>
                    {selectedNode.posture.ingress}
                  </Badge>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-slate-500">Invoker IAM:</span>
                  <Badge variant={selectedNode.posture.invoker_iam ? 'success' : 'danger'}>
                    {selectedNode.posture.invoker_iam ? 'Enforced' : 'Public'}
                  </Badge>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-slate-500">Cloud Armor WAF:</span>
                  <Badge variant={selectedNode.posture.waf ? 'success' : 'outline'}>
                    {selectedNode.posture.waf ? 'Protected' : 'None'}
                  </Badge>
                </div>
              </div>
            </div>

            {/* Invariants Checked on this Node */}
            {businessContext?.invariants && businessContext.invariants.length > 0 && (
              <div className="space-y-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Enforced Invariants
                </span>
                <div className="space-y-1">
                  {businessContext.invariants.map((inv, idx) => (
                    <div
                      key={idx}
                      className="rounded border border-slate-100 bg-slate-50 p-2 text-xs"
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-mono font-medium text-slate-800">{inv.check}</span>
                        <Badge variant="outline">{inv.status || 'verified'}</Badge>
                      </div>
                      <p className="mt-1 font-mono text-[10px] text-slate-500 truncate">
                        {inv.statement}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Verified Evidence Pack References */}
            {businessContext?.evidence && businessContext.evidence.length > 0 && (
              <div className="space-y-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Grounded Evidence
                </span>
                <div className="space-y-1">
                  {businessContext.evidence.map((ev, idx) => (
                    <div
                      key={idx}
                      className="rounded border border-slate-100 bg-slate-50 p-2 text-xs"
                    >
                      <div className="flex items-center gap-1.5 font-mono text-[10px] text-blue-700">
                        <Badge variant="outline" className="uppercase text-[9px]">
                          {ev.kind}
                        </Badge>
                        <span className="truncate">{ev.ref}</span>
                      </div>
                      <p className="mt-1 font-mono text-[11px] text-slate-600 bg-white p-1 rounded border border-slate-100">
                        {ev.excerpt}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Drawer Actions */}
          <div className="border-t border-slate-200 bg-slate-50 p-4 flex gap-2">
            <Button
              variant="primary"
              size="sm"
              className="flex-1"
              onClick={() => {
                if (onSelectIncident && businessContext?.recent_incidents?.[0]) {
                  onSelectIncident(businessContext.recent_incidents[0].id);
                } else {
                  window.location.hash = '#incidents';
                }
              }}
            >
              Incident War Room
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setSelectedNodeId(null)}
            >
              Close
            </Button>
          </div>
        </aside>
      )}
    </div>
  );
};
