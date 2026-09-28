# Pre-merge UI review (report each line as pass / fail / n.a.)

**Clarity**
- [ ] The primary decision on the screen is obvious within 3 seconds.
- [ ] One primary action per view; destructive actions visually distinct and confirmed.
- [ ] Numbers that matter are large, tabular and labelled with units and freshness.

**Consistency**
- [ ] Only design tokens used (no raw hex, px radius or ms values in components).
- [ ] shadcn primitives used for standard controls; no hand-rolled dropdowns or dialogs.
- [ ] Icons from lucide only, 16/20/24 px, consistent stroke.

**States**
- [ ] Loading skeleton, empty, error with retry, success, and stale-data indicator all designed.
- [ ] Long text, Tamil/Hindi text and large numbers do not break layout.

**Motion**
- [ ] Every animation explains a change; ≤ 400 ms; reduced-motion honoured; no infinite loops.

**Responsive**
- [ ] Works at 390, 1440 and 1920 wide; wall-display mode hides chrome and enlarges type.

**Accessibility**
- [ ] `accessibility.md` checklist passes; axe clean.

**Evidence**
- [ ] chrome-devtools: console clean; screenshots saved in dark and light to `docs/evidence/`.
