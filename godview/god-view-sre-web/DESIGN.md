---
name: God-View Material 3 Expressive
colors:
  primary: '#1A73E8'
  on-primary: '#FFFFFF'
  primary-container: '#D3E3FD'
  on-primary-container: '#041E49'
  inverse-primary: '#A8C7FA'
  surface-tint: '#1A73E8'
  primary-fixed: '#D3E3FD'
  primary-fixed-dim: '#A8C7FA'
  on-primary-fixed: '#041E49'
  on-primary-fixed-variant: '#0842A0'
  secondary: '#00639B'
  on-secondary: '#FFFFFF'
  secondary-container: '#C2E7FF'
  on-secondary-container: '#001D35'
  secondary-fixed: '#C2E7FF'
  secondary-fixed-dim: '#7FCFFF'
  on-secondary-fixed: '#001D35'
  on-secondary-fixed-variant: '#004A77'
  tertiary: '#006972'
  on-tertiary: '#FFFFFF'
  tertiary-container: '#C2F7FF'
  on-tertiary-container: '#001F23'
  tertiary-fixed: '#C2F7FF'
  tertiary-fixed-dim: '#75D5E2'
  on-tertiary-fixed: '#001F23'
  on-tertiary-fixed-variant: '#004F56'
  error: '#D93025'
  on-error: '#FFFFFF'
  error-container: '#FCE8E6'
  on-error-container: '#A50E0E'
  background: '#F8FAFD'
  on-background: '#1F1F1F'
  surface: '#FFFFFF'
  surface-dim: '#D8DEE7'
  surface-bright: '#FFFFFF'
  surface-container-lowest: '#FFFFFF'
  surface-container-low: '#F8FAFD'
  surface-container: '#F0F4F9'
  surface-container-high: '#E9EEF6'
  surface-container-highest: '#DDE3EA'
  surface-variant: '#DDE3EA'
  on-surface: '#1F1F1F'
  on-surface-variant: '#444746'
  inverse-surface: '#2E3136'
  inverse-on-surface: '#F1F4F8'
  outline: '#747775'
  outline-variant: '#C4C7C5'
  warning: '#F9AB00'
  warning-container: '#FEF7E0'
  on-warning-container: '#7A4E00'
  healthy: '#188038'
  healthy-container: '#E6F4EA'
  on-healthy-container: '#0D652D'
  unknown: '#9AA0A6'
  space-core: '#0D2647'
  space-mid: '#081A33'
  space-edge: '#040B17'
typography:
  display-lg:
    fontFamily: Google Sans Display
    fontSize: 57px
    fontWeight: '400'
    lineHeight: 64px
    letterSpacing: -0.25px
  display-sm:
    fontFamily: Google Sans Display
    fontSize: 36px
    fontWeight: '400'
    lineHeight: 44px
    letterSpacing: '0'
  metric-xl:
    fontFamily: Google Sans Display
    fontSize: 44px
    fontWeight: '400'
    lineHeight: 48px
    letterSpacing: '0'
  headline-sm:
    fontFamily: Google Sans
    fontSize: 24px
    fontWeight: '400'
    lineHeight: 32px
    letterSpacing: '0'
  title-lg:
    fontFamily: Google Sans
    fontSize: 22px
    fontWeight: '400'
    lineHeight: 28px
    letterSpacing: '0'
  title-md:
    fontFamily: Google Sans
    fontSize: 16px
    fontWeight: '500'
    lineHeight: 24px
    letterSpacing: 0.15px
  body-lg:
    fontFamily: Roboto
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
    letterSpacing: 0.5px
  body-md:
    fontFamily: Roboto
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
    letterSpacing: 0.25px
  label-lg:
    fontFamily: Google Sans Text
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
    letterSpacing: 0.1px
  label-md:
    fontFamily: Google Sans Text
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.5px
rounded:
  xs: 8px
  sm: 12px
  md: 16px
  DEFAULT: 20px
  lg: 20px
  xl: 28px
  full: 9999px
spacing:
  unit: 4px
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
  gutter: 8px
  stage-inset: 24px
  appbar: 64px
  rail: 80px
  sheet: 400px
---

# God-View Cockpit Design System (v2, Material 3 Expressive)

Owner: design-lead. Implementer: cockpit. Tokens live in
[`src/theme/tokens.css`](src/theme/tokens.css) (CSS custom properties, light + dark),
[`src/theme/tailwind-preset.ts`](src/theme/tailwind-preset.ts) (Tailwind mapping),
[`src/theme/globe.ts`](src/theme/globe.ts) (three.js constants) and
[`src/theme/fonts.css`](src/theme/fonts.css). The Stitch reference project is listed in section 12.

The YAML front matter above is Stitch compatible (it is uploaded as the Stitch design system).

## 0. Design Read and Dials

*   **Signals:** Enterprise SRE & autonomous organism cockpit (`dashboard` + `AI experience` + `data-heavy tool`), audience: Bank CTOs, Principal SREs, and Platform Operators; vibe words: calm, authoritative, high-trust, glanceable, zero-slop; quiet constraints: regulated financial-grade observability, strict accessibility (WCAG AA contrast, keyboard navigation, `prefers-reduced-motion` compliance), honest telemetry ("Not measured yet" for nulls, never fabricated numbers).
*   **Reading:** Reading this as: an enterprise SRE & autonomous organism console for Bank CTOs and platform engineers, with a calm Material 3 Expressive tonal language, on GM3 at dials `3 / 4 / 6`. Creative idea: a unified Estate Topology & Service Mesh canvas paired with a slide-over Business Context & Call-Graph Inspector sheet and an interactive Sovereign & Specialist Court Org-Chart where every node, invariant, and trust-gated capability is one click away.
*   **Dials:** `VARIANCE 3 / MOTION 4 / DENSITY 6`, preset `Data-heavy tool` (`3 / 3 / 6`), adjusted to `MOTION 4` because state transitions, incident pulse rings, and slide-over inspector sheets benefit from M3 Expressive spatial easing (`--md-sys-motion-easing-emphasized-decelerate`) while disabling all decorative motion under `prefers-reduced-motion: reduce`.
*   **Layout:** Overview + KPI strip + multi-mode Estate Topology canvas (`Service Mesh DAG`, `Domain Clusters`, `Full Estate Matrix`) with a slide-over right-hand Inspector Sheet (`DetailSheet`) and an opt-in Chaos Drills Drawer.
*   **Structural Shell & GM3 Surface Ladder:**
    *   **App Frame (`.gm3-app`):** `--md-sys-color-surface-container-low` (`#F8FAFD` light / `#141A22` dark) with `--md-sys-color-on-surface` primary text.
    *   **Top App Bar & Navigation Bar (`.gm3-app-bar`):** `--md-sys-color-surface` (`#FFFFFF` light / `#0F141B` dark) with `1px solid var(--md-sys-color-outline-variant)` bottom border, pill status badges (`var(--md-sys-shape-corner-full)`), and `2px` `--md-sys-color-primary` active tab indicator.
    *   **Primary Pane / Card (`.gm3-pane`):** `--md-sys-color-surface` (`#FFFFFF` light / `#0F141B` dark) with `1px solid var(--md-sys-color-outline-variant)` border (per the Outlined Rule on the base canvas), `var(--md-sys-shape-corner-large)` (`20px`) or `var(--md-sys-shape-corner-extra-large)` (`28px`) corners, no drop shadow on static panes.
    *   **Nested Group / Tile (`.gm3-pane-group`):** Steps one tone up to `--md-sys-color-surface-container-low` (`#F8FAFD`) or `--md-sys-color-surface-container` (`#F0F4F9`) with `var(--md-sys-shape-corner-small)` (`12px`) or `var(--md-sys-shape-corner-medium)` (`16px`) corners; never card-in-card-in-card.
*   **Anti-Slop Self-Check:**
    *   [x] UI spec starts with Design Read and V/M/D dials (`3 / 4 / 6`).
    *   [x] Shell follows the GM3 surface ladder (`surface-container-low` frame -> `surface` pane with `1px solid outline-variant` -> `surface-container-low` nested group).
    *   [x] Typography uses Google Sans (`--gv-font-title`), Google Sans Text (`--gv-font-label`), Roboto (`--gv-font-body`), and Google Sans Mono (`--gv-font-mono`) with weights `400` and `500` (`font-medium`) only; tabular numerals (`tabular-nums`) on all metrics.
    *   [x] Button emphasis hierarchy enforced: at most one Filled (`primary`) CTA per view/card, paired with Tonal/Outlined (`secondary`) and Text (`ghost`) actions; pill geometry (`rounded-full`).
    *   [x] Zero hardcoded hex literals in application CSS/TSX outside `src/theme/tokens.css`; verified by `audit_gm3.py`.

## 1. Brand and intent

God-View is the flagship screen a bank CTO sees. It must feel calm, expensive and certain.

- **Visual first.** A 3D globe of the discovered estate owns the screen. Health is read from colour and
  motion before a single word is read.
- **Glanceable.** Every card says one thing. Numbers are display type with a sparkline. Labels are chips.
- **Progressive disclosure.** Detail lives behind a selection (side sheet), an expand row, a hover, or a
  detail route. Nothing is shown "just in case".
- **Honest.** Unknown is shown as unknown ("Not measured yet"), never as a plausible number. Mock or sample
  data always carries a visible "Sample data" chip in the app bar.
- **Material 3 Expressive.** Tonal surfaces, generous radii, pill controls, emphasized motion, springy
  but restrained feedback. Google Blue is the only brand colour; red, yellow and green only ever mean state.

**Forbidden everywhere:** purple, violet, indigo, fuchsia, magenta, lavender; neon or rainbow gradients;
the four-colour Google stripe as decoration; 1 px grey grids and table borders; dividers between every row;
red bars across the globe; paragraphs inside cards; em-dashes in UI copy.

## 2. Colour

### 2.1 Roles (light is the default theme)

| Role | Light | Dark | Use |
|---|---|---|---|
| primary | `#1A73E8` | `#A8C7FA` | Filled buttons, links, selection ring, focus ring, active tab underline |
| on-primary | `#FFFFFF` | `#062E6F` | Text and icons on primary |
| primary-container | `#D3E3FD` | `#0842A0` | Tonal buttons, selected chip fill, avatar circles for nodes |
| on-primary-container | `#041E49` | `#D3E3FD` | |
| secondary | `#00639B` | `#7FCFFF` | Rarely as a fill; secondary icons |
| secondary-container | `#C2E7FF` | `#004A77` | Navigation rail active indicator, selected list row |
| on-secondary-container | `#001D35` | `#C2E7FF` | Active rail icon and label |
| tertiary | `#006972` | `#75D5E2` | Agents, AI, "Ask" FAB icon, agent sparklines |
| tertiary-container | `#C2F7FF` | `#004F56` | Agent chips, "Ask" FAB fill, swarm message bubbles |
| on-tertiary-container | `#001F23` | `#C2F7FF` | |
| error (incident) | `#D93025` | `#F28B82` | Incident numerals, incident icon, Reject focus |
| error-container | `#FCE8E6` | `#8C1D18` | Incident card icon circle, incident chip |
| on-error-container | `#A50E0E` | `#F9DEDC` | Text on incident chip |
| warning | `#F9AB00` | `#FDD663` | Approval badges, degraded status dots (fill only) |
| warning-text | `#B06000` | `#FDD663` | Warning used as text on a light surface (AA) |
| warning-container | `#FEF7E0` | `#4F3400` | Approval chip, degraded chip |
| on-warning-container | `#7A4E00` | `#FFDFA0` | |
| healthy | `#188038` | `#81C995` | Healthy dots, success icon |
| healthy-container | `#E6F4EA` | `#0F5223` | Healthy chip |
| on-healthy-container | `#0D652D` | `#C4EED0` | |
| unknown | `#9AA0A6` | `#9AA0A6` | Unknown status dot, "Not measured yet" icon |

### 2.2 Surface tiers (no borders: hierarchy comes from tone)

| Token | Light | Dark | Paints |
|---|---|---|---|
| background / surface-container-low | `#F8FAFD` | `#141A22` | App chrome: top app bar, navigation rail, the 8 px gutter around the stage |
| surface / surface-container-lowest | `#FFFFFF` | `#0A0E14` (lowest), `#0F141B` (surface) | Light: floating cards, side sheet, pulse bar, incident card, filters over the globe |
| surface-container | `#F0F4F9` | `#18202A` | Search bar, tiles inside the sheet, list-detail panes |
| surface-container-high | `#E9EEF6` | `#212A35` | Dark theme floating cards and sheet; hover fills on light |
| surface-container-highest | `#DDE3EA` | `#2B3541` | Pressed fills, sparkline track, progress track |
| on-surface | `#1F1F1F` | `#E3E6EA` | Primary text |
| on-surface-variant | `#444746` | `#C4C7CC` | Secondary text, inactive icons |
| outline | `#747775` | `#8E9196` | Outlined button stroke and outlined chip stroke only |
| outline-variant | `#C4C7C5` | `#434850` | Only where a separator is unavoidable (inside dense lists, max one per group) |

The convenience aliases `--gv-chrome-bg`, `--gv-floating-bg` and `--gv-tile-bg` switch automatically with the theme.

### 2.3 The space stage (same in both themes)

The globe lives in a deep navy ink field. It is never purple, never violet tinted, never black.

- Background: `radial-gradient(120% 100% at 55% 45%, #0D2647 0%, #081A33 45%, #040B17 100%)` (`--gv-space-gradient`).
- Sphere ocean `#0A1F3D`, key-light side `#123563`, graticule `#A8C7FA` at 10% alpha, atmosphere rim `#4C8DF6` at 35%.
- Stars: 1,400 points, white, opacity 0.1 to 0.55, size 0.6 to 1.6 px, no twinkle.
- Arcs `#8AB4F8` at 45%; flowing particles `#E8F0FE`; erroring edges `#F28B82`; a highlighted journey `#A8C7FA` at 100%.
- Node fills are the **on-space** lifts of the state colours (Google 400 tones): healthy `#5BB974`, warning
  `#FCC934`, incident `#EE675C`, unknown `#9AA0A6`; agent satellites teal `#75D5E2` (awake) or `#5F6B7A` (asleep).
- Labels drawn on the stage: `#E8EEF7`, secondary `#A9B8CC`, label-md, with a 2 px `#040B17` 60% text shadow.

### 2.4 Rules

1. State colour appears only where there is state: a dot, a chip, a numeral, a node. Never as a section header or decoration.
2. A screen may show at most one red element outside the globe (the incident card icon or the incident numeral) plus the red node.
3. Blue means "you can act" or "this is selected". Do not use blue for status.
4. Text on yellow is always `on-warning` (`#1F1F1F`). Yellow is never used as text on white; use `warning-text`.
5. Hover, focus and pressed are state layers of the content colour at 8%, 10% and 10% (`--md-sys-state-*`), not new colours.

## 3. Typography

| Token | Family | Size / line | Weight | Tracking | Use |
|---|---|---|---|---|---|
| display-lg | Google Sans Display | 57 / 64 | 400 | -0.25 | Empty-state hero numeral only |
| display-md | Google Sans Display | 45 / 52 | 400 | 0 | Incident timer in war-room header |
| display-sm | Google Sans Display | 36 / 44 | 400 | 0 | Trust level numeral |
| metric-xl | Google Sans Display | 44 / 48 | 400 | 0 | Stat-card numerals |
| metric-lg | Google Sans Display | 32 / 36 | 400 | 0 | Golden-signal tiles in the side sheet |
| metric-md | Google Sans Display | 24 / 28 | 400 | 0 | Compact tiles, list trailing numerals |
| metric-unit | Google Sans Text | 14 / 20 | 500 | 0.1 | Units after numerals ("ms", "%", "/12"), on-surface-variant |
| headline-sm | Google Sans | 24 / 32 | 400 | 0 | Route titles (Approvals, Agents) |
| title-lg | Google Sans | 22 / 28 | 400 | 0 | Side sheet title, product name in app bar |
| title-md | Google Sans | 16 / 24 | 500 | 0.15 | Card titles |
| title-sm | Google Sans | 14 / 20 | 500 | 0.1 | Section labels inside the sheet |
| body-lg | Roboto | 16 / 24 | 400 | 0.5 | Rare; dialogs |
| body-md | Roboto | 14 / 20 | 400 | 0.25 | The one-sentence summaries |
| body-sm | Roboto | 12 / 16 | 400 | 0.4 | Timestamps, "Not measured yet" |
| label-lg | Google Sans Text | 14 / 20 | 500 | 0.1 | Buttons, tabs, search placeholder |
| label-md | Google Sans Text | 12 / 16 | 500 | 0.5 | Chips, rail labels, pulse-bar labels |
| label-sm | Google Sans Text | 11 / 16 | 500 | 0.5 | Badges, legend |

- All numerals use `font-variant-numeric: tabular-nums` (`.gv-numeric`).
- Sentence case everywhere. No ALL CAPS labels, no letter-spaced eyebrow text.
- Max line length for any body copy: 56 characters; cards are one sentence (max two lines, then ellipsis + tooltip).
- Monospace (`--gv-font-mono`) only for IDs, capability ids and log excerpts inside expanded evidence.

## 4. Shape

| Token | Radius | Components |
|---|---|---|
| extra-small | 8 px | Tooltips, menus, sparkline tooltip |
| small | 12 px | Tiles inside the side sheet, text fields, evidence rows on hover |
| medium | 16 px | FAB ("Ask"), small cards, list-item hover shape, segmented control |
| large | 20 px | Incident card, approval cards, stat cards (Cost), all standard cards |
| large-increased | 24 px | Compact-width bottom sheet top corners |
| extra-large | 28 px | The globe stage, side sheets, dialogs, list-detail panes |
| full | pill | Buttons, chips, search bar, nav rail active indicator (56 x 32), icon-button groups, badges |

Nested radii: inner radius = outer radius minus padding (e.g. 28 px sheet with 16 px padding hosts 12 px tiles).

## 5. Elevation

Tonal first. Shadows only for elements that float over the globe or over content.

| Level | Where | Shadow token |
|---|---|---|
| 0 | Chrome (app bar, rail), stage, tiles | none |
| 1 | Chips over the globe, icon-button pill | `--md-sys-elevation-1` |
| 2 | Pulse bar, incident card | `--md-sys-elevation-2` |
| 3 | Side sheet, menus, search suggestions | `--md-sys-elevation-3` |
| 4 | Snackbar | `--md-sys-elevation-4` |
| 5 | Dialogs | `--md-sys-elevation-5` |

Over the navy stage, add `--gv-elevation-over-space` to floating cards so they lift off the dark field.
The app bar never gets a shadow or a bottom divider; when content scrolls beneath it (list routes), switch its fill to
`surface-container` over 200 ms.

## 6. Motion

M3 Expressive. Motion explains change; it never decorates chrome.

| Pattern | Duration | Easing | Notes |
|---|---|---|---|
| Side sheet enter | 500 ms | emphasized-decelerate | translateX(24px) + fade; globe target shifts left in parallel |
| Side sheet exit | 200 ms | emphasized-accelerate | |
| Incident card enter / exit | 400 / 200 ms | emphasized-decelerate / accelerate | rises 16 px from below |
| Chip select, toggle | 350 ms | spring-fast | 4% overshoot on the check icon |
| Rail indicator | 400 ms | spring-default | pill grows from centre (width 0 to 56) |
| Card expand | 300 ms | emphasized | height + chevron rotate 180 deg |
| Numeral change | 600 ms | emphasized | count-up from previous value, never from 0 after first paint |
| Camera fly-to on select | 1200 ms | emphasized `[0.2, 0, 0, 1]` | slerp orientation, dolly from 3.6R to 2.4R |
| Idle globe rotation | continuous | linear | 1.5 deg/s (one turn per 4 min); pause on pointer down, hover, or sheet open; resume 8 s after last interaction |
| Arc flow | 2400 ms per traverse | linear | speed x clamp(log10(rps+1), 0.5, 3); max 3 particles per arc; no particles when rps is null |
| Incident pulse | 1600 ms period | emphasized-decelerate | 2 rings, scale 1 to 2.4, opacity 0.6 to 0 |
| Journey reveal | 900 ms | emphasized | path draws from first to last step, other nodes dim to 20% |
| Hover node | 150 ms | standard | scale 1.35 + label fade-in |

**Reduced motion** (`prefers-reduced-motion: reduce`): no idle rotation; no particles (arcs render as static
dashes, 2 px dash, 6 px gap); fly-to becomes an instant cut with a 150 ms crossfade; incident pulse becomes a
static double ring; count-ups disabled; sheet and incident card use a 150 ms fade only. If WebGL is unavailable the
cockpit's existing 2D tier matrix fallback is used with the same colours and chips.

## 7. Globe stage specification

- Container: the stage, radius 28 px, `overflow: hidden`, background `--gv-space-gradient`.
- Camera: perspective, FOV 35 deg, idle distance 3.6R, focus distance 2.4R. Globe centre sits at 50% x / 52% y of the
  visible stage; when the side sheet is open the look-at target shifts so the globe centres in the remaining area
  (`sheetOpenTargetOffsetX = -0.14`).
- Sphere: radius 1, ocean `#0A1F3D` with lambert key light from upper-left giving `#123563`; graticule every 15 deg at 10%;
  fresnel atmosphere shell at 1.06R, `#4C8DF6`, 35%. No continents (the estate is abstract, not geographic).
- Placement: tier latitude bands (observer 78, edge 55, messaging 35, compute 8, data -42, platform -68 deg) and
  domain longitude clusters (`business.domain`), as already built in `GlobeView.tsx`. External nodes and awake agents
  are satellites on a 1.28R orbit.
- Node marker: sphere radius 0.018R (0.024R for `lb_frontend`, `cloud_sql`, `api_gateway`), additive halo sprite
  at 2.2x, 35%. Fill by `status` (section 2.3). `incidents_open > 0` adds the pulse rings.
- Selection: ring 2.8x node radius, `#8AB4F8`, 0.004R stroke, plus the node's label pinned.
- Labels: hidden by default; shown on hover, for the selected node, for nodes with incidents, and for all nodes on the
  current journey. Never more than 12 labels visible; collision-avoid by dropping lower-tier labels.
- Edges: great-circle tubes, apex lift 0.04R + 0.18R per radian. Opacity 45%; red when `error_ratio >= 0.02`;
  dimmed to 8% when another node or journey is focused.
- Interaction: drag to orbit (damped), wheel / pinch to zoom 1.8R..5R, click node to select (fly-to + sheet), click
  empty space to deselect (sheet closes, camera returns to idle distance), `Esc` closes the sheet, arrow keys cycle
  nodes within a tier, `/` focuses search.

## 8. Layout and grid

### 8.1 Expanded (>= 1240 px wide, reference 1440 x 900)

```
x:0          80                                                              1432 1440
y:0  +-------+-----------------------------------------------------------------------+
     | logo  |  God-View  [sample-env]     ( Search services, journeys, incidents ) [Sample data] (theme)(bell)(avatar) |  app bar 64
 64  +-------+-------------------------------------------------------------------+---+
     | (Ask) | STAGE  x 80..1432  y 64..892  radius 28                            | 8 |
     |       |  inset 24: [ All | Edge | Compute | Data | Messaging ]  +---------+ |   |
     | Globe |                                                       |  side   | |   |
     | Jrnys |              (globe, idle rotation, centred in        |  sheet  | |   |
     | Incid |               the free area left of the sheet)        |  400w   | |   |
     | Apprv |                          [incident card, 1 line] [ctl]|  inset  | |   |
     | Agents|        ( Services 24 ~ | Incidents 1 ~ | ... )   [ctl]|  16     | |   |
     | Cost  |        pulse bar, bottom-centre of the free area      +---------+ |   |
     | (gear)|                                                                    |   |
 892 +-------+--------------------------------------------------------------------+---+
 900                                   8 px gutter
```

- Stage area = 1352 x 828 = **86.9%** of a 1440 x 900 viewport. The side sheet floats inside the stage, so the globe
  canvas never shrinks below the 70% bar.
- Overlay safe area inside the stage: 24 px on every side. The "free area" is the stage minus the open sheet
  (`400 + 16` px on the right). The globe, pulse bar, incident card and map controls all centre or align inside the
  free area and slide with the sheet animation (500 ms, emphasized-decelerate).
- Chosen globe-home layout: **Cinematic** (Stitch variant A, see section 12). Rationale: the globe gets the largest
  unobstructed disc (about 600 px at 1440 x 900, versus about 520 px in the stacked-card "Command" variant), the four
  metrics collapse into one 64 px pill instead of four 96 px cards (about 60% less overlay area over the space
  field), the tier filters sit in one container so they can never collide with the incident message, and the incident
  card sits next to the controls where the hand already is. The Command variant read like a dashboard with a globe
  behind it; Cinematic reads like a globe with instruments on it, which is the brief.

### 8.2 Medium (840 to 1239 px)

Rail stays (80 px). Search collapses to an icon button that expands into a full-width search bar overlay.
The pulse bar keeps all metrics but drops sparklines below 1000 px. Side sheet 360 px.

### 8.3 Compact (< 840 px: Jetski aux pane, mobile)

- Top app bar 56 px: logo, environment chip, overflow menu. Rail is replaced by a bottom **navigation bar** (80 px,
  5 destinations, Cost moves into overflow).
- Stage is full width with 0 gutter and 0 radius on the left/right; the pulse bar becomes a horizontally scrollable
  pill (metric-md numerals, no sparklines).
- The side sheet becomes a **modal bottom sheet** (radius 24 top corners, drag handle, peek height 40% of the viewport,
  expands to 90%).
- The incident card becomes a full-width card at the top of the stage, 12 px inset.

## 9. Components (inventory and exact specs)

All interactive elements have 48 x 48 px minimum touch targets, a 3 px `primary` focus ring with 2 px offset, and
M3 state layers.

1. **Top app bar** (`AppBar`). Height 64, fill `--gv-chrome-bg`, padding 0 16 0 0. Leading 80 px slot centred over the
   rail holds the logo mark (40 px circle, `primary` fill, `public` icon in on-primary, 24 px). Title "God-View"
   title-lg on-surface, 4 px from the logo slot. Environment assist chip 32 px tall: 8 px status dot (healthy /
   warning / incident of the environment) + `env_id` label-md. Centre: search bar. Trailing (8 px gaps): "Sample data"
   outlined chip (only in mock mode), system-state chip: "Live" (pulsing 6 px healthy dot, SSE connected
   and backend healthy), "Degraded" (`warning-container`, shown whenever the backend reports any degraded adapter,
   model or executor; click opens a menu listing what is degraded, one line each; per plan section 0 there is no silent
   fallback, so the UI must surface it), "Reconnecting" (unknown dot, SSE down), theme icon button (`dark_mode` / `light_mode`),
   notifications icon button with badge, avatar 32 px.
2. **Search bar** (`SearchBar`). Pill, 48 tall, 480 wide, fill `surface-container`, leading `search` icon, label-lg
   placeholder "Search services, journeys, incidents", trailing `/` keyboard hint chip. Focus: fill
   `surface-container-lowest`, elevation 3, results menu below (28 px radius) grouped by Services / Journeys /
   Incidents, each row an icon + one line.
3. **Navigation rail** (`NavRail`). Width 80, fill `--gv-chrome-bg`, items start 8 px below the app bar. FAB "Ask"
   56 x 56, radius 16, fill `tertiary-container`, icon `auto_awesome` on-tertiary-container, label "Ask" below
   (label-md), opens the agent query dialog. Destinations, 12 px apart: icon 24 inside a 56 x 32 pill indicator,
   label-md 4 px below. Active: indicator `secondary-container`, filled icon in on-secondary-container, label-md in on-surface. Inactive: icon and label on-surface-variant. Badges: 16 px pill, label-sm, `error` / `warning`
   fill, positioned top-right of the icon. Destinations and icons:
   Globe `public`, Journeys `route`, Incidents `crisis_alert`, Approvals `approval_delegation`, Agents `neurology`,
   Cost `savings`. Settings `settings` pinned 16 px from the bottom.
4. **Stage** (`Stage`). See section 7 and 8.
5. **Tier filter** (`TierFilter`). One floating pill container, 48 tall, radius full, fill `--gv-floating-bg`,
   elevation 1 + over-space, padding 4, top-left of the stage (24 px inset). Inside: borderless filter segments 40 tall,
   radius full, label-lg, 16 px horizontal padding. Selected: fill `secondary-container`, text on-secondary-container,
   leading `check` 18 px (spring-fast). Options: All, Edge, Compute, Data, Messaging, External (External drops into
   an overflow `more_horiz` segment below 1240 px). Selecting a tier dims other tiers to 20% and their arcs to 8%.
6. **Pulse bar** (`PulseBar`, replaces stat cards on every stage route). One floating pill, 64 tall, radius full,
   fill `--gv-floating-bg`, elevation 2 + over-space, padding 8 28, centred horizontally in the free area, 24 px above
   the stage bottom. 2 to 4 metric segments, 32 px apart, **no dividers**. Each segment: label-md on-surface-variant
   (sentence case, one or two words) above a metric-lg numeral (+ optional metric-unit suffix such as "/12"), and a
   64 x 24 sparkline to the right of the numeral. Numeral colour: on-surface, or `error` / `warning-text` / `tertiary`
   when the metric is a state count above zero. Sparkline: 1.5 px stroke in the numeral colour, last point a 3 px dot,
   area fill at 12%. Unknown: numeral replaced by "Not measured yet" (body-sm, on-surface-variant), no sparkline.
   Each segment is a button (hover state layer, radius full) that navigates to the matching route.
   Globe home: Services, Incidents, Approvals, Agents awake. Journeys: Steps, Confidence. Agents: Agents awake,
   Skills, Versions promoted.
   (`StatCard` 168 x 96, radius 20, same anatomy stacked vertically, is kept only for list-detail routes such as Cost.)
7. **Map controls** (`MapControls`). Vertical pill 48 wide, fill `--gv-floating-bg`, elevation 1, 4 icon buttons
   (`add`, `remove`, `center_focus_strong`, `view_in_ar` / `grid_view` for 3D / 2D), bottom-right of the free area
   (24 px insets). Long-press or `?` opens a legend popover listing the four status dots with one word each.
8. **Incident card** (`IncidentBanner`, banner/snackbar hybrid). Floating at the bottom-right of the free area,
   right-aligned with the map controls and 16 px above the pulse bar's top edge, max 400 wide, 56 tall, radius 20,
   fill `--gv-floating-bg`, elevation 2 + over-space, padding 8 8 8 12. Leading 32 px circle `error-container` with
   `crisis_alert` (filled, 20 px) in `error`. Text: exactly one line, title-sm, "Incident open on {node}" (ellipsis +
   tooltip if long). Actions: text button "Open war room" (primary) and a `close` icon button. Multiple incidents:
   "{n} incidents open" + "Review". Pending approvals use the same card with a `warning-container` circle and
   `approval_delegation` icon when no incident is open. Dismiss hides until the next `incident.opened`. Enter:
   translateY(16px) + fade, 400 ms emphasized-decelerate.
9. **Snackbar** (`Snackbar`). Inverse surface, radius 8 (M3), bottom centre of the stage, for confirmations
   ("Approved. Remediation is running.") with an optional action. 4 s, or until action.
10. **Side sheet** (`DetailSheet`). Floating inside the stage, width 400 (480 for the war-room), inset 16 from the stage
    top, right and bottom, radius 28, fill `--gv-floating-bg`, elevation 3 + over-space, padding 24, internal scroll
    with the header and footer pinned. Opens only on selection. Anatomy for a node:
    - Header (56): 40 px circle `primary-container` with the kind icon (section 9.17) in on-primary-container;
      title-lg name (1 line, ellipsis); kind chip (label-md, outlined); close icon button.
    - Status chips row (8 px gaps): status chip (Healthy / Degraded / Incident / Unknown in the matching container
      colours, leading dot), tier chip, confidence chip with `neurology` icon in tertiary showing the band word,
      "Strong" (>= 0.8), "Moderate" (0.5 to 0.79), "Weak" (0.3 to 0.49), "Insufficient" (< 0.3, outlined chip).
      Hover tooltip, one line each: "Confidence 0.82", "Based on {n} contract items, {m} behavioural items",
      "{k} claims verified, {d} dropped" (only the lines the API provides).
    - Summary: `business.role_summary`, body-md, max 2 lines.
    - Golden signals: 3 tiles in a row (each 109 x 88, radius 12, fill `--gv-tile-bg`, padding 12): label-md, metric-lg
      numeral + unit, sparkline 85 x 16. Null values render "Not measured yet" in body-sm on-surface-variant with an
      `help` icon tooltip "No telemetry source found for this signal yet."
    - Journeys: chips (assist, leading `route`), click selects the journey.
    - Disclosure rows (56 tall, no dividers, 16 px radius hover shape, trailing `expand_more`): Business context,
      Invariants, Posture, Incidents, Evidence. Expanded content is chips and one-line rows, never paragraphs.
      Posture uses 4 icon chips: Ingress, IAP, Invoker IAM, WAF, each with `check_circle` (healthy) or `error`
      (incident) or `help` (unknown).
    - Footer (pinned, 72): filled button primary action + tonal button secondary action, both pill, 40 tall.
11. **Buttons.** Pill, 40 tall, label-lg, horizontal padding 24 (16 with icon). Filled (primary / on-primary),
    tonal (secondary-container / on-secondary-container), outlined (outline stroke 1 px), text (primary). Destructive
    confirm ("Reject") is outlined with `error` text; never a solid red button.
12. **Chips.** 32 tall, radius full, label-md. Assist (outlined), filter (selected = secondary-container), input,
    status (container colour + 8 px dot, no stroke).
13. **List row** (`ListRow`). 56 or 72 tall, no dividers, 16 px radius hover state layer, leading 40 px icon circle,
    one-line title-sm + optional one-line body-sm, trailing chip or metric-md numeral.
14. **Tabs** (`Tabs`, war-room only). Primary tabs, label-lg, 48 tall, active indicator 3 px pill `primary`
    under the label width, no full-width divider.
15. **Stepper** (`JourneyStepper`). Vertical; each step 56 tall: 24 px numbered circle (status colour container with
    number in on-container), 2 px connector in `outline-variant` (the one permitted line), node name title-sm,
    operation chip (mono label), trailing status dot. Selected step: row fill `secondary-container`.
16. **Sparkline** (`Sparkline`). SVG, 1.5 px stroke, round caps, no axes, no gridlines, area fill 12%, last point dot.
    Hover: 8 px radius tooltip with value and time. Empty series: renders nothing (no flat line).
17. **Icons.** Material Symbols Rounded, 24 px (20 in chips, 18 in dense chips), weight 400, fill 0; fill 1 for the
    active rail item and for state icons inside container circles. Node kind icons: cloud_run `cloud`, gke_workload
    `deployed_code`, gce_instance `computer`, cloud_sql `database`, lb_frontend `lan`, backend_service `dns`,
    security_policy `shield`, api_gateway `api`, pubsub_topic `campaign`, pubsub_subscription `inbox`, gcs_bucket
    `folder_open`, bigquery_dataset `table_chart`, firestore_db `storage`, secret `key`, external `public`, agent
    `neurology`.
18. **Trust ladder** (`TrustLadder`). Five rungs as a horizontal segmented track, 12 px tall pill segments with 4 px
    gaps: L0 Observe, L1 Suggest, L2 Approve each, L3 Auto + notify, L4 Autonomous. Filled segments `primary`,
    current rung `primary` with a 20 px white knob, future `surface-container-highest`. Rung labels label-md below.
19. **Agent node** (`AgentGlyph`). 40 px circle `tertiary-container` with `neurology` icon; awake = 2 px tertiary ring
    with a slow 3 s breathing glow; asleep = `surface-container-highest` fill, on-surface-variant icon.
20. **Empty and loading states.** Loading: globe renders the sphere with a slow graticule shimmer and the chip
    "Discovering estate" with a circular progress indicator (indeterminate, 24 px, primary). Empty topology:
    centred card "No services discovered yet" + text button "Run discovery". Errors: snackbar, never a red page.

## 10. Screens

Reference viewport 1440 x 900. All screens share the app bar, rail and stage from section 8.

### 10.1 Globe home (`#globe`)

- Stage overlays: tier filter (top-left), pulse bar (bottom-centre of the free area), incident card (bottom-right,
  only when incidents are open), map controls (bottom-right).
- No side sheet until a node is clicked. On click: fly-to, pinned label, sheet (section 9.10).
- Pulse bar: Services (= node count, on-surface), Incidents (open count, error when > 0), Approvals (pending count,
  warning-text when > 0), Agents awake (`awake/total`, tertiary). Sparklines from the last 24 h of the matching
  counters; omit the sparkline when there is no history yet.
- SSE: `topology.updated` re-lays nodes with a 600 ms emphasized tween; `incident.opened` shows the incident card, starts
  the pulse, and increments the counter with a count-up.

### 10.2 Journey explorer (`#journeys`)

- Stage overlays: a horizontally scrollable **journey chip set** (top-left, max width 60% of the stage); each chip
  has a leading 8 px health dot and the journey name. The pulse bar shows two metrics: "Steps" and "Confidence".
- Selecting a journey: other nodes and arcs dim (20% / 8%), the journey path animates (900 ms reveal) as a brighter
  `#A8C7FA` tube with numbered step badges (20 px circles, label-sm) on each node, camera frames the path.
- Side sheet (journey): header with `route` icon circle, journey name, health chip, confidence chip; one sentence
  summary; the vertical **Journey stepper** (section 9.15); a tile row "End-to-end p95" and "Success" (Not measured
  yet when null); footer "Open first failing step" (filled, only when health is amber or red) + "Share" (tonal).
- Clicking a step flies to that node and highlights the step row; the node sheet is reachable via a trailing
  `open_in_new` icon button on the row.

### 10.3 Incident war-room (`#incidents/{id}`)

- Stage stays full; camera focused on the incident node at 2.4R; the node pulses; the blast radius (nodes within
  one hop) keeps full opacity while the rest dims to 20%.
- The incident card is hidden; its role moves to the **lifecycle strip** inside the sheet header, not on the globe.
- Side sheet wide (480):
  - Header: 40 px `error-container` circle with `crisis_alert`; title-lg one-line incident title; chips: severity
    ("Sev 2", error container), affected node, "Open {duration}" (display-md is reserved for the timer in the strip).
  - Lifecycle strip: 5 dots with labels (Detected, Diagnosed, Proposed, Approved, Resolved), completed = primary,
    current = primary with pulse, future = surface-container-highest.
  - Tabs: Evidence, Swarm, Remediation.
    - Evidence: list rows, each icon + one line + trailing "View" text button (logs `article`, metrics
      `monitoring`, traces `timeline`, config change `difference`). Expanding a row shows a mono excerpt in a
      12 px radius `surface-container` block, max 8 lines.
    - Swarm: compact transcript. Each message: 32 px `AgentGlyph`, agent role name (title-sm), one sentence
      (body-md), timestamp (body-sm). Messages sit in `tertiary-container` 16 px radius bubbles at 12% tint, max
      6 visible then "Show {n} more".
    - Remediation: one proposed action card (radius 20, `surface-container`): capability id (mono chip), target chip,
      blast radius chip, risk chip (warning or healthy container), trust level chip, one-sentence rationale, "Diff"
      expand row.
  - Pinned footer: "Approve" (filled primary) + "Reject" (outlined, error text) + overflow `more_vert`
    (Escalate, Snooze). Approve opens a confirm dialog (radius 28) restating the action in one sentence.

### 10.4 Approvals and trust ladder (`#approvals`)

This route is a **list-detail** layout inside the stage, which switches from space to `surface-container-low` for
this route only (the globe is not the subject here). The stage keeps its 28 px radius.

- Header row inside the stage (inset 24): headline-sm "Approvals", segmented button (Pending / Decided), and a
  trailing chip with the pending count.
- **Trust ladder card** (full width, radius 20, `--gv-floating-bg`, padding 20, height 120): title-md "Trust
  ladder", the 5-rung track (section 9.18), and on the right a display-sm current level ("L2") with label-md
  "Approve each". Per capability family overrides appear as chips under the track (e.g. "edge.* at L1").
- List pane (left, 440 wide, radius 28, `surface-container`): approval cards 88 tall, radius 20, fill
  `--gv-floating-bg`, no borders: leading 40 px `warning-container` circle with the capability icon, title-sm one
  line, chips row (target, risk), trailing age body-sm. Selected card: `secondary-container` fill.
- Detail pane (right, remaining width, radius 28, `--gv-floating-bg`, padding 24): title-lg action title; chips
  (capability mono, risk, trust level required, requested by agent); "What changes" before/after tiles (two 12 px
  radius tiles, label-md "Before" / "After", mono value); a **mini globe** (240 x 240, same space gradient in a 20 px
  radius tile) highlighting affected nodes; one-sentence rationale; footer Approve / Reject as in 10.3.

### 10.5 Agent brain (`#agents`)

- Stage returns to space. Instead of the globe, a **radial agent tree** (Harness tree) is drawn in the same 3D
  scene style: a central orchestrator `AgentGlyph`, first ring = per-domain agents, second ring = specialist
  sub-agents; links are thin `#8AB4F8` arcs; awake agents glow teal, asleep agents are slate.
- Stage overlays: tier-filter-style container (All, Awake, Asleep), pulse bar (Agents awake, Skills, Versions promoted).
- Side sheet (agent): header `AgentGlyph` + agent name + state chip (Awake / Asleep); chips for skills (max 6 then
  "+n"); **Version timeline**: horizontal row of version pills (v1, v2, ...), the promoted one filled `primary`,
  candidates outlined; one-sentence "Last learned" summary; tiles: "Runs", "Win rate" (Not measured yet when null);
  footer "Wake" / "Sleep" (tonal) + "Open tree" (filled).
- Never display model id strings. Show the role ("Reasoning", "Fast") from configuration.

### 10.6 Cost (`#cost`)

Not part of the Stitch set. Use the list-detail surface from 10.4 with stat cards (metric-xl + sparkline) for spend
by service family and a single bar chart (bars radius 8 top corners, primary for current period, primary-container
for previous). Unknown cost renders "Not measured yet".

## 11. Data to visual mapping (topology contract, plan section 1.4)

| Field | Visual |
|---|---|
| `nodes[].status` green / amber / red / unknown | Node fill (2.3), status chip, environment dot aggregate (worst wins) |
| `nodes[].tier` | Latitude band; tier chip |
| `nodes[].kind` | Kind icon (9.17), kind chip, marker size |
| `nodes[].business.domain` | Longitude cluster; domain chip in the sheet |
| `nodes[].business.role_summary` | The single summary sentence |
| `nodes[].business.confidence` | Confidence band chip ("Strong", "Moderate", "Weak", "Insufficient"), number in tooltip; below 0.5 the chip is outlined, the summary turns on-surface-variant and gains a leading `help` icon |
| `nodes[].incidents_open` | Pulse rings, incident card, Incidents metric |
| `nodes[].golden.*` | Sheet tiles; null = "Not measured yet" |
| `nodes[].posture.*` | Posture icon chips |
| `edges[].rps` | Particle speed and count; null = no particles, static arc |
| `edges[].error_ratio` | Red arc at >= 0.02 |
| `edges[].confidence` | Arc opacity x (0.4 + 0.6 x confidence) |
| `journeys[]` | Journey chips, path, stepper |
| SSE `approval.created` | Approvals badge + warning variant of the incident card |
| SSE `agent.woke / slept` | Agent satellite glow, Agents stat count-up |

## 12. Stitch reference

- Project: **God-View Cockpit v2 (Material 3 Expressive)**, id `13870657085939966332`.
- Screen ids and PNGs: see the design-lead board post "[design] v2 ready" (PNG copies under the coordinator's
  `brain/.../stitch/v2/`). Stitch canvases render the globe as an illustration placeholder; the chrome, overlays and
  side sheets in the PNGs are final and this document wins on any pixel disagreement.

## 13. Implementation checklist for the cockpit

1. `import './theme/fonts.css'; import './theme/tokens.css';` first in `main.tsx`; remove `@fontsource/inter` and the
   four-colour hairline from `index.css`; set `<html data-theme="light">` and persist the theme toggle in
   `localStorage` (`gv-theme`: light | dark | system).
2. `tailwind.config.ts`: `presets: [gvPreset]` from `src/theme/tailwind-preset.ts`. Delete the `canvas`, `ink` and
   `accent` palettes; lint for raw `slate-`, `blue-`, `red-`, `amber-`, `green-`, `purple-`, `violet-`, `indigo-`
   classes (should be zero). The preset **replaces** the Tailwind colour palette, so legacy palette classes stop
   compiling on purpose. Colours are CSS variables, so Tailwind opacity modifiers (`bg-primary/10`) do not work: build
   state layers with `color-mix(in srgb, var(--md-sys-color-on-surface) 8%, transparent)` or an `::after` overlay at
   `--md-sys-state-hover` opacity.
3. Three.js: import `GLOBE_COLORS`, `NODE_STATUS_COLORS`, `GLOBE_STYLE`, `GLOBE_MOTION`, `TIER_LATITUDE` from
   `src/theme/globe.ts`; no hex literals in `GlobeView.tsx`.
4. Replace the left text column with `NavRail`; replace the always-open drawer with `DetailSheet` that mounts only on
   selection; replace any full-width red bar with `IncidentBanner`; replace stat-card grids with `PulseBar`.
5. Copy audit: every card one sentence; no paragraphs; no em-dashes; "Not measured yet" for nulls; "Sample data"
   chip in mock mode.
6. Screenshots at 1440 x 900 (light and dark) and at 480 x 900 (aux pane) for each route, posted to the board.
