---
name: High-Precision Engineering
colors:
  surface: '#131313'
  surface-dim: '#131313'
  surface-bright: '#393939'
  surface-container-lowest: '#0e0e0e'
  surface-container-low: '#1b1b1c'
  surface-container: '#202020'
  surface-container-high: '#2a2a2a'
  surface-container-highest: '#353535'
  on-surface: '#e5e2e1'
  on-surface-variant: '#c1c6d7'
  inverse-surface: '#e5e2e1'
  inverse-on-surface: '#303030'
  outline: '#8b90a0'
  outline-variant: '#414755'
  surface-tint: '#adc6ff'
  primary: '#adc6ff'
  on-primary: '#002e69'
  primary-container: '#4b8eff'
  on-primary-container: '#00285c'
  inverse-primary: '#005bc1'
  secondary: '#d7baff'
  on-secondary: '#440088'
  secondary-container: '#6702c8'
  on-secondary-container: '#ceacff'
  tertiary: '#c8c6c5'
  on-tertiary: '#313030'
  tertiary-container: '#929090'
  on-tertiary-container: '#2a2a29'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#d8e2ff'
  primary-fixed-dim: '#adc6ff'
  on-primary-fixed: '#001a41'
  on-primary-fixed-variant: '#004493'
  secondary-fixed: '#eddcff'
  secondary-fixed-dim: '#d7baff'
  on-secondary-fixed: '#280056'
  on-secondary-fixed-variant: '#6100be'
  tertiary-fixed: '#e5e2e1'
  tertiary-fixed-dim: '#c8c6c5'
  on-tertiary-fixed: '#1c1b1b'
  on-tertiary-fixed-variant: '#474646'
  background: '#131313'
  on-background: '#e5e2e1'
  surface-variant: '#353535'
typography:
  headline-sm:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  code-md:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: '450'
    lineHeight: 20px
  code-sm:
    fontFamily: JetBrains Mono
    fontSize: 11px
    fontWeight: '400'
    lineHeight: 16px
  label-caps:
    fontFamily: Inter
    fontSize: 10px
    fontWeight: '700'
    lineHeight: 12px
    letterSpacing: 0.05em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  base: 4px
  xs: 4px
  sm: 8px
  md: 12px
  lg: 16px
  xl: 24px
  panel-gap: 1px
  container-padding: 8px
---

## Brand & Style
The design system is engineered for technical precision, catering to hardware security researchers and digital logic engineers. The brand personality is clinical, efficient, and authoritative, mirroring the complexity of gate-level circuit analysis.

The visual style follows a **Corporate / Modern** aesthetic tailored for EDA (Electronic Design Automation). It emphasizes high data density and structural clarity. The interface utilizes a "split-pane" philosophy inspired by high-end IDEs, using subtle borders rather than heavy shadows to define workspace boundaries. The aesthetic avoids unnecessary decoration, focusing instead on readability and the rapid identification of Hardware Trojans within dense netlists.

## Colors
The palette is rooted in a "Deep Dark" spectrum to reduce eye strain during long analysis sessions. 

- **Core Surfaces:** The primary workspace uses a deep charcoal (#121212), while elevated panels (sidebars, inspector) use a slightly lighter slate gray (#1E1E1E).
- **Accents:** Electric Blue is the primary driver for interaction and focus states. Secondary Purple is reserved for AI-assisted insights and Trojan detection highlights.
- **Functional Colors:** High-contrast semantic colors (Green, Orange, Red) are used strictly for status indicators, gate logic states, and security alerts.

## Typography
This design system employs a dual-font strategy to distinguish between UI orchestration and circuit data.

- **UI Language:** Inter is used for all navigational elements, menus, and descriptive text. It provides a clean, neutral baseline that stays readable at small scales.
- **Technical Language:** JetBrains Mono is used for all circuit identifiers, gate names (e.g., `G_AND_01`), netlist snippets, and terminal output. Its increased x-height and distinct characters prevent confusion between similar alphanumeric strings.
- **Density:** We prioritize smaller font sizes (11px-14px) to maximize the information displayed on screen without sacrificing legibility.

## Layout & Spacing
The layout follows a **Fixed-Fluid Hybrid** model typical of professional IDEs. The sidebars (File Explorer, Gate Tree) and the bottom panels (Console, Logic Analyzer) have fixed or user-resizable widths, while the central workspace (Circuit Graph, Analysis View) is fluid.

- **Rhythm:** An 4px incremental scale is used for high-density layouts. 
- **Borders over Margins:** Use 1px borders (#333333) to separate functional areas rather than whitespace to maintain a compact, "instrumental" feel.
- **Grid:** A 12-column internal grid is available for dashboard views, but the primary interface relies on flexible split-pane containers.

## Elevation & Depth
In this design system, depth is communicated through **Tonal Layering** rather than realistic shadows.

- **Level 0 (Background):** #121212 - The main canvas for graph editors or code.
- **Level 1 (Panels):** #1E1E1E - Sidebars, toolbars, and tab headers.
- **Level 2 (Popovers/Tooltips):** #252525 - Floating menus and context-sensitive help, using a subtle 8px blur shadow with 40% opacity.
- **Interaction:** Hover states on list items or tree nodes should use a subtle highlight (opacity 0.08 of Primary Blue) rather than changing elevation.

## Shapes
The design system utilizes a **Rounded** (8px default) aesthetic to soften the technical density of the UI.

- **Containers:** Main app panels and cards use 8px corners.
- **Small Elements:** Buttons, input fields, and tags use 6px for a sharper, more precise appearance.
- **Progress Bars:** Fully rounded (pill) to distinguish them from structural containers.

## Components
- **Buttons:** Primary buttons are solid Electric Blue with white text. Secondary buttons are outlined with a subtle gray border. All buttons use a compact height (28px or 32px).
- **Tree Views:** Used for gate hierarchies. Include a 1px vertical guide line to indicate depth. Icons for gates (AND, OR, XOR) should be simplified 12px geometric glyphs.
- **Data Tables:** Zebra-striping is omitted. Use thin #333333 bottom borders for rows. Monospace font is mandatory for numerical data and hex addresses.
- **Status Badges:** Small, pill-shaped indicators. Use "Glow" effects (subtle outer shadow of the same color) for critical "Trojan Detected" alerts.
- **Input Fields:** Dark background (#121212) with a 1px border. The border shifts to Primary Blue on focus.
- **Splitters:** The handles between panes should be 4px wide, appearing as a simple line that changes color to Primary Blue on hover to indicate resizability.