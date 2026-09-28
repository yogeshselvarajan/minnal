# Phase 07: citizen-voice-line

Spec name: `citizen-voice-line` (brief: `docs/spec-briefs/05-citizen-voice-line.md`).
The spec was written and reviewed in phase 01. Implement its tasks; if implementation shows the spec is wrong or incomplete, update the spec first (architect or domain-analyst) and log the change in `docs/plans/decisions-log.md`.
Lanes: agent-engineer (voice/**), frontend-engineer (citizen page voice button), qa-eval-engineer (tests/voice/**).
Model: Nova 2 Sonic via Strands bidirectional streaming. Tests cover the conversation state machine and tool calls with a fake audio stream; downed-wire reports must always trigger the safety instruction.
