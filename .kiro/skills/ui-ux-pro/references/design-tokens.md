# Design tokens

Brand idea: **lightning over a night grid**. Deep blue-black surfaces, one electric-yellow accent used sparingly for the brand and the primary action, and a colour-blind-safe status set (Okabe-Ito derived).

## Typography
- **UI:** `IBM Plex Sans` (400, 500, 600). **Data/code:** `IBM Plex Mono` (tabular figures). **Tamil:** `Noto Sans Tamil`. **Hindi:** `Noto Sans Devanagari`.
- Stack: `"IBM Plex Sans", "Noto Sans Tamil", "Noto Sans Devanagari", system-ui, sans-serif`.
- Scale (rem): 0.75 · 0.875 · 1 · 1.125 · 1.25 · 1.5 · 2 · 2.5. Body 0.875 in the war room (dense), 1 on the citizen page. Line height 1.5 body, 1.2 headings.
- `font-variant-numeric: tabular-nums` on every changing number.

## Spacing, radius, elevation
- 4 px grid: 4, 8, 12, 16, 24, 32, 48, 64.
- Radius: 6 (controls), 10 (cards), 16 (sheets), full (badges).
- Elevation by surface colour steps, not heavy shadows. Shadow only for floating layers (popovers, map controls).

## Colour roles (see `assets/tokens.css` for values)
| Role | Use |
|---|---|
| `--background`, `--surface-1..3` | page and stacked surfaces |
| `--foreground`, `--muted-foreground` | text |
| `--border`, `--ring` | dividers and focus rings |
| `--brand` | Minnal accent (lightning yellow); logo, primary CTA, selection |
| `--status-critical` | danger, veto, make-safe, downed wire |
| `--status-warning` | degraded, at risk, waiting approval |
| `--status-ok` | restored, energised safe, PASS |
| `--status-info` | in progress, crew en route |
| `--status-neutral` | unknown, not yet assessed |
| `--flood` | flood polygons (with hatch pattern) |

All text/background pairs must reach **4.5:1** (normal text) or **3:1** (large text, icons, focus rings). Run `scripts/contrast_check.py` after any token change.

## Tailwind v4 wiring
`@theme inline` maps CSS variables to utilities, so components use `bg-surface-2 text-foreground border-border` and status utilities like `text-status-critical`. Dark is the default (`:root`), light under `.light`; also honour `prefers-color-scheme` on the citizen page.
