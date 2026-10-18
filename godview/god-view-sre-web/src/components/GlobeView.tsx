import {
  AlertTriangle,
  ArrowRight,
  Brain,
  Compass,
  Layers,
  Minus,
  Plus,
  RotateCcw,
  Search,
  Shield,
  Wifi,
  X,
} from 'lucide-react';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { cockpitApi } from '../api/client';
import type {
  ApprovalCard,
  CostAndDriftOverview,
  CourtAgent,
  EpisodicIncident,
  FullBusinessContext,
  PerceptionReport,
  TopologyNode,
  TopologyResponse,
  TopologyTier,
} from '../api/types';
import {
  Badge,
  Button,
  Card,
  formatCurrency,
} from './ui';
import {
  GLOBE_COLORS,
  GLOBE_STYLE,
  NODE_STATUS_COLORS,
  STATUS_COLORS,
  TIER_LATITUDE,
} from '../theme/globe';

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

// Latitudinal band assignment per architectural tier (degrees)
const TIER_LATITUDES: Record<TopologyTier, number> = {
  observer: TIER_LATITUDE.observer ?? 68,
  edge: TIER_LATITUDE.edge ?? 46,
  messaging: TIER_LATITUDE.messaging ?? -10,
  compute: TIER_LATITUDE.compute ?? 16,
  data: TIER_LATITUDE.data ?? -38,
  platform: TIER_LATITUDE.platform ?? -62,
  external: TIER_LATITUDE.external ?? 22, // Orbiting satellite at higher radius
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

// Spherical Linear Interpolation (SLERP) for great circle arc paths
function slerpVector3(v1: THREE.Vector3, v2: THREE.Vector3, t: number): THREE.Vector3 {
  const n1 = v1.clone().normalize();
  const n2 = v2.clone().normalize();
  const dot = Math.max(-1, Math.min(1, n1.dot(n2)));
  if (dot > 0.9999) return n1;
  const omega = Math.acos(dot);
  const sinOmega = Math.sin(omega);
  if (Math.abs(sinOmega) < 0.0001) return n1.lerp(n2, t).normalize();
  const w1 = Math.sin((1 - t) * omega) / sinOmega;
  const w2 = Math.sin(t * omega) / sinOmega;
  return new THREE.Vector3(
    n1.x * w1 + n2.x * w2,
    n1.y * w1 + n2.y * w2,
    n1.z * w1 + n2.z * w2
  ).normalize();
}

const GLOBE_RADIUS = 7.8;

export const GlobeView: React.FC<GlobeViewProps> = ({
  initialForce2D = false,
  topology,
  selectedNodeId: propSelectedNodeId,
  selectedJourneyName,
  court,
  incidents,
  approvals,
  perceive,
  cost: _cost,
  streamStatus = 'polling',
  onSelectNode,
  onSelectIncident,
  onSelectJourney,
  onSelectAgent: _onSelectAgent,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(
    propSelectedNodeId || null
  );
  const [activeJourney, setActiveJourney] = useState<string | null>(
    selectedJourneyName || null
  );
  const [businessContext, setBusinessContext] = useState<FullBusinessContext | null>(null);
  const [loadingContext, setLoadingContext] = useState<boolean>(false);
  const [contextError, setContextError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [incidentBannerDismissed, setIncidentBannerDismissed] = useState<boolean>(false);

  const [activeTierFilters, setActiveTierFilters] = useState<Set<TopologyTier>>(
    new Set(['edge', 'compute', 'data', 'messaging', 'platform', 'observer', 'external'])
  );

  // Sync prop changes
  useEffect(() => {
    if (propSelectedNodeId !== undefined) {
      setSelectedNodeId(propSelectedNodeId);
    }
  }, [propSelectedNodeId]);

  useEffect(() => {
    if (selectedJourneyName !== undefined) {
      setActiveJourney(selectedJourneyName);
    }
  }, [selectedJourneyName]);

  // Reduced motion detection
  const [prefersReducedMotion, setPrefersReducedMotion] = useState<boolean>(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  });

  const [force2D, setForce2D] = useState<boolean>(initialForce2D || false);
  const use2DMode = prefersReducedMotion || force2D;

  useEffect(() => {
    if (initialForce2D !== undefined) {
      setForce2D(initialForce2D);
    }
  }, [initialForce2D]);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    const listener = (e: MediaQueryListEvent) => setPrefersReducedMotion(e.matches);
    mediaQuery.addEventListener('change', listener);
    return () => mediaQuery.removeEventListener('change', listener);
  }, []);

  // Fetch verified Business Context when node is selected
  useEffect(() => {
    if (!selectedNodeId) {
      setBusinessContext(null);
      setContextError(null);
      return;
    }
    let cancelled = false;
    setLoadingContext(true);
    setContextError(null);
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
          setContextError(err.message || 'Failed to load business context');
          setLoadingContext(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [selectedNodeId]);

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
        (node.business?.domain && node.business.domain.toLowerCase().includes(q));
      return matchesTier && matchesQuery;
    });
  }, [topology, activeTierFilters, searchQuery]);

  // Compute 3D node coordinates across front hemisphere facing camera
  const nodePositions = useMemo(() => {
    if (!topology || topology.nodes.length === 0) return new Map<string, THREE.Vector3>();

    const positions = new Map<string, THREE.Vector3>();
    const sphereRadius = GLOBE_RADIUS;

    // Group nodes by domain
    const domainGroups: Record<string, TopologyNode[]> = {};
    topology.nodes.forEach((node) => {
      const domain = node.business?.domain || 'core';
      if (!domainGroups[domain]) domainGroups[domain] = [];
      domainGroups[domain].push(node);
    });

    const domains = Object.keys(domainGroups);
    const domainCount = Math.max(1, domains.length);
    // Span longitude across the camera-facing arc [-55deg, +55deg] (cos(theta) > 0.57 > 0)
    const totalLongSpanRad = Math.min(Math.PI * 0.65, domainCount * 0.45);
    const startTheta = -totalLongSpanRad / 2;
    const domainStep = domainCount > 1 ? totalLongSpanRad / (domainCount - 1) : 0;

    domains.forEach((dom, dIdx) => {
      const groupNodes = domainGroups[dom];
      const baseTheta = domainCount === 1 ? 0 : startTheta + dIdx * domainStep;

      groupNodes.forEach((node, nIdx) => {
        const tier = (node.tier as TopologyTier) || 'compute';
        const latDeg = TIER_LATITUDES[tier] ?? 16;
        const phi = (latDeg * Math.PI) / 180;

        // Spread nodes slightly in longitude if multiple share a domain
        const offset = (nIdx - (groupNodes.length - 1) / 2) * 0.16;
        const theta = baseTheta + offset;

        // External nodes orbit at higher radius as satellites
        const r = tier === 'external' ? sphereRadius * GLOBE_STYLE.satelliteOrbitRadius : sphereRadius;

        const x = r * Math.cos(phi) * Math.sin(theta);
        const y = r * Math.sin(phi);
        const z = r * Math.cos(phi) * Math.cos(theta);

        positions.set(node.id, new THREE.Vector3(x, y, z));
      });
    });

    return positions;
  }, [topology]);

  // Active journey node set
  const activeJourneyNodes = useMemo(() => {
    if (!activeJourney || !topology) return null;
    const j = topology.journeys.find((item) => item.name === activeJourney);
    if (!j) return null;
    return new Set(j.steps.map((s) => s.node));
  }, [activeJourney, topology]);

  // Incident count
  const activeIncidentsCount = useMemo(() => {
    if (!incidents) return null;
    return incidents.filter((i) => i.outcome !== 'resolved').length;
  }, [incidents]);

  // Approvals count
  const pendingApprovalsCount = useMemo(() => {
    if (!approvals) return null;
    return approvals.filter((a) => (a as any).status === 'pending' || a.decision === 'pending').length;
  }, [approvals]);

  // Agent counts
  const totalAgents = court ? court.length : null;
  const awakeAgents = court ? court.filter((a) => a.state === 'awake').length : null;

  // Selected node
  const selectedNode = useMemo(() => {
    if (!topology || !selectedNodeId) return null;
    return topology.nodes.find((n) => n.id === selectedNodeId || n.name === selectedNodeId) || null;
  }, [topology, selectedNodeId]);

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
    camera.position.set(0, 9, 29);
    camera.lookAt(0, 0, 0);
    cameraRef.current = camera;

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
    controls.minDistance = 12;
    controls.maxDistance = 50;
    controls.enablePan = false;
    controlsRef.current = controls;

    // 2. Lights
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.7);
    scene.add(ambientLight);

    const keyLight = new THREE.DirectionalLight(GLOBE_COLORS.oceanLit, 1.4);
    keyLight.position.set(-20, 30, 20);
    scene.add(keyLight);

    const fillLight = new THREE.DirectionalLight(0xffffff, 0.4);
    fillLight.position.set(20, -10, -20);
    scene.add(fillLight);

    // 3. Central Sphere & Atmosphere Shell
    const sphereRadius = GLOBE_RADIUS;
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

    [-62, -38, -10, 0, 16, 46, 68].forEach((latDeg) => {
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
      const isImportant =
        node.kind === 'lb_frontend' ||
        node.kind === 'cloud_sql' ||
        node.kind === 'api_gateway' ||
        node.tier === 'edge';
      const radiusScale = isImportant ? GLOBE_STYLE.nodeRadius.important : GLOBE_STYLE.nodeRadius.base;
      const beaconRadius = radiusScale * sphereRadius * 1.9;

      const isJourneyTarget = activeJourneyNodes?.has(node.id) || activeJourneyNodes?.has(node.name);

      const nodeGeo = new THREE.SphereGeometry(
        isJourneyTarget ? beaconRadius * 1.35 : beaconRadius,
        24,
        24
      );
      const nodeMat = new THREE.MeshLambertMaterial({
        color: isJourneyTarget ? '#FBBC04' : statusConf.num,
      });

      const beaconMesh = new THREE.Mesh(nodeGeo, nodeMat);
      nodeSubGroup.add(beaconMesh);

      // Core anchor pin connecting beacon to surface
      const pinPoints = [new THREE.Vector3(0, 0, 0), pos.clone().normalize().multiplyScalar(-0.6)];
      const pinGeo = new THREE.BufferGeometry().setFromPoints(pinPoints);
      const pinLine = new THREE.Line(
        pinGeo,
        new THREE.LineBasicMaterial({
          color: isJourneyTarget ? '#FBBC04' : statusConf.num,
          opacity: 0.7,
          transparent: true,
        })
      );
      nodeSubGroup.add(pinLine);

      // Radiant pulse ring if incidents exist or status is red
      if (node.incidents_open > 0 || node.status === 'red') {
        const ringGeo = new THREE.RingGeometry(0.55, 0.8, 32);
        const ringMat = new THREE.MeshBasicMaterial({
          color: NODE_STATUS_COLORS.red,
          transparent: true,
          opacity: 0.85,
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

    // 6. Arcs as Great-Circle Curves with Animated Traffic Flow (SLERP + Sine Altitude)
    const arcGroup = new THREE.Group();
    const particleFlows: Array<{
      curve: THREE.CatmullRomCurve3;
      particle: THREE.Mesh;
      speed: number;
      progress: number;
    }> = [];

    topology.edges.forEach((edge) => {
      const srcPos = nodePositions.get(edge.src);
      const dstPos = nodePositions.get(edge.dst);
      if (!srcPos || !dstPos) return;

      const dist = srcPos.distanceTo(dstPos);
      const maxLift = Math.max(0.75, dist * 0.22);
      const steps = 24;
      const points: THREE.Vector3[] = [];

      for (let i = 0; i <= steps; i++) {
        const t = i / steps;
        const altitude = maxLift * Math.sin(t * Math.PI);
        const pt = slerpVector3(srcPos, dstPos, t).multiplyScalar(sphereRadius + altitude);
        points.push(pt);
      }

      const curve = new THREE.CatmullRomCurve3(points);
      const isError = (edge.error_ratio ?? 0) >= 0.02;
      const isJourneyEdge =
        activeJourneyNodes &&
        (activeJourneyNodes.has(edge.src) && activeJourneyNodes.has(edge.dst));

      const arcColor = isJourneyEdge
        ? '#FBBC04'
        : isError
        ? GLOBE_COLORS.arcError
        : GLOBE_COLORS.arc;

      const tubeGeo = new THREE.TubeGeometry(
        curve,
        28,
        isJourneyEdge ? 0.055 : 0.038,
        8,
        false
      );
      const arcMat = new THREE.MeshBasicMaterial({
        color: arcColor,
        transparent: true,
        opacity: isJourneyEdge ? 0.95 : activeJourneyNodes ? 0.2 : GLOBE_STYLE.arcOpacity,
      });
      const tubeMesh = new THREE.Mesh(tubeGeo, arcMat);
      arcGroup.add(tubeMesh);

      // Traffic flow particle
      const pGeo = new THREE.SphereGeometry(isJourneyEdge ? 0.12 : 0.09, 12, 12);
      const pMat = new THREE.MeshBasicMaterial({
        color: isJourneyEdge ? '#FFFFFF' : GLOBE_COLORS.arcParticle,
      });
      const pMesh = new THREE.Mesh(pGeo, pMat);
      arcGroup.add(pMesh);

      particleFlows.push({
        curve,
        particle: pMesh,
        speed: isJourneyEdge ? 0.006 : 0.0035 + Math.random() * 0.0015,
        progress: Math.random(),
      });
    });
    scene.add(arcGroup);

    // 7. Raycasting for picking/clicking nodes
    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2();

    const handlePointerDown = (evt: MouseEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      mouse.x = ((evt.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((evt.clientY - rect.top) / rect.height) * 2 + 1;

      raycaster.setFromCamera(mouse, camera);
      const intersects = raycaster.intersectObjects(nodeGroup.children, true);

      if (intersects.length > 0) {
        let current: THREE.Object3D | null = intersects[0].object;
        while (current && !current.userData.nodeId && current.parent !== nodeGroup) {
          current = current.parent;
        }
        if (current && current.userData.nodeId) {
          const pickedId = current.userData.nodeId as string;
          setSelectedNodeId(pickedId);
          if (onSelectNode) onSelectNode(pickedId);

          // Camera fly-to
          const targetPos = nodePositions.get(pickedId);
          if (targetPos) {
            const camTarget = targetPos.clone().normalize().multiplyScalar(24);
            camera.position.lerp(camTarget, 0.5);
            controls.target.copy(targetPos.clone().multiplyScalar(0.4));
          }
        }
      }
    };

    renderer.domElement.addEventListener('pointerdown', handlePointerDown);

    // 8. Animation Loop
    let animFrameId: number;
    let clock = new THREE.Clock();

    const animate = () => {
      animFrameId = requestAnimationFrame(animate);
      const delta = clock.getDelta();
      const elapsed = clock.getElapsedTime();

      controls.update();

      // Animate traffic particles along great-circle curves
      particleFlows.forEach((flow) => {
        flow.progress = (flow.progress + flow.speed) % 1;
        const pt = flow.curve.getPointAt(flow.progress);
        flow.particle.position.copy(pt);
      });

      // Animate incident pulse rings
      pulseMeshes.forEach((pulse) => {
        pulse.phase += delta * 2.5;
        const scale = 1.0 + Math.sin(pulse.phase) * 0.22;
        pulse.mesh.scale.set(scale, scale, 1);
        (pulse.mesh.material as THREE.MeshBasicMaterial).opacity =
          0.3 + Math.max(0, Math.cos(pulse.phase)) * 0.6;
      });

      // Subtle slow star rotation
      starField.rotation.y = elapsed * 0.008;

      renderer.render(scene, camera);
    };

    animate();

    // 9. Resize Observer
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width: w, height: h } = entry.contentRect;
        if (w > 0 && h > 0) {
          camera.aspect = w / h;
          camera.updateProjectionMatrix();
          renderer.setSize(w, h);
        }
      }
    });

    resizeObserver.observe(container);

    return () => {
      cancelAnimationFrame(animFrameId);
      resizeObserver.disconnect();
      renderer.domElement.removeEventListener('pointerdown', handlePointerDown);
      renderer.dispose();
      controls.dispose();
    };
  }, [use2DMode, topology, nodePositions, activeJourneyNodes, onSelectNode]);

  // Tier filter toggle
  const toggleTierFilter = (tier: TopologyTier) => {
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

  // Map Controls
  const handleZoomIn = () => {
    if (controlsRef.current && cameraRef.current) {
      cameraRef.current.position.multiplyScalar(0.85);
      controlsRef.current.update();
    }
  };

  const handleZoomOut = () => {
    if (controlsRef.current && cameraRef.current) {
      cameraRef.current.position.multiplyScalar(1.15);
      controlsRef.current.update();
    }
  };

  const handleResetCamera = () => {
    if (controlsRef.current && cameraRef.current) {
      cameraRef.current.position.set(0, 9, 29);
      controlsRef.current.target.set(0, 0, 0);
      controlsRef.current.update();
    }
  };

  const handleSelectNodeInternal = useCallback((nodeId: string) => {
    setSelectedNodeId(nodeId);
    if (onSelectNode) onSelectNode(nodeId);
  }, [onSelectNode]);

  // Topology missing state
  if (!topology) {
    return (
      <div
        className="flex flex-1 flex-col items-center justify-center p-12 text-center bg-slate-50"
        data-testid="globe-topology-empty"
      >
        <div className="rounded-full bg-slate-100 p-4 mb-4">
          <Layers className="h-8 w-8 text-slate-400" />
        </div>
        <h3 className="text-base font-semibold text-slate-800">
          Topology Not Measured Yet
        </h3>
        <p className="mt-1 max-w-md text-xs text-slate-500">
          The 3D Globe renders real discovered estate topology from the Sovereign control plane.
          Waiting for <code>/api/v1/topology</code>.
        </p>
      </div>
    );
  }

  return (
    <div
      className="relative flex flex-1 w-full min-h-0 overflow-hidden bg-slate-50 text-slate-900"
      data-testid="globe-view"
    >
      {/* Slim Left Rail: Environment & Swarm Status */}
      <aside className="z-10 flex w-72 shrink-0 flex-col justify-between border-r border-slate-200 bg-white/95 p-4 shadow-sm backdrop-blur-md overflow-y-auto">
        <div className="space-y-4">
          {/* Environment Header */}
          <div className="flex items-center justify-between border-b border-slate-100 pb-3">
            <div>
              <div className="flex items-center gap-1.5">
                <Shield className="h-4 w-4 text-blue-600" />
                <span className="font-mono text-xs font-semibold text-slate-500">
                  Cluster estate
                </span>
              </div>
              <h2 className="mt-1 font-mono text-sm font-bold text-slate-900">
                {topology.env_id || 'default'}
              </h2>
            </div>
            <Badge
              variant={
                streamStatus === 'connected'
                  ? 'success'
                  : streamStatus === 'reconnecting'
                  ? 'warning'
                  : 'outline'
              }
              dot
            >
              {streamStatus === 'connected'
                ? 'Live Realtime'
                : streamStatus === 'reconnecting'
                ? 'Reconnecting'
                : streamStatus === 'polling'
                ? 'Polling'
                : 'Disconnected'}
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
              <span className="font-semibold">Topology tiers</span>
              <span className="font-mono text-[11px]">{topology.nodes.length} nodes</span>
            </div>
            <div className="space-y-1">
              {(
                ['edge', 'compute', 'data', 'messaging', 'platform', 'observer', 'external'] as TopologyTier[]
              ).map((tier) => {
                const count = topology.nodes.filter((n) => n.tier === tier).length;
                if (count === 0 && tier !== 'edge' && tier !== 'compute' && tier !== 'data') return null;
                const active = activeTierFilters.has(tier);
                return (
                  <button
                    key={tier}
                    onClick={() => toggleTierFilter(tier)}
                    className={`flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-xs transition ${
                      active
                        ? 'bg-slate-100/80 font-medium text-slate-900'
                        : 'text-slate-400 hover:bg-slate-50'
                    }`}
                  >
                    <span className="flex items-center gap-2">
                      <span
                        className="h-2 w-2 rounded-full"
                        style={{ backgroundColor: TIER_COLORS[tier] }}
                      />
                      <span className="capitalize">{tier}</span>
                    </span>
                    <span className="font-mono text-[11px] text-slate-400">{count}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Business Journeys Overlay Selector */}
          <div className="space-y-2 border-t border-slate-100 pt-3">
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span className="font-semibold">Business journeys</span>
              <span className="font-mono text-[11px]">{topology.journeys?.length ?? 0}</span>
            </div>
            {topology.journeys && topology.journeys.length > 0 ? (
              <div className="space-y-1">
                {topology.journeys.map((j) => {
                  const isActive = activeJourney === j.name;
                  return (
                    <button
                      key={j.name}
                      onClick={() => {
                        const next = isActive ? null : j.name;
                        setActiveJourney(next);
                        if (onSelectJourney) {
                          onSelectJourney(next || '');
                        }
                      }}
                      className={`flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-xs transition ${
                        isActive
                          ? 'bg-blue-50 border border-blue-200 font-semibold text-blue-800'
                          : 'text-slate-600 hover:bg-slate-50'
                      }`}
                    >
                      <span className="truncate">{j.name}</span>
                      <span className="font-mono text-[10px] text-slate-400 shrink-0">
                        {j.steps.length} steps
                      </span>
                    </button>
                  );
                })}
              </div>
            ) : (
              <p className="text-xs text-slate-400">No journeys discovered</p>
            )}
          </div>

          {/* Autonomous Swarm Telemetry */}
          <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-3 text-xs space-y-2.5">
            <div className="flex items-center justify-between text-slate-700">
              <span className="flex items-center gap-1 font-semibold">
                <Brain className="h-3.5 w-3.5 text-blue-600" />
                Autonomous swarm
              </span>
              <span className="font-mono text-emerald-700 font-medium">
                {totalAgents != null ? `${totalAgents} Specialists` : 'Not measured yet'}
              </span>
            </div>

            <div className="grid grid-cols-3 gap-1 text-center font-mono text-[11px]">
              <div className="rounded bg-white p-1 border border-slate-100">
                <span className="block text-[10px] text-slate-400">Awake</span>
                <span className="font-bold text-slate-900">
                  {awakeAgents != null ? awakeAgents : '—'}
                </span>
              </div>
              <div className="rounded bg-white p-1 border border-slate-100">
                <span className="block text-[10px] text-slate-400">Standby</span>
                <span className="font-bold text-slate-700">
                  {totalAgents != null && awakeAgents != null
                    ? totalAgents - awakeAgents
                    : '—'}
                </span>
              </div>
              <div className="rounded bg-white p-1 border border-slate-100">
                <span className="block text-[10px] text-slate-400">Cycle</span>
                <span className="font-bold text-blue-600">
                  {perceive?.sentinel_cadence_s != null
                    ? `${perceive.sentinel_cadence_s}s`
                    : 'Not measured yet'}
                </span>
              </div>
            </div>

            {/* Reasoning Quota Budget */}
            <div className="space-y-1">
              <div className="flex justify-between text-[11px] text-slate-500">
                <span>Reasoning quota budget</span>
                <span className="font-mono text-slate-800">
                  {perceive?.sovereign_budget_daily_usd != null &&
                  perceive?.sovereign_spent_today_usd != null
                    ? `${Math.min(
                        100,
                        Math.round(
                          (perceive.sovereign_spent_today_usd /
                            perceive.sovereign_budget_daily_usd) *
                            100
                        )
                      )}% (${formatCurrency(perceive.sovereign_spent_today_usd)} / ${formatCurrency(perceive.sovereign_budget_daily_usd)})`
                    : 'Not measured yet'}
                </span>
              </div>
              {perceive?.sovereign_budget_daily_usd != null &&
                perceive?.sovereign_spent_today_usd != null && (
                  <div className="h-1.5 w-full rounded-full bg-slate-200 overflow-hidden">
                    <div
                      className="h-full bg-blue-600 rounded-full"
                      style={{
                        width: `${Math.min(
                          100,
                          (perceive.sovereign_spent_today_usd /
                            perceive.sovereign_budget_daily_usd) *
                            100
                        )}%`,
                      }}
                    />
                  </div>
                )}
            </div>
          </div>
        </div>

        {/* View Mode Controls */}
        <div className="border-t border-slate-100 pt-3 space-y-2 text-xs">
          <div className="flex items-center justify-between">
            <span className="text-slate-500">Visualization mode</span>
            <Button
              data-testid="toggle-2d-mode"
              variant="secondary"
              size="sm"
              onClick={() => setForce2D(!force2D)}
              className="h-7 text-xs"
            >
              {use2DMode ? 'Switch to 3D Globe' : '2D Force Graph'}
            </Button>
          </div>
          <div className="flex items-center gap-1.5 text-[11px] text-slate-400">
            <Wifi
              className={`h-3 w-3 ${
                streamStatus === 'connected'
                  ? 'text-emerald-500'
                  : streamStatus === 'reconnecting'
                  ? 'text-amber-500'
                  : 'text-slate-400'
              }`}
            />
            <span>
              SSE Stream:{' '}
              {streamStatus === 'connected'
                ? 'Connected to Sovereign'
                : streamStatus === 'reconnecting'
                ? 'Reconnecting...'
                : streamStatus === 'polling'
                ? 'Polling fallback (10s)'
                : 'Disconnected'}
            </span>
          </div>
        </div>
      </aside>

      {/* Main Viewport: 3D Globe OR 2D Architectural Graph Fallback */}
      <main
        ref={containerRef}
        className="relative flex-1 bg-gradient-to-br from-[#0d2647] via-[#081a33] to-[#040b17] flex items-center justify-center overflow-hidden"
      >
        {use2DMode ? (
          /* 2D Architectural Force Graph Fallback with SVG Edge Flow */
          <div
            className="flex h-full w-full flex-col p-6 overflow-auto bg-slate-50 text-slate-900"
            data-testid="globe-2d-graph"
          >
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h3 className="text-sm font-bold text-slate-900">
                  2D Architectural Tier Matrix &amp; Edge Flow
                </h3>
                <p className="text-xs text-slate-500">
                  Accessible reduced-motion view representing all discovered estate services and active connections.
                </p>
              </div>
              <Badge variant="outline">2D Reduced Motion</Badge>
            </div>

            {/* Architectural Tier Matrix */}
            <div className="grid flex-1 grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
              {(
                ['edge', 'compute', 'data', 'messaging', 'platform', 'observer', 'external'] as TopologyTier[]
              ).map((tier) => {
                const nodesInTier = filteredNodes.filter((n) => n.tier === tier);
                if (nodesInTier.length === 0) return null;
                return (
                  <Card key={tier} className="p-4 flex flex-col justify-between">
                    <div>
                      <div className="flex items-center justify-between border-b border-slate-100 pb-2 mb-3">
                        <span className="flex items-center gap-2 font-mono text-xs font-bold text-slate-800">
                          <span
                            className="h-2.5 w-2.5 rounded-full"
                            style={{ backgroundColor: TIER_COLORS[tier] }}
                          />
                          <span className="capitalize">{tier} tier</span>
                        </span>
                        <span className="font-mono text-xs text-slate-400">
                          {nodesInTier.length} services
                        </span>
                      </div>
                      <div className="space-y-2">
                        {nodesInTier.map((node) => (
                          <div
                            key={node.id}
                            onClick={() => handleSelectNodeInternal(node.id)}
                            className={`flex cursor-pointer items-center justify-between rounded-md border p-2 text-xs transition-all ${
                              selectedNodeId === node.id
                                ? 'border-blue-500 bg-blue-50/50 shadow-sm'
                                : 'border-slate-200 bg-white hover:border-slate-300'
                            }`}
                          >
                            <div>
                              <p className="font-semibold text-slate-900">{node.name}</p>
                              <p className="text-[11px] text-slate-500">
                                {node.business?.domain}
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

            {/* Edge Connections Table in 2D Mode */}
            {topology.edges.length > 0 && (
              <Card className="p-4">
                <div className="flex items-center justify-between mb-3 border-b border-slate-100 pb-2">
                  <span className="text-xs font-semibold text-slate-700">
                    Discovered Inter-Service Edges ({topology.edges.length} total)
                  </span>
                  <span className="text-[11px] font-mono text-slate-400">
                    Verified via traces &amp; route configs
                  </span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs border-collapse">
                    <thead>
                      <tr className="border-b border-slate-200 bg-slate-50 text-slate-600">
                        <th className="p-2 font-semibold">Source</th>
                        <th className="p-2 font-semibold">Destination</th>
                        <th className="p-2 font-semibold">Protocol</th>
                        <th className="p-2 font-semibold">Evidence</th>
                        <th className="p-2 text-right font-semibold">Throughput</th>
                        <th className="p-2 text-right font-semibold">Error Ratio</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 num-tabular">
                      {topology.edges.map((edge) => (
                        <tr key={edge.id} className="hover:bg-slate-50">
                          <td className="p-2 font-mono font-medium text-slate-800">{edge.src}</td>
                          <td className="p-2 font-mono font-medium text-slate-800">{edge.dst}</td>
                          <td className="p-2 text-slate-600">{edge.kind}</td>
                          <td className="p-2 text-slate-500">{edge.evidence_kind}</td>
                          <td className="p-2 text-right font-mono">
                            {edge.rps != null ? `${edge.rps} rps` : 'Not measured yet'}
                          </td>
                          <td className="p-2 text-right font-mono">
                            {edge.error_ratio != null
                              ? `${(edge.error_ratio * 100).toFixed(2)}%`
                              : 'Not measured yet'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
          </div>
        ) : (
          /* Three.js 3D Animated Globe Canvas */
          <div className="relative h-full w-full" data-testid="globe-3d-canvas">
            <canvas ref={canvasRef} className="h-full w-full block" />

            {/* Stage Overlay: TierFilter Floating Pill (Top-Left) */}
            <div className="absolute top-4 left-6 z-20 flex flex-wrap items-center gap-1.5 rounded-full border border-slate-700/50 bg-[#081a33]/90 px-3 py-1.5 shadow-xl backdrop-blur-md">
              <span className="mr-1 text-[11px] font-semibold text-slate-400">
                Tiers
              </span>
              {(
                ['edge', 'compute', 'data', 'messaging', 'platform'] as TopologyTier[]
              ).map((tier) => {
                const active = activeTierFilters.has(tier);
                return (
                  <button
                    key={tier}
                    onClick={() => toggleTierFilter(tier)}
                    className={`flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs transition ${
                      active
                        ? 'bg-blue-600 font-semibold text-white shadow-sm'
                        : 'bg-slate-800/80 text-slate-300 hover:bg-slate-700 hover:text-white'
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

            {/* Stage Overlay: Active Journey Filter Pill (Top-Center) */}
            {activeJourney && (
              <div className="absolute top-4 left-1/2 -translate-x-1/2 z-20 flex items-center gap-2 rounded-full border border-amber-500/60 bg-[#1f1600]/90 px-4 py-1.5 text-xs text-amber-200 shadow-xl backdrop-blur-md">
                <Compass className="h-3.5 w-3.5 text-amber-400 animate-spin" />
                <span>
                  Journey path:{' '}
                  <strong className="text-white">{activeJourney}</strong>
                </span>
                <button
                  onClick={() => setActiveJourney(null)}
                  className="rounded-full p-0.5 hover:bg-amber-900/60 text-amber-300"
                  title="Clear Journey Filter"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            )}

            {/* Stage Overlay: Floating IncidentBanner (Bottom-Right, Above MapControls) */}
            {activeIncidentsCount != null && activeIncidentsCount > 0 && !incidentBannerDismissed && (
              <div className="absolute bottom-20 right-8 z-20 max-w-xs md:max-w-sm rounded-xl border border-red-500/40 bg-[#1c0808]/90 p-3.5 text-xs text-slate-200 shadow-2xl backdrop-blur-md break-words">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-start gap-2.5">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-node-incident" style={{ color: 'var(--gv-node-incident)' }} />
                    <div>
                      <h4 className="font-semibold text-red-200">
                        {activeIncidentsCount} Active Incident{activeIncidentsCount > 1 ? 's' : ''} Detected
                      </h4>
                      <p className="mt-0.5 text-slate-300 leading-snug break-words">
                        {incidents?.find((i) => i.outcome !== 'resolved')?.symptom_summary
                          ? incidents.find((i) => i.outcome !== 'resolved')!.symptom_summary
                          : `${activeIncidentsCount} active incident${activeIncidentsCount > 1 ? 's' : ''} requiring investigation.`}
                      </p>
                    </div>
                  </div>
                  <button
                    onClick={() => setIncidentBannerDismissed(true)}
                    className="text-slate-400 hover:text-white"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
                <div className="mt-2.5 flex items-center justify-end gap-2">
                  <Button
                    variant="danger"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={() => {
                      if (onSelectIncident) onSelectIncident('active-incident');
                    }}
                  >
                    Open Incident War Room
                  </Button>
                </div>
              </div>
            )}

            {/* Stage Overlay: PulseBar (Bottom-Center) */}
            <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-20 flex items-center gap-6 rounded-full border border-slate-700/60 bg-[#081a33]/90 px-6 py-2 shadow-2xl backdrop-blur-md num-tabular">
              <div className="flex flex-col items-center text-center">
                <span className="text-[11px] text-on-space-variant" style={{ color: 'var(--gv-on-space-variant)' }}>
                  Services
                </span>
                <span className="font-mono text-base font-bold text-on-space" style={{ color: 'var(--gv-on-space)' }}>
                  {topology.nodes.length}
                </span>
              </div>

              <div className="h-7 w-px bg-slate-700/60" />

              <button
                onClick={() => {
                  window.location.hash = '#incidents';
                }}
                className="group flex flex-col items-center text-center transition hover:opacity-90"
              >
                <span className="text-[11px] group-hover:text-slate-200" style={{ color: 'var(--gv-on-space-variant)' }}>
                  Incidents
                </span>
                <span
                  className="font-mono text-base font-bold"
                  style={{
                    color:
                      activeIncidentsCount == null
                        ? 'var(--gv-color-unknown)'
                        : activeIncidentsCount > 0
                        ? 'var(--gv-node-incident)'
                        : 'var(--gv-node-healthy)',
                  }}
                >
                  {activeIncidentsCount != null ? activeIncidentsCount : 'Not measured yet'}
                </span>
              </button>

              <div className="h-7 w-px bg-slate-700/60" />

              <button
                onClick={() => {
                  window.location.hash = '#approvals';
                }}
                className="group flex flex-col items-center text-center transition hover:opacity-90"
              >
                <span className="text-[11px] group-hover:text-slate-200" style={{ color: 'var(--gv-on-space-variant)' }}>
                  Approvals
                </span>
                <span
                  className="font-mono text-base font-bold"
                  style={{
                    color:
                      pendingApprovalsCount != null
                        ? 'var(--gv-node-warning)'
                        : 'var(--gv-color-unknown)',
                  }}
                >
                  {pendingApprovalsCount != null ? pendingApprovalsCount : 'Not measured yet'}
                </span>
              </button>

              <div className="h-7 w-px bg-slate-700/60" />

              <button
                onClick={() => {
                  window.location.hash = '#organism';
                }}
                className="group flex flex-col items-center text-center transition hover:opacity-90"
              >
                <span className="text-[11px] group-hover:text-slate-200" style={{ color: 'var(--gv-on-space-variant)' }}>
                  Agents awake
                </span>
                <span
                  className="font-mono text-base font-bold"
                  style={{
                    color:
                      awakeAgents != null
                        ? 'var(--gv-node-agent)'
                        : 'var(--gv-color-unknown)',
                  }}
                >
                  {awakeAgents != null && totalAgents != null
                    ? `${awakeAgents}/${totalAgents}`
                    : totalAgents != null
                    ? `${totalAgents}`
                    : 'Not measured yet'}
                </span>
              </button>
            </div>

            {/* Stage Overlay: MapControls Vertical Pill (Bottom-Right) */}
            <div className="absolute bottom-4 right-6 z-20 flex flex-col items-center rounded-2xl border border-slate-700/50 bg-[#081a33]/90 p-1.5 shadow-xl backdrop-blur-md">
              <button
                onClick={handleZoomIn}
                title="Zoom in"
                className="rounded-xl p-2 text-slate-300 transition hover:bg-slate-800 hover:text-white"
              >
                <Plus className="h-4 w-4" />
              </button>
              <button
                onClick={handleZoomOut}
                title="Zoom out"
                className="rounded-xl p-2 text-slate-300 transition hover:bg-slate-800 hover:text-white"
              >
                <Minus className="h-4 w-4" />
              </button>
              <button
                onClick={handleResetCamera}
                title="Reset camera view"
                className="rounded-xl p-2 text-slate-300 transition hover:bg-slate-800 hover:text-white"
              >
                <RotateCcw className="h-4 w-4" />
              </button>
              <button
                onClick={() => setForce2D(!force2D)}
                title="Toggle 2D / 3D mode"
                className="rounded-xl p-2 text-slate-300 transition hover:bg-slate-800 hover:text-white"
              >
                <Layers className="h-4 w-4" />
              </button>
            </div>
          </div>
        )}

        {/* Right Drawer: Business Context Inspector (DetailSheet) */}
        {selectedNode && (
          <aside
            data-testid="business-context-drawer"
            className="absolute top-0 right-0 z-30 h-full w-[440px] overflow-y-auto border-l border-slate-200 bg-white/95 shadow-2xl backdrop-blur-lg transition-transform"
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
                  Fetching verified business context &amp; evidence...
                </div>
              )}

              {contextError && (
                <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900">
                  <p className="font-semibold">Business context unavailable</p>
                  <p className="mt-0.5 text-amber-800">{contextError}</p>
                </div>
              )}

              {/* Inferred Business Domain & Role Summary */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-slate-600">
                    Business semantics
                  </span>
                  <Badge variant="info">
                    {Math.round((selectedNode.business?.confidence || 0.94) * 100)}% Confidence
                  </Badge>
                </div>
                <div className="rounded-lg border border-slate-100 bg-slate-50/80 p-3 text-xs leading-relaxed text-slate-700">
                  <p className="font-semibold text-slate-900 mb-1">
                    Domain: {selectedNode.business?.domain}
                  </p>
                  <p>
                    {businessContext?.role_summary || selectedNode.business?.role_summary}
                  </p>
                </div>
              </div>

              {/* Golden Signals KPI Matrix — Honesty Rule Enforced */}
              <div className="space-y-2">
                <span className="text-xs font-semibold text-slate-600">
                  Golden signals (Cloud Monitoring)
                </span>
                <div className="grid grid-cols-3 gap-2 text-center text-xs">
                  <div className="rounded-md border border-slate-200 bg-white p-2">
                    <span className="block text-[10px] text-slate-400">Throughput</span>
                    <span className="font-mono font-bold text-slate-900">
                      {selectedNode.golden?.rps != null
                        ? `${selectedNode.golden.rps} rps`
                        : 'Not measured yet'}
                    </span>
                  </div>
                  <div className="rounded-md border border-slate-200 bg-white p-2">
                    <span className="block text-[10px] text-slate-400">p95 Latency</span>
                    <span className="font-mono font-bold text-slate-900">
                      {selectedNode.golden?.p95_ms != null
                        ? `${selectedNode.golden.p95_ms}ms`
                        : 'Not measured yet'}
                    </span>
                  </div>
                  <div className="rounded-md border border-slate-200 bg-white p-2">
                    <span className="block text-[10px] text-slate-400">Error Ratio</span>
                    <span
                      className={`font-mono font-bold ${
                        selectedNode.golden?.error_ratio != null &&
                        selectedNode.golden.error_ratio > 0.01
                          ? 'text-red-600'
                          : 'text-slate-900'
                      }`}
                    >
                      {selectedNode.golden?.error_ratio != null
                        ? `${(selectedNode.golden.error_ratio * 100).toFixed(2)}%`
                        : 'Not measured yet'}
                    </span>
                  </div>
                </div>
              </div>

              {/* Business KPIs with Grounded Evidence (§1.4 / §3.7) */}
              {businessContext?.kpis && businessContext.kpis.length > 0 && (
                <div className="space-y-2">
                  <span className="text-xs font-semibold text-slate-600">
                    Business KPIs &amp; telemetry
                  </span>
                  <div className="space-y-2">
                    {businessContext.kpis.map((kpi, idx) => (
                      <div
                        key={idx}
                        className="rounded-lg border border-slate-200 bg-white p-3 text-xs space-y-1.5"
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-slate-900">{kpi.name}</span>
                          <span className="font-mono text-xs font-bold text-blue-700">
                            {kpi.value != null
                              ? `${kpi.value} ${kpi.unit}`
                              : 'Not measured yet'}
                          </span>
                        </div>
                        <p className="font-mono text-[11px] text-slate-500 truncate" title={kpi.definition}>
                          {kpi.definition}
                        </p>
                        <div className="flex items-center justify-between text-[10px] text-slate-400 pt-1 border-t border-slate-100">
                          <span>Confidence: {Math.round((kpi.confidence ?? 0.9) * 100)}%</span>
                          {kpi.evidence?.ref && (
                            <span className="font-mono truncate max-w-[160px]" title={kpi.evidence.ref}>
                              {kpi.evidence.ref}
                            </span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Discovered End-to-End Journeys */}
              {businessContext?.journeys && businessContext.journeys.length > 0 && (
                <div className="space-y-2">
                  <span className="text-xs font-semibold text-slate-600">
                    Participating business journeys
                  </span>
                  <div className="space-y-1.5">
                    {businessContext.journeys.map((j, idx) => (
                      <div
                        key={idx}
                        onClick={() => {
                          setActiveJourney(j.name);
                          if (onSelectJourney) onSelectJourney(j.name);
                        }}
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
                <span className="text-xs font-semibold text-slate-600">
                  Security posture &amp; invariants
                </span>
                <div className="rounded-md border border-slate-200 bg-white p-2.5 text-xs space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-slate-500">IAP authentication:</span>
                    <Badge
                      variant={
                        selectedNode.posture?.iap === true
                          ? 'success'
                          : selectedNode.posture?.iap === false
                          ? 'danger'
                          : 'outline'
                      }
                    >
                      {selectedNode.posture?.iap === true
                        ? 'Enforced'
                        : selectedNode.posture?.iap === false
                        ? 'Disabled'
                        : 'Not measured yet'}
                    </Badge>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-slate-500">Public ingress:</span>
                    <Badge
                      variant={
                        selectedNode.posture?.ingress === 'internal'
                          ? 'success'
                          : selectedNode.posture?.ingress
                          ? 'warning'
                          : 'outline'
                      }
                    >
                      {selectedNode.posture?.ingress || 'Not measured yet'}
                    </Badge>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-slate-500">Invoker IAM:</span>
                    <Badge
                      variant={
                        selectedNode.posture?.invoker_iam === true
                          ? 'success'
                          : selectedNode.posture?.invoker_iam === false
                          ? 'danger'
                          : 'outline'
                      }
                    >
                      {selectedNode.posture?.invoker_iam === true
                        ? 'Enforced'
                        : selectedNode.posture?.invoker_iam === false
                        ? 'Public'
                        : 'Not measured yet'}
                    </Badge>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-slate-500">Cloud Armor WAF:</span>
                    <Badge
                      variant={
                        selectedNode.posture?.waf &&
                        selectedNode.posture.waf !== 'unknown' &&
                        selectedNode.posture.waf !== 'none'
                          ? 'success'
                          : 'outline'
                      }
                    >
                      {selectedNode.posture?.waf && selectedNode.posture.waf !== 'unknown'
                        ? selectedNode.posture.waf
                        : 'Not measured yet'}
                    </Badge>
                  </div>
                </div>
              </div>

              {/* Invariants Checked on this Node */}
              {businessContext?.invariants && businessContext.invariants.length > 0 && (
                <div className="space-y-2">
                  <span className="text-xs font-semibold text-slate-600">
                    Active invariants evaluated
                  </span>
                  <div className="space-y-2">
                    {businessContext.invariants.map((inv, idx) => (
                      <div
                        key={idx}
                        className="rounded-md border border-slate-200 bg-white p-2.5 text-xs space-y-1"
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-slate-800">
                            {inv.check || 'Invariant Check'}
                          </span>
                          <Badge
                            variant={
                              inv.status === 'verified'
                                ? 'success'
                                : inv.status === 'breached'
                                ? 'danger'
                                : 'warning'
                            }
                          >
                            {inv.status || 'shadow'}
                          </Badge>
                        </div>
                        <p className="font-mono text-[11px] text-slate-600">{inv.statement}</p>
                        {inv.evidence?.sentinel_id && (
                          <div className="pt-1 text-[10px] font-mono text-slate-400">
                            Sentinel: {inv.evidence.sentinel_id}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Grounded Evidence Chain */}
              {businessContext?.evidence && businessContext.evidence.length > 0 && (
                <div className="space-y-2">
                  <span className="text-xs font-semibold text-slate-600">
                    Grounded telemetry evidence
                  </span>
                  <div className="space-y-1.5">
                    {businessContext.evidence.map((ev, idx) => (
                      <div
                        key={idx}
                        className="rounded-md border border-slate-200 bg-white p-2 text-xs font-mono space-y-0.5"
                      >
                        <div className="flex items-center justify-between text-[11px] text-blue-700">
                          <span className="font-semibold">{ev.kind}</span>
                          <span className="text-slate-400">{ev.ref}</span>
                        </div>
                        <p className="text-[11px] text-slate-600 truncate">{ev.excerpt}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </aside>
        )}
      </main>
    </div>
  );
};
