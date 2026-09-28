# Phase 05: war-room-ui

Spec name: `war-room-ui` (brief: `docs/spec-briefs/04-war-room-ui.md`).
The spec was written and reviewed in phase 01. Implement its tasks; if implementation shows the spec is wrong or incomplete, update the spec first (architect or domain-analyst) and log the change in `docs/plans/decisions-log.md`.
Lanes: frontend-engineer (frontend/**), qa-eval-engineer (frontend tests and `frontend/e2e/**`).
The frontend-engineer activates the `ui-ux-pro` skill first, installs its tokens, and follows its review checklist. Build against a recorded AG-UI event fixture (`frontend/src/fixtures/replay-events.json`) so the UI works without a deployed backend (`?mock=1`).
Unit tests cover loading, empty, error and success states for the approval inbox, glass box and map legend.
