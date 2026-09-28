# Accessibility checklist (WCAG 2.2 AA)

- [ ] Contrast passes (`scripts/contrast_check.py`), including focus rings (3:1) and status icons (3:1).
- [ ] Every interactive element is reachable and operable by keyboard in a logical order; no keyboard traps; visible focus (2 px ring, `--ring`, offset 2 px).
- [ ] Focus not obscured by sticky headers or the map controls (2.4.11).
- [ ] Target size ≥ 24×24 px in the war room, ≥ 48×48 px on the citizen page (2.5.8).
- [ ] Status and map meaning never by colour alone (icons, labels, patterns).
- [ ] All images and icons have text alternatives or `aria-hidden` if decorative; icon-only buttons have `aria-label` and a tooltip.
- [ ] Forms: visible labels, error text linked with `aria-describedby`, errors announced; no placeholder-as-label.
- [ ] Live updates via `aria-live="polite"`; urgent vetoes and downed-wire alerts via `role="alert"` (sparingly).
- [ ] `lang` attribute set per language block (`ta`, `hi`, `en`); text resizes to 200% without loss.
- [ ] Motion respects `prefers-reduced-motion`; nothing flashes more than 3 times per second.
- [ ] `@axe-core/playwright` shows no serious or critical violations.
