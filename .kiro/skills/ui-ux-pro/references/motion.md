# Motion (Motion for React + tw-animate-css)

Motion exists to **explain change**: where something came from, what just updated, what needs attention. It is never decoration in a control room.

## Library
- `motion/react` (the Motion library, formerly Framer Motion) for layout, presence and sequenced animation. Check its current API with Context7 before use.
- `tw-animate-css` utilities (`animate-in fade-in slide-in-from-bottom-2`) for simple shadcn enter/exit.
- Map motion (crews, pins) is done in MapLibre (feature state, `requestAnimationFrame` interpolation), not with DOM animation.

## Timing
| Token | ms | Use |
|---|---|---|
| `--duration-fast` | 150 | hover, press, small state flips |
| `--duration-base` | 220 | panels, cards entering, tab changes |
| `--duration-slow` | 380 | large layout shifts, first-load sequences |
Easing: `--ease-out` for entering, `--ease-in-out` for moving. Nothing longer than 400 ms. Stagger lists by 30 to 40 ms, max 8 items animated; the rest appear instantly.

## Patterns Minnal uses
| Moment | Motion |
|---|---|
| New glass-box step streams in | fade + 4 px rise, 220 ms, staggered; auto-scroll only if user is at the bottom |
| Approval card arrives | slide from the right edge of the inbox + one-time brand-colour border glow (600 ms fade out) |
| Approval resolved | `AnimatePresence` exit: collapse height + fade, list reflows with `layout` |
| Safety veto | card border switches to critical colour, icon swaps with a 150 ms cross-fade, **one** subtle attention pulse; no shaking |
| KPI number changes | tabular numbers; brief highlight of the changed digit block (background tint 600 ms); no counting animations for safety figures |
| Crew moves on map | interpolate position between updates over the update interval; heading arrow rotates smoothly |
| Outage cluster grows | count badge scales 1 → 1.12 → 1 once |
| Theme switch | instant (no colour tweening across the whole UI) |

## Code sketch
```tsx
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

export function ApprovalList({ items }: Props) {
  const reduce = useReducedMotion();
  return (
    <AnimatePresence initial={false}>
      {items.map((p) => (
        <motion.li
          key={p.id}
          layout={!reduce}
          initial={reduce ? false : { opacity: 0, x: 24 }}
          animate={{ opacity: 1, x: 0 }}
          exit={reduce ? { opacity: 0 } : { opacity: 0, height: 0 }}
          transition={{ duration: 0.22, ease: [0.2, 0.8, 0.2, 1] }}
        >
          <ApprovalCard proposal={p} />
        </motion.li>
      ))}
    </AnimatePresence>
  );
}
```

## Rules
- Honour `prefers-reduced-motion` (`useReducedMotion`, and the global CSS guard in tokens.css).
- Animate `transform` and `opacity` only; never animate width/height of large trees except via `layout`.
- No infinite animations except a tiny "live" indicator dot, which must be pausable.
- Never animate content that is being read (no marquee, no auto-rotating carousels).
