---
inclusion: fileMatch
fileMatchPattern: ["frontend/**"]
---

# Frontend standards: React (war room + citizen page)

For visual design, motion and UX quality, activate the **`ui-ux-pro`** skill (`/ui-ux-pro`) before building or reviewing any screen.

## Stack (matches the FAST template; do not swap core pieces)
| Concern | Choice |
|---|---|
| Framework | **React 19** + **TypeScript 5 strict** + **Vite 8** |
| Styling | **Tailwind CSS v4** (`@theme` tokens in `src/app/globals.css`), `tailwind-merge`, `clsx`, `class-variance-authority` |
| Components | **shadcn/ui** (style `new-york`, Radix primitives, `lucide-react` icons). Add components with the **shadcn MCP** / `npx shadcn@latest add` |
| Agent streaming | FAST's `src/lib/agentcore-client` with the **AG-UI parser** (`parsers/agui.ts`). Extend it; do not add a second client |
| Auth | FAST `AuthProvider` (Cognito via `react-oidc-context` / Amplify) |
| Routing | `react-router-dom` v6 (as in FAST) |
| Server state | **TanStack Query** for REST/Gateway reads; AG-UI stream state in a reducer hook |
| UI state | React context + `useReducer`; add **Zustand** only if a store is shared by 3+ distant components |
| Map | **MapLibre GL** via `react-map-gl/maplibre`, Amazon Location map style, GeoJSON sources with clustering |
| Motion | **Motion** (`motion/react`) + `tw-animate-css` for simple enter/exit |
| Charts | shadcn **charts** (Recharts) |
| Forms | `react-hook-form` + `zod` resolver |
| Toasts / command palette | shadcn `sonner`, `command` (cmdk) |
| Tests | **Vitest 4** + Testing Library + `fast-check`; **Playwright** e2e |

Check every library API with the **Context7 MCP** at the installed version before using it.

## Structure (feature-first)
```
frontend/src/
  app/                  providers, router, globals.css (tokens)
  components/ui/        shadcn primitives (generated, lightly edited)
  components/common/    app-wide building blocks (StatusBadge, EmptyState, ErrorState, KeyHint)
  features/
    incident/           header, objectives, operational period
    map/                MapView, layers/, legend, clustering, crew animation
    glass-box/          AgentTimeline, StepCard, ToolCallDetails, CitationList
    approvals/          ApprovalInbox, ApprovalCard, useApprovalShortcuts
    citizen/            CitizenPage, VoiceButton, ReportOutageForm, AreaEtr
  lib/agentcore-client/ FAST client + Minnal AG-UI event types
  lib/api/              typed Gateway/REST clients (zod-validated responses)
  hooks/                cross-feature hooks
```
A feature folder owns its components, hooks, types and tests. Features import from `components/*` and `lib/*`, never from each other's internals (export through `index.ts`).

## Component rules
- Function components, named exports, props typed with `type Props = {...}`; no `React.FC`.
- One component per file; ≤ 200 lines; extract hooks for logic.
- Every async view has **loading (skeleton), empty, error and success** states.
- No inline styles except dynamic values (map positions). No magic numbers: use tokens.
- Accessibility is required: semantic elements, labelled controls, focus visible, keyboard paths for every action, `aria-live="polite"` for streamed agent updates, status never by colour alone.
- i18n: all user-facing strings through a `t()` helper with `en`, `hi`, `ta` bundles on the citizen page; fonts must cover Tamil and Devanagari.

## Data and streaming
- Validate every server payload with zod at the edge (`lib/api`). Types come from the schemas (`z.infer`).
- AG-UI events are a discriminated union; unknown events are logged and ignored, never crash the UI.
- Optimistic updates only for local UI (e.g. selecting a crew); approvals wait for server confirmation.

## Performance
- Code-split routes (`React.lazy`), lazy-load the map and charts.
- Memoise expensive map layer data; update GeoJSON sources, do not recreate layers.
- Bundle budget: initial JS < 250 KB gzip for the citizen page.

## Commands
`npm run lint && npx tsc --noEmit && npm test && npx playwright test`
