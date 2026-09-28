# Role: Frontend engineer

Before building or restyling any screen, activate the **`ui-ux-pro`** skill and follow its workflow; follow `frontend-react.md` for code and `ux.md` for the domain layout.

- Stack is FAST's React 19 + TS strict + Vite 8 + Tailwind v4 + shadcn/ui, streaming through FAST's AgentCore client with the AG-UI parser. Add MapLibre (`react-map-gl/maplibre`) with Amazon Location maps, Motion for purposeful animation, TanStack Query for reads.
- Add components through the **shadcn** MCP; look up every library API (Motion, MapLibre, TanStack Query, Radix) with **Context7** at the installed version; use **fetch** for specific docs pages.
- Install tokens from `.kiro/skills/ui-ux-pro/assets/tokens.css` into `src/app/globals.css` once, then run the contrast check.
- War room: approval inbox (A / M / R), map layers, agent glass box fed by AG-UI custom events (`api-contracts.md`). Citizen page: voice button, language toggle, report outage, my-area ETR.
- Every async view has loading, empty, error and success states.
- Verify with **chrome-devtools** at phone, laptop and wall sizes in dark and light; zero console errors; screenshots to `docs/evidence/`.
- Report done only after the skill's `review-checklist.md`, listing any item not met.
