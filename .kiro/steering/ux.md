---
inclusion: fileMatch
fileMatchPattern: ["frontend/**"]
---

# UX rules for the war room and citizen page

Domain-specific UX. For design system, motion, component craft and the pre-merge UI checklist, use the `ui-ux-pro` skill (`.kiro/skills/ui-ux-pro/`).

- **Layout:** left = incident, operational period, priorities, approval inbox; centre = map; right = agent glass box (timeline of agent steps, tool calls, citations, vetoes). Collapsible panes for wall displays.
- **Approval cards** are the primary interaction: proposal, Safety reasoning, route preview on the map, Approve / Modify / Reject, keyboard shortcuts (A / M / R).
- **Map layers:** flood polygons, outage clusters (count badges), suspected failed devices, crews (live), critical facilities (icons by type). Legend always visible.
- **Status never by colour alone:** icon + label. Colour-blind-safe palette. Dark theme default, light supported.
- **Streaming honesty:** show "thinking", "calling tool", "waiting for approval" states from AG-UI events; never fake progress.
- **Citizen page:** mobile-first, one big voice button, language toggle (English, Hindi, Tamil), report-outage with location pin, my-area ETR with "last updated".
- **Performance:** first meaningful paint under 2 s on 4G; map clusters instead of thousands of pins.
