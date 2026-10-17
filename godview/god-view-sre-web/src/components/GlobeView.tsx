import React, { useEffect, useRef, useState, useMemo } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import {
  AlertTriangle,
  ArrowRight,
  Brain,
  RotateCcw,
  Search,
  Shield,
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

export interface GlobeViewProps {
  topology: TopologyResponse | null;
  onSelectNode?: (nodeId: string) => void;
  onSelectIncident?: (incidentId: string) => void;
  onSelectJourney?: (journeyName: string) => void;
  onSelectAgent?: (agentId: string) => void;
}

// Latitudinal band assignment per architectural tier (degrees)
const TIER_LATITUDES: Record<TopologyTier, number> = {
  observer: 78,
  edge: 55,
  messaging: 35,
  compute: 8,
  data: -42,
  platform: -68,
  external: 20, // Orbiting satellite at higher radius
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
  green: { hex: '#34A853', threeHex: 0x34a853 },
  amber: { hex: '#FBBC04', threeHex: 0xfbbc04 },
  red: { hex: '#EA4335', threeHex: 0xea4335 },
  unknown: { hex: '#94A3B8', threeHex: 0x94a3b8 },
};

export const GlobeView: React.FC<GlobeViewProps> = ({
  topology,
  onSelectNode,
  onSelectIncident,
  onSelectJourney,
  onSelectAgent,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [businessContext, setBusinessContext] = useState<FullBusinessContext | null>(null);
  const [loadingContext, setLoadingContext] = useState(false);
  const [activeTierFilters, setActiveTierFilters] = useState<Set<TopologyTier>>(
    new Set(['edge', 'compute', 'data', 'messaging', 'platform', 'observer', 'external'])
  );
  const [searchQuery, setSearchQuery] = useState('');
  const [force2D, setForce2D] = useState(false);
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false);

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

        // Longitude: clustered around domain center with jitter
        const thetaSpread = domainStep * 0.75;
        const thetaOffset =
          groupNodes.length > 1
            ? (nIdx / (groupNodes.length - 1) - 0.5) * thetaSpread
            : 0;
        const theta = baseTheta + thetaOffset;

        const baseRadius = tier === 'external' ? 12.8 : 9.0;
        const x = baseRadius * Math.cos(phi) * Math.sin(theta);
        const y = baseRadius * Math.sin(phi);
        const z = baseRadius * Math.cos(phi) * Math.cos(theta);

        positions.set(node.id, new THREE.Vector3(x, y, z));
      });
    });

    return positions;
  }, [topology]);

  // Filtered nodes based on active tier filters and search query
  const filteredNodes = useMemo(() => {
    if (!topology) return [];
    return topology.nodes.filter((node) => {
      const tierMatch = activeTierFilters.has(node.tier as TopologyTier);
      const searchMatch =
        !searchQuery ||
        node.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        node.id.toLowerCase().includes(searchQuery.toLowerCase()) ||
        node.business.domain.toLowerCase().includes(searchQuery.toLowerCase());
      return tierMatch && searchMatch;
    });
  }, [topology, activeTierFilters, searchQuery]);

  // Three.js interactive 3D Globe Lifecycle
  useEffect(() => {
    if (use2DMode) return;
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container || !topology) return;

    // Check WebGL availability safely
    try {
      const testGl = canvas.getContext('webgl2') || canvas.getContext('webgl');
      if (!testGl) {
        console.warn('WebGL not supported in current environment; switching to 2D force graph.');
        setForce2D(true);
        return;
      }
    } catch {
      setForce2D(true);
      return;
    }

    const width = container.clientWidth || 1000;
    const height = container.clientHeight || 700;

    // 1. Scene, Camera, Renderer
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xf8fafc); // Crisp luminous white/slate canvas

    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
    camera.position.set(0, 10, 26);

    const renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true,
      powerPreference: 'high-performance',
    });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.rotateSpeed = 0.65;
    controls.minDistance = 14;
    controls.maxDistance = 45;
    controls.enablePan = false;

    // 2. Lights
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.85);
    scene.add(ambientLight);

    const dirLight1 = new THREE.DirectionalLight(0xffffff, 0.65);
    dirLight1.position.set(20, 30, 20);
    scene.add(dirLight1);

    const dirLight2 = new THREE.DirectionalLight(0x4285f4, 0.25);
    dirLight2.position.set(-20, -10, -20);
    scene.add(dirLight2);

    // 3. Central Luminous Sphere
    const sphereRadius = 9.0;
    const sphereGeo = new THREE.SphereGeometry(sphereRadius, 64, 64);
    const sphereMat = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      roughness: 0.9,
      metalness: 0.05,
      emissive: 0xf1f5f9,
      emissiveIntensity: 0.2,
    });
    const globeMesh = new THREE.Mesh(sphereGeo, sphereMat);
    scene.add(globeMesh);

    // 4. Subtle Meridians and Parallels (Lat/Long Grid)
    const gridGroup = new THREE.Group();
    const lineMat = new THREE.LineBasicMaterial({
      color: 0xcbd5e1, // Subtle slate divider
      transparent: true,
      opacity: 0.45,
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
      const nodeGeo = new THREE.SphereGeometry(0.38, 24, 24);
      const nodeMat = new THREE.MeshStandardMaterial({
        color: statusConf.threeHex,
        emissive: statusConf.threeHex,
        emissiveIntensity: 0.5,
        roughness: 0.3,
        metalness: 0.2,
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
          color: 0xea4335, // Incident Red
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

    // 6. Great-Circle Animated Tube Arcs for Edges
    const arcGroup = new THREE.Group();
    const particleFlows: Array<{
      curve: THREE.CatmullRomCurve3;
      particle: THREE.Mesh;
      speed: number;
      progress: number;
    }> = [];

    topology.edges.forEach((edge, eIdx) => {
      const p1 = nodePositions.get(edge.src);
      const p2 = nodePositions.get(edge.dst);
      if (!p1 || !p2) return;

      const dist = p1.distanceTo(p2);
      if (dist < 0.1) return;

      // Elevated midpoint along great circle
      const mid = p1.clone().add(p2).multiplyScalar(0.5);
      const elevHeight = Math.max(1.1, dist * 0.24);
      const midElev = mid.clone().normalize().multiplyScalar(sphereRadius + elevHeight);

      // Intermediate smoothed Bezier control points
      const p1Elev = p1.clone().lerp(midElev, 0.5).normalize().multiplyScalar(sphereRadius + elevHeight * 0.7);
      const p2Elev = p2.clone().lerp(midElev, 0.5).normalize().multiplyScalar(sphereRadius + elevHeight * 0.7);

      const curve = new THREE.CatmullRomCurve3([p1, p1Elev, midElev, p2Elev, p2]);
      const tubeGeo = new THREE.TubeGeometry(curve, 32, 0.038, 8, false);

      const isAlert = edge.error_ratio !== null && edge.error_ratio > 0.01;
      const arcColor = isAlert ? 0xea4335 : 0x1a73e8; // Red or Google Blue

      const tubeMat = new THREE.MeshBasicMaterial({
        color: arcColor,
        transparent: true,
        opacity: isAlert ? 0.75 : 0.42,
      });

      const tubeMesh = new THREE.Mesh(tubeGeo, tubeMat);
      arcGroup.add(tubeMesh);

      // Moving animated packet pulse along the arc
      const particleGeo = new THREE.SphereGeometry(0.09, 12, 12);
      const particleMat = new THREE.MeshBasicMaterial({ color: isAlert ? 0xea4335 : 0x4285f4 });
      const particleMesh = new THREE.Mesh(particleGeo, particleMat);
      arcGroup.add(particleMesh);

      particleFlows.push({
        curve,
        particle: particleMesh,
        speed: 0.003 + (eIdx % 5) * 0.001,
        progress: (eIdx * 0.17) % 1.0,
      });
    });
    scene.add(arcGroup);

    // 7. Raycasting for picking
    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2();

    const handlePointerMove = (evt: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      mouse.x = ((evt.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((evt.clientY - rect.top) / rect.height) * 2 + 1;

      raycaster.setFromCamera(mouse, camera);
      const intersects = raycaster.intersectObjects(nodeGroup.children, true);

      if (intersects.length > 0) {
        let current: THREE.Object3D | null = intersects[0].object;
        while (current && !current.userData.nodeId && current.parent) {
          current = current.parent;
        }
        if (current && current.userData.nodeId) {
          setHoveredNodeId(current.userData.nodeId);
          canvas.style.cursor = 'pointer';
          return;
        }
      }
      setHoveredNodeId(null);
      canvas.style.cursor = 'grab';
    };

    const handleClick = (evt: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      mouse.x = ((evt.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((evt.clientY - rect.top) / rect.height) * 2 + 1;

      raycaster.setFromCamera(mouse, camera);
      const intersects = raycaster.intersectObjects(nodeGroup.children, true);

      if (intersects.length > 0) {
        let current: THREE.Object3D | null = intersects[0].object;
        while (current && !current.userData.nodeId && current.parent) {
          current = current.parent;
        }
        if (current && current.userData.nodeId) {
          const clickedId = current.userData.nodeId;
          setSelectedNodeId(clickedId);
          if (onSelectNode) onSelectNode(clickedId);

          // Camera fly-to
          const pos = nodePositions.get(clickedId);
          if (pos) {
            const targetCamPos = pos.clone().normalize().multiplyScalar(22);
            targetCamPos.y += 2.0;

            const startPos = camera.position.clone();
            const startTarget = controls.target.clone();
            const endTarget = pos.clone();
            let flyProgress = 0;

            const flyAnim = () => {
              flyProgress += 0.05;
              if (flyProgress < 1.0) {
                camera.position.lerpVectors(startPos, targetCamPos, flyProgress);
                controls.target.lerpVectors(startTarget, endTarget, flyProgress);
                requestAnimationFrame(flyAnim);
              } else {
                camera.position.copy(targetCamPos);
                controls.target.copy(endTarget);
              }
            };
            flyAnim();
          }
        }
      }
    };

    canvas.addEventListener('mousemove', handlePointerMove);
    canvas.addEventListener('click', handleClick);

    // 8. Animation Render Loop
    let animId: number;
    let clock = 0;

    const animate = () => {
      animId = requestAnimationFrame(animate);
      clock += 0.016;

      controls.update();

      // Subtle slow planetary rotation if not dragging
      if ((controls as any).state === -1 || (controls as any).state === undefined) {
        scene.rotation.y += 0.0006;
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
  }, [topology, nodePositions, use2DMode, onSelectNode]);

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

  return (
    <div className="relative flex h-[calc(100vh-68px)] w-full overflow-hidden bg-slate-50 text-slate-900">
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
                {topology?.env_id || 'gcp-general-ak'}
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
        className="relative flex-1 bg-slate-50 flex items-center justify-center overflow-hidden"
      >
        {use2DMode ? (
          /* 2D Fallback Force Graph */
          <div className="flex h-full w-full flex-col p-6 overflow-auto" data-testid="globe-2d-graph">
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

            {/* Hover Tooltip Overlay */}
            {hoveredNode && (
              <div
                className="pointer-events-none absolute bottom-6 left-6 z-20 rounded-lg border border-slate-200 bg-white/95 px-3 py-2 text-xs shadow-lg backdrop-blur-md"
                data-testid="globe-hover-tooltip"
              >
                <div className="flex items-center gap-2">
                  <span
                    className="h-2 w-2 rounded-full"
                    style={{ backgroundColor: STATUS_COLORS[hoveredNode.status]?.hex }}
                  />
                  <span className="font-bold text-slate-900">{hoveredNode.name}</span>
                  <Badge variant="outline" className="text-[10px]">
                    {hoveredNode.tier}
                  </Badge>
                </div>
                <p className="mt-1 text-[11px] text-slate-500">
                  {hoveredNode.business.domain} • Inferred Role: {hoveredNode.business.role_summary}
                </p>
              </div>
            )}

            {/* Camera Helper Controls */}
            <div className="absolute right-6 top-6 z-20 flex flex-col gap-2">
              <button
                onClick={() => setSelectedNodeId(null)}
                title="Reset Camera"
                className="rounded-lg border border-slate-200 bg-white/90 p-2 text-slate-700 shadow-sm transition hover:bg-white hover:text-slate-900"
              >
                <RotateCcw className="h-4 w-4" />
              </button>
            </div>
          </div>
        )}
      </main>

      {/* Right Drawer: Business Context Inspector */}
      {selectedNode && (
        <aside
          className="z-30 flex w-96 flex-col justify-between border-l border-slate-200 bg-white shadow-xl animate-in slide-in-from-right duration-200 overflow-y-auto"
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
                  <span className="text-slate-500">Cloud Armor WAF:</span>
                  <span className="font-mono text-slate-800">
                    {selectedNode.posture.waf || 'not attached'}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-slate-500">Invoker IAM:</span>
                  <span className="font-mono text-slate-800">
                    {selectedNode.posture.invoker_iam ? 'Restricted' : 'Public'}
                  </span>
                </div>
              </div>
            </div>

            {/* Evidence References */}
            {businessContext?.evidence && businessContext.evidence.length > 0 && (
              <div className="space-y-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Verification Evidence
                </span>
                <div className="space-y-1">
                  {businessContext.evidence.map((ev, idx) => (
                    <div
                      key={idx}
                      className="rounded bg-slate-50 p-2 text-[11px] text-slate-600 font-mono border border-slate-100"
                    >
                      <span className="font-bold text-slate-900 uppercase tracking-wider">
                        [{ev.kind}]
                      </span>{' '}
                      {ev.ref}
                      {ev.excerpt && (
                        <p className="mt-0.5 text-slate-500 text-[10px] italic">
                          &quot;{ev.excerpt}&quot;
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Active Incidents linked to node */}
            {selectedNode.incidents_open > 0 && (
              <div className="rounded-lg border border-red-200 bg-red-50/50 p-3 space-y-2">
                <div className="flex items-center justify-between text-xs font-bold text-red-800">
                  <span className="flex items-center gap-1.5">
                    <AlertTriangle className="h-4 w-4 text-red-600" />
                    Active Incidents ({selectedNode.incidents_open})
                  </span>
                </div>
                <p className="text-xs text-red-700">
                  Node telemetry is currently reporting policy or SLA breaches.
                </p>
                {onSelectIncident && (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => onSelectIncident('active-incident')}
                    className="w-full text-xs border-red-200 text-red-700 hover:bg-red-100"
                  >
                    Open Incident War-Room
                  </Button>
                )}
              </div>
            )}
          </div>

          {/* Drawer Footer with Agent Link */}
          {selectedNode.agent_id && onSelectAgent && (
            <div className="border-t border-slate-100 p-4 bg-slate-50">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => onSelectAgent(selectedNode.agent_id!)}
                className="w-full text-xs"
              >
                Inspect Supervising Agent ({selectedNode.agent_id})
              </Button>
            </div>
          )}
        </aside>
      )}
    </div>
  );
};
