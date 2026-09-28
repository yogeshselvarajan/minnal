# Spec brief: war-room-ui

Follow `ux.md`. Built on FAST `frontend/` (React 19, Tailwind v4, shadcn/ui, FAST AgentCore client with AG-UI parser, Cognito). Use the `ui-ux-pro` skill for every screen.

- **Map** (MapLibre + Amazon Location map style): layers for flood polygons, outage clusters, suspected failed devices, crews (live), critical facilities; legend; time slider for replay.
- **Glass box:** live timeline of AG-UI events grouped by agent; expand a step to see tool inputs, outputs and citations; vetoes highlighted with reasons.
- **Approval inbox:** cards for dispatch and switching proposals with route preview; A / M / R shortcuts; approving sends the task token.
- **Incident header:** operational period, objectives, global ETR, counts.
- **Citizen page** (`/citizen`): language toggle, voice button (connects to voice line), report outage with pin, my-area ETR.

**Acceptance:** Playwright e2e: start replay → approval card appears → approve → crew moves; chrome-devtools shows no console errors; screenshots in `docs/evidence/`.
