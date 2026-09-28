---
name: ui-ux-pro
description: Professional UI/UX craft for Minnal's React 19 + Tailwind v4 + shadcn/ui frontend - design tokens, typography, dark ops-console and light themes, purposeful motion with Motion, map and dashboard layouts, approval and timeline components, loading/empty/error states, WCAG 2.2 AA accessibility, and a pre-merge UI review. Use whenever you design, build, restyle, animate or review any screen, component or visual in frontend/.
license: MIT
metadata:
  author: Yogesh Selvarajan
  version: "1.0"
---

# UI/UX Pro for Minnal

Minnal is a **mission-critical control-room tool** used at night, under stress, on wall displays and laptops, plus a **citizen page** used on cheap phones in a storm. Design for clarity first, then delight. Every visual choice must help someone make a safe decision faster.

## Workflow (follow in order)

1. **Read the brief.** Which user (commander, crew lead, citizen), which decision, which device? Read `.kiro/steering/ux.md` for the domain layout.
2. **Tokens first.** Use only the tokens in `references/design-tokens.md` (copy `assets/tokens.css` into `src/app/globals.css` once). Never hard-code colours, radii, shadows or durations in components.
3. **Compose from shadcn.** Find components with the **shadcn MCP** and add them; check Radix/shadcn APIs with **Context7**. Patterns for Minnal-specific components are in `references/components.md`.
4. **Design the four states** for every async surface: loading (skeleton shaped like the content), empty (what it means and what to do), error (plain language + retry), success.
5. **Add motion last**, only where it explains change. Follow `references/motion.md`; always respect `prefers-reduced-motion`.
6. **Map and data views** follow `references/map-and-dataviz.md`.
7. **Accessibility pass** with `references/accessibility.md`; run `python .kiro/skills/ui-ux-pro/scripts/contrast_check.py frontend/src/app/globals.css`.
8. **Verify in a browser** with the **chrome-devtools** MCP at 1440×900, 1920×1080 (wall) and 390×844 (phone), in dark and light; zero console errors; save screenshots to `docs/evidence/ui-<screen>-<theme>.png`.
9. **Self-review** against `references/review-checklist.md` before reporting done, and list any item you could not satisfy.

## Non-negotiables
- Status is never shown by colour alone: icon + text label + colour.
- Keyboard can do everything a mouse can; focus is always visible.
- Numbers that matter (customers out, ETR, crews) use tabular figures and never jump layout while updating.
- Nothing flashes, pulses forever or autoplays sound. One-shot attention cues only.
- Text in Tamil and Hindi renders with proper fonts and does not overflow controls.
- The UI tells the truth: show "thinking", "waiting for approval", "stale data (updated 4 min ago)" rather than fake progress.

## Anti-patterns to reject
Generic purple gradients, glassmorphism over maps, drop shadows on everything, centred walls of text, spinners for anything that has a known shape (use skeletons), toasts for errors that need action (use inline errors), modals for approvals (use the inbox), icon-only buttons without tooltips and `aria-label`, animations over 400 ms, and colour-only legends.

## Optional companion
The community **UI UX Pro Max** skill (MIT; `npm i -g ui-ux-pro-max-cli && uipro init --ai kiro`) adds a searchable library of styles, palettes and font pairings. If installed, use it for exploration only; this skill's tokens and rules win for Minnal.
