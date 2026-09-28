# Component patterns (compose from shadcn; add with the shadcn MCP)

## StatusBadge (`components/common/StatusBadge.tsx`)
`Badge` + lucide icon + label. Variants map 1:1 to status tokens: critical (`OctagonAlert`), warning (`TriangleAlert`), ok (`CircleCheck`), info (`Truck` or `Loader`), neutral (`CircleHelp`). Always renders text; icon has `aria-hidden`.

## ApprovalCard (`features/approvals/`)
- `Card` with header (kind + proposal ID + time waiting), body (one-sentence proposal, Safety reasoning excerpt with "show more", impact: customers restored, ETR change), mini route preview (static map image or small MapLibre inset), footer actions.
- Actions: `Approve` (primary, brand), `Modify` (secondary opens a `Sheet` with editable fields), `Reject` (ghost, requires reason via `Textarea`).
- Keyboard: `A`, `M`, `R` when the card is focused; `J`/`K` moves between cards; shortcuts shown with `Kbd` hints.
- Vetoed proposals render read-only with the veto reason and rule ID; no approve button.
- Pending server confirmation: buttons disabled with inline spinner; success collapses the card; failure shows inline `Alert` with retry.

## Agent glass box (`features/glass-box/`)
- Vertical timeline grouped by operational period, then by agent. Each `StepCard`: agent avatar (initials in a coloured ring per ICS role), step title, status, duration, expandable `Collapsible` with tool calls (input/output summaries, `IBM Plex Mono`), citations as links.
- Live region: `aria-live="polite"`, announcing only step completions and vetoes (not every token).
- "Jump to latest" floating button when the user has scrolled up.

## Incident header
Operational period, objectives, three KPIs (customers out, critical facilities out, crews active) with deltas, global ETR with "updated x min ago". KPI tiles use `Card` + tabular numbers; deltas use arrow icons plus words ("up 1,240").

## Command palette
shadcn `Command` on `Ctrl/Cmd+K`: jump to feeder, crew, facility, proposal; toggle map layers; switch theme.

## Feedback
- Inline `Alert` for errors that need action; `sonner` toasts only for confirmations ("Dispatch approved") and non-blocking info.
- `Skeleton` shaped like the final content; never full-screen spinners.
- Empty states: short sentence + one action ("No approvals waiting. The next operational period starts at 14:00.").

## Citizen page
Large (56 px) round voice button with state ring (idle / listening / speaking), language `ToggleGroup` (English / हिन्दी / தமிழ்), report form with location pin, ETR card with plain-language status and last update time. Minimum tap target 48 px; font size ≥ 16 px.
