# Kickoff prompts (high level; paste into Kiro)

> **To run the whole build unattended, use `docs/START_PROMPT.md` (autopilot or `/goal`).** The prompts below are the manual, day-by-day alternative.

CLI as `minnal-lead` unless marked **IDE** or **Web**.

## Day 1 · Mon 28 Sep · Foundation
Follow `docs/SETUP.md`, commit, push a public repo.
> Have the domain-analyst research storm restoration for a Chennai utility (ICS roles, restoration order, flood safety, CAP 1.2, open weather and cyclone data on RODA) into docs/domain with sources. Then create specs `replay-simulator` and `grid-tools` from docs/spec-briefs 01 and 02: requirements first, stop for my review.

## Day 2 · Tue 29 Sep · Data + tools (**IDE** for PBT)
**IDE:** approve designs (properties P1 to P4, P7), run the tasks, let Kiro generate and run the property-based tests.
> CLI: /ship-feature replay-simulator, then /ship-feature grid-tools.

## Day 3 · Wed 30 Sep · Agent team runtime
> Create spec `agent-team-runtime` from brief 03. Then /ship-feature agent-team-runtime. The platform-engineer prepares the CDK changes and stops before deploy.

Approve `cdk deploy`, then:
> Run one operational period against the replay and show me every Safety veto.

## Day 4 · Thu 1 Oct · War room UI
> Create spec `war-room-ui` from brief 04, then /ship-feature war-room-ui. The frontend-engineer verifies with chrome-devtools and the qa-eval-engineer adds the Playwright approval test.

## Day 5 · Fri 2 Oct · Public information + voice
> Add PIO, CAP alerts and SNS (properties P5, P6), then create spec `citizen-voice-line` from brief 05 and /ship-feature it.

## Day 6 · Sat 3 Oct · Quality, power, cloud
> qa-eval-engineer: AgentCore evaluations for every agent plus citizen-line user simulation; security-reviewer: full review against security.md.

**IDE:** use the Power Builder to finish `powers/minnal-gridops`, import it, and trigger it by asking "how should I prioritise outage restoration after a cyclone?".

**Web** (paid plan): connect the repo, Configuration Sync (steering, agents, powers), a cloud session on a GitHub issue, and an Automation "Nightly replay regression":
> Run the offline test suite and the recorded-replay evaluation in evals/offline. If any property test or evaluation score regressed versus evals/baseline.json, open a PR with the report and a proposed fix. Otherwise do nothing.

## Day 7 · Sun 4 Oct · Evidence + video
> Write docs/evidence per lesson, update CHALLENGE_CHECKLIST.md, verify the ledger, and draft the README from docs/BLUEPRINT.md.

Record the 3-minute demo (blueprint section 12).

## Day 8 · Mon 5 Oct · Ship
Post on LinkedIn (#KiroUniversity #BuildWithKiro, tag @kiro), submit the form with the per-lesson writeup, freeze the repo until 19 Oct.
