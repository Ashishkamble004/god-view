/**
 * Globe scene tokens for three.js (WebGL cannot read CSS variables cheaply).
 * Mirrors the --gv-space-* / --gv-node-* / motion tokens in tokens.css.
 * Owner: design-lead. Spec: DESIGN.md section 7 (Globe stage).
 * The stage is navy ink in BOTH themes; these values do not switch with theme.
 */

export const GLOBE_COLORS = {
  spaceCore: 0x0d2647,
  spaceMid: 0x081a33,
  spaceEdge: 0x040b17,
  ocean: 0x0a1f3d,
  oceanLit: 0x123563,
  graticule: 0xa8c7fa, // draw at opacity GLOBE_STYLE.graticuleOpacity
  atmosphere: 0x4c8df6, // fresnel rim
  star: 0xffffff,
  arc: 0x8ab4f8,
  arcParticle: 0xe8f0fe,
  arcError: 0xf28b82,
  arcJourney: 0xa8c7fa,
  selectedRing: 0x8ab4f8,
  onSpace: 0xe8eef7,
  onSpaceVariant: 0xa9b8cc,
} as const;

/** Node fill by topology status. Only state drives colour. */
export const NODE_STATUS_COLORS = {
  green: 0x5bb974,
  amber: 0xfcc934,
  red: 0xee675c,
  unknown: 0x9aa0a6,
} as const;

/** Agents rendered as satellites use teal (tertiary), not a status colour. */
export const AGENT_COLORS = { awake: 0x75d5e2, asleep: 0x5f6b7a } as const;

/** Geometry, in globe-radius units (R = 1). */
export const GLOBE_STYLE = {
  radius: 1,
  graticuleStepDeg: 15,
  graticuleOpacity: 0.1,
  atmosphereScale: 1.06,
  atmosphereOpacity: 0.35,
  starCount: 1400,
  starOpacityMax: 0.55,
  /** Node marker radius by kind weight; scale by sqrt(rps) up to 1.6x when measured. */
  nodeRadius: { base: 0.018, important: 0.024, satellite: 0.014 },
  nodeHaloScale: 2.2, // soft additive halo sprite around each node
  nodeHaloOpacity: 0.35,
  selectedRingScale: 2.8,
  selectedRingWidth: 0.004,
  /** Arcs: great-circle with apex lift proportional to angular distance. */
  arcLiftMin: 0.04,
  arcLiftPerRadian: 0.18,
  arcWidth: 0.0035,
  arcOpacity: 0.45,
  arcDimmedOpacity: 0.08,
  particleSize: 0.009,
  particlesPerArcMax: 3,
  /** External satellites orbit outside the sphere. */
  satelliteOrbitRadius: 1.28,
  /** Camera */
  cameraFovDeg: 35,
  cameraDistanceIdle: 3.6,
  cameraDistanceFocus: 2.4,
  /** Shift the look-at target left when the side sheet is open (fraction of stage width). */
  sheetOpenTargetOffsetX: -0.14,
} as const;

/** Tier latitude bands (degrees). Matches the cockpit engine in GlobeView.tsx. */
export const TIER_LATITUDE: Record<string, number> = {
  observer: 78,
  edge: 55,
  messaging: 35,
  external: 20, // drawn as satellites on satelliteOrbitRadius
  compute: 8,
  data: -42,
  platform: -68,
};

export const GLOBE_MOTION = {
  idleRotationDegPerSec: 1.5,
  idleResumeAfterMs: 8000,
  flyToMs: 1200,
  /** M3 emphasized easing for camera moves, as a cubic-bezier tuple. */
  flyToEasing: [0.2, 0, 0, 1] as const,
  pulsePeriodMs: 1600,
  pulseMaxScale: 2.4,
  pulseRings: 2,
  arcLoopMs: 2400, // baseline; speed scales with log10(rps + 1), clamp 0.5x..3x
  hoverScale: 1.35,
  hoverMs: 150,
  journeyDrawMs: 900, // path reveal along the journey
} as const;

/** Read once at startup and on change. */
export function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}
