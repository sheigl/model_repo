---
version: alpha
name: huggingface
description: "We're on a journey to advance and democratize artificial intelligence through open source and open science."
sourceUrl: "https://huggingface.co"

colors:
  primary: "#14a800"          # Hugging Face brand green — primary CTAs, links, active accents
  on-primary: "#0b0d0f"       # near-black text on the brand green
  background: "#0b0d0f"       # page background (HF dark mode base)
  surface: "#16181b"          # card / content surface
  surface-row: "#1c1e21"      # list rows, ghost buttons
  surface-elevated: "#1f2226" # elevated panels / overlays
  border: "#23262b"           # subtle separators & card borders
  border-strong: "#2f3338"    # input borders, strong outlines
  text: "#f7f7f8"             # primary text (near-white)
  text-muted: "#abadaf"       # muted / secondary text
  link: "#14a800"             # inline links
  success: "#3fbf6e"          # success state (always paired with a label/icon)
  error: "#ff9b9b"            # error state (always paired with a label/icon)
  warning: "#e0a458"          # warning state (always paired with a label/icon)
  info: "#478de7"             # info state (always paired with a label/icon)

typography:
  display:
    fontFamily: "Source Sans Pro, -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica Neue, Noto Sans, Arial, sans-serif, Apple Color Emoji, Segoe UI Emoji, Segoe UI Symbol, Noto Color Emoji"
    fontSize: 48px
    fontWeight: 700
    lineHeight: 1
  heading:
    fontFamily: "Source Sans Pro, -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica Neue, Noto Sans, Arial, sans-serif, Apple Color Emoji, Segoe UI Emoji, Segoe UI Symbol, Noto Color Emoji"
    fontSize: 30px
    fontWeight: 700
    lineHeight: 1.2
  body:
    fontFamily: "Source Sans Pro, -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica Neue, Noto Sans, Arial, sans-serif, Apple Color Emoji, Segoe UI Emoji, Segoe UI Symbol, Noto Color Emoji"
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.5
  mono:
    fontFamily: "IBM Plex Mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, Courier New, monospace"
    fontSize: 15px
    fontWeight: 400
    lineHeight: 1.6

spacing:
  base: 4px
  scale: [4, 8, 12, 16, 20, 24, 28, 32, 36, 40]

radius:
  sm: 4px
  md: 8px
  lg: 12px
  xl: 50px
  pill: 9999px

shadows:
  card: "rgba(0, 0, 0, 0) 0px 0px 0px 0px, rgba(0, 0, 0, 0) 0px 0px 0px 0px, rgba(0, 0, 0, 0) 0px 0px 0px 0px, rgba(0, 0, 0, 0) 0px 0px 0px 0px, rgba(0, 0, 0, 0.05) 0px 1px 2px 0px"
  elevated: "rgba(0, 0, 0, 0) 0px 0px 0px 0px, rgba(0, 0, 0, 0) 0px 0px 0px 0px, rgba(0, 0, 0, 0) 0px 0px 0px 0px, rgba(0, 0, 0, 0) 0px 0px 0px 0px, rgba(0, 0, 0, 0.1) 0px 20px 25px -5px, rgba(0, 0, 0, 0.1) 0px 8px 10px -6px"

motion:
  duration-fast: 150ms
  duration-base: 200ms
  duration-slow: 200ms
  easing: "cubic-bezier(0.4, 0, 0.2, 1)"
---

## Rationale

Hugging Face is the central hub for open-source machine learning, democratizing AI through community collaboration. The measured design tokens reflect a deliberately minimal, low-glare dark aesthetic that prioritizes clarity and accessibility over visual ornament. The near-black navy background (#0b0d0f) with near-white text (#f7f7f8) keeps dense technical content — model cards, datasets, and API references — legible for long reading sessions while staying comfortable on OLED screens. A single branded accent, Hugging Face green (#14a800), is reserved for primary actions, links, and the active nav state so interactive elements are unmistakable without introducing ornamental color. The typography stack (Source Sans Pro as primary, IBM Plex Mono for code) is utilitarian and web-safe, avoiding novelty; it signals "serious infrastructure" rather than consumer product.

The spacing and radius tokens reveal a micro-interaction sensibility: a 4px base unit enables tight, grid-aligned layouts that feel structured and professional. Shadows are nearly imperceptible (card shadow is 1px at 5% opacity), allowing content hierarchy to emerge through position and weight rather than depth, which suits a research-first audience. Motion durations are brisk (150-200ms), avoiding eye strain during frequent navigation of large model lists and repository browsing.

Overall, the system is stripped back and functional-every token serves clarity and performance. There are no decorative color accents beyond a single brand green, no gradients, and no warm/cool temperature shifts. This reflects Hugging Face's role as infrastructure: the design steps aside so that ML breakthroughs, community discussions, and technical integrations take center stage.

## 1. Visual Theme & Atmosphere

**Archetype:** Serious, accessible, minimal infrastructure.

The dark mode with near-black navy surfaces (#0b0d0f) and near-white text (#f7f7f8) establishes immediate credibility and accessibility while staying comfortable for long reading sessions. The near-zero shadow values indicate a flat, digital-native aesthetic that rejects skeuomorphism. No blur, depth layering, or color gradients distract from content; the one brand green (#14a800) is reserved for interactive moments. This is a **no-nonsense design language**: information density and scanability trump ornamentation. The result feels institutional and trustworthy-appropriate for a platform hosting production ML models and datasets that power enterprise applications.

**Mood:** Transparent, technical, democratic.

## 2. Color System

- **Primary:** #14a800 (Hugging Face brand green — primary CTAs, links, active nav)
- **On-primary:** #0b0d0f (near-black on the brand green)
- **Background:** #0b0d0f (near-black navy — HF dark mode base)
- **Surface:** #16181b (cards and content panels)
- **Text:** #f7f7f8 (near-white primary text)
- **Text-muted:** #abadaf (cool gray secondary text)

Hugging Face's dark palette is deliberately low-glare and monochromatic, with a single animated brand green accent. The key rules:

1. **One accent only.** Brand green #14a800 is the only saturated color — used exclusively for primary actions, inline links, and the active nav/selection state. Everything else stays in the neutral near-black/gray range.
2. **Neutral hierarchy via grays, not hue.** Distinction comes from near-black surfaces (background #0b0d0f, cards #16181b, rows #1c1e21, borders #23262b) and text weight/value (primary #f7f7f8, muted #abadaf).
3. **Semantic colors exist but are restrained.** Success (#3fbf6e), error (#ff9b9b), warning (#e0a458), and info (#478de7) are component-level only, and are **always paired with a text label or icon** so nothing is communicated by color alone (color-independence for colorblind users).
4. **Contrast:** near-white text (#f7f7f8) on near-black (#0b0d0f) yields a very high ~15:1 WCAG AAA ratio; the muted grays still clear AA for secondary text.

This constraint forces clarity: if you can't rely on color, you use **weight, position, size, and proximity** to establish hierarchy.

## 3. Typography

**Font stack (all scales):**
- **Primary:** Source Sans Pro → system fonts (Segoe UI, Roboto, etc.) → Arial fallback
- **Monospace:** IBM Plex Mono → ui-monospace fallback

**Scales:**

| Role | Size | Weight | Line Height |
|------|------|--------|-------------|
| Display | 48px | 700 | 1.0 |
| Heading | 30px | 700 | 1.2 |
| Body | 14px | 400 | 1.25 |
| Mono | 15px | 400 | 1.6 |

**Hierarchy via weight, not size:** The display scale (48px, bold) is reserved for hero statements ("The AI community building the future"). Headings (30px, bold) segment major sections. Body copy at 14px is compact but legible-suitable for reading model documentation and dataset descriptions on smaller devices. Monospace at 15px is slightly larger than body, ensuring code snippets and terminal output remain readable even at small scales.

**Line height is tight:** 1.0 for display, 1.2 for headings, and 1.25 for body-standard for web, favoring density and reduced scrolling. The 1.6 line height for monospace is wider, easing code scanning.

This stack prioritizes **web performance** (no custom fonts loaded) and **international support** (system fonts cover CJK, Cyrillic, Arabic, etc.).

## 4. Components & Patterns

**Subtle elevation:** Shadows are used sparingly:
- **Card shadow:** 1px blur, 5% black opacity-barely perceptible, for light surface separation.
- **Elevated shadow:** 20px blur + 8px inner shadow at 10% opacity-for modals or overlays that must float above content.

This restraint keeps the interface unified and prevents visual fatigue.

**Button & CTA design:** Primary CTAs are filled with the brand green (#14a800) and near-black text (#0b0d0f); secondary actions are ghost buttons with a subtle dark surface (#1c1e21) and a strong border (#2f3338); destructive actions use the restrained error tint. Interactive states rely on:
- Hover via darkening the filled green (#0f8500) or lightening the ghost surface/border
- Focus ring: a green outline (2px) on the dark background, never `outline: none`
- Active/pressed states via opacity shift

**Card & content containers:** Use a 1px border (#23262b) on the near-black surface (#16181b) for definition rather than shadow depth, with padding derived from the spacing scale. Elevation is reserved for overlays/drawers, which get the elevated shadow.

## 5. Spacing & Layout

**Base unit:** 4px

**Scale:** 4, 8, 12, 16, 20, 24, 28, 32, 36, 40

Multiples of 4 create a **tight, rhythm-driven grid**. This 4px base is increasingly common in modern design systems and allows for:
- **Micro-adjustments** (8-12px gaps between inline elements)
- **Generous breathing room** at larger scales (32-40px for section margins)
- **Vertical rhythm alignment** with text baselines (4px grid accommodates line heights of 1.0, 1.2, 1.25, 1.6)

No responsive breakpoints are defined in the token set, but the scale is designed to scale proportionally across viewport sizes (likely using CSS custom properties or a preprocessor).

## 6. Motion & Interaction

**Durations:**
- Fast: 150ms
- Base: 200ms
- Slow: 200ms (note: base and slow are equal; likely a measurement artifact or intentional simplification)

**Easing:** `cubic-bezier(0.4, 0, 0.2, 1)` (Material Design standard easing, slightly snappy)

These values indicate **quick, responsive interactions**: hover states, modal opens, and navigation transitions complete in 150-200ms, keeping the interface feel snappy without disorienting the user. The easing curve accelerates in and decelerates out, creating a sense of purpose and polish-important for a platform where users perform frequent searches and model card navigation.

---

## Accessibility

### Contrast Ratios

**Measured pairs:**
- Near-white text (#f7f7f8) on near-black background (#0b0d0f): **~15:1 WCAG AAA** ✓ Exceeds AA (4.5:1)
- Near-black text (#0b0d0f) on brand green (#14a800): **~5:1 WCAG AA** ✓ Meets AA for normal text
- Muted text (#abadaf) on surface (#16181b): **~6:1 WCAG AA** ✓ Exceeds AA

All text is accessible for users with normal or corrected vision. Semantic colors (success/error/warning/info) are always paired with a text label or icon so colorblind users receive equivalent information (no color-only encoding).

### Minimum Requirements

- **Touch target:** 44×44px minimum. Not explicitly defined in tokens, but the 4px spacing scale supports this: a button with padding of `8px 16px` (horizontally) and `12px` (vertically) easily meets 44px in height or width. Components must enforce this at build time.

- **Focus indicator:** A 2px brand-green (#14a800) outline with a 2px offset is used on the dark surface so the focused control is clearly visible. This must be added at the component level and not overridden via `outline: none` (a common accessibility mistake).

- **Color independence:** Semantic colors exist but are restrained and are **always paired with a text label or icon**, ensuring colorblind users receive equivalent information without relying on color alone.

- **Motion:** Durations of 150-200ms are well below the threshold that triggers vestibular issues (generally considered safe for animations under 500ms). However, **no `prefers-reduced-motion` override is defined**; this must be implemented in CSS (e.g., `@media (prefers-reduced-motion: reduce) { * { animation-duration: 0.01ms !important; } }`).
