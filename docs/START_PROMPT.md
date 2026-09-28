# How to start the build and let it run on its own

Two ways to drive the whole build from **Kiro CLI**. Both use the same specs, steering, agents, hooks and phase briefs, so you can switch between them at any time.

| | A. Autopilot (fully unattended) | B. Interactive goal loop |
|---|---|---|
| Needs | `KIRO_API_KEY` (Kiro Pro and above) | Any Kiro plan, a terminal left open |
| How it decides "done" | The **runner** runs each phase's verification command; retries up to 3 times with the failure output | The agent's `/goal` loop checks its own acceptance criteria |
| Commits | Runner commits after each verified phase | You commit (or ask the lead to summarise, then commit) |
| Best for | Overnight runs, whole-plan execution | Watching one phase, steering mid-way (`Ctrl+S` queues a message) |

Before either: finish `docs/SETUP.md` (FAST copied, AWS profiles, Bedrock access for Nova 2 Lite, Nova 2 Sonic, gpt-oss-120b and Titan Embeddings V2, powers installed), and commit.

## Where the specs come from

Specs are generated **first, all together**, before any feature code:

| Phase | What happens | Gate |
|---|---|---|
| 00 foundation | Base project, models config, domain research notes in `docs/domain/` (inputs for the specs) | `tests/test_no_claude.py`, 3 domain notes |
| **01 specs** | For each of the six features: `domain-analyst` writes `requirements.md`, `solution-architect` writes `design.md` (with correctness properties) and `tasks.md`, `code-reviewer` reviews into `docs/reviews/spec-<name>.md`. **No code.** Source briefs: `docs/spec-briefs/` | `scripts/specs-ready.sh` (all 3 files, EARS, properties, tasks cite requirements, review exists) |
| 02 to 07 | Each phase implements one existing spec's tasks and ticks them | `scripts/spec-complete.sh <spec>` + that feature's tests |

The six specs land in `.kiro/specs/`: `replay-simulator`, `grid-tools`, `agent-team-runtime`, `war-room-ui`, `public-information`, `citizen-voice-line`.

**Recommended:** run `scripts/autopilot.sh run --to 01`, read the specs (they are your Lesson 1 evidence and the contract for everything after), edit anything you disagree with, commit, then `scripts/autopilot.sh run` to build the rest.

---

## A. Autopilot

```bash
export KIRO_API_KEY=...            # from app.kiro.dev (API keys)
scripts/autopilot.sh dry-run | less   # read exactly what will be sent
scripts/autopilot.sh run              # phases 00 -> 09, unattended
scripts/autopilot.sh status           # progress table
scripts/autopilot.sh run --from 03    # resume or re-run from a phase
scripts/autopilot.sh run --to 01      # stop after the specs so you can read them first
```

What happens for each phase in `autopilot/phases.tsv`:
1. The runner sends `AUTOPILOT RUN ...` + the phase brief + the verification command to `minnal-lead` in headless mode (`--no-interactive --v3 --trust-all-tools --output-format stream-json`), with `MINNAL_AUTOPILOT=1`.
2. The lead follows `.kiro/steering/autopilot.md`: writes the Kiro spec (requirements → design with correctness properties → tasks), has it reviewed by `code-reviewer` instead of waiting for you, delegates tasks in parallel by lane, runs the tests → code-review → security-review loops, ticks tasks, updates `docs/plans/autopilot-state.md`.
3. The runner executes the verification command (for spec phases this includes `scripts/spec-complete.sh <spec>`: all required tasks ticked). Fail → retry with the error output (max 3). Pass → `git commit`.
4. Logs: `.kiroster/runs/<phase>-a<attempt>-<time>.jsonl`; decisions: `docs/plans/decisions-log.md`; evidence ledger: `.kiroster/ledger/`.

Safety while unattended: the guard hook blocks `cdk deploy`, live AWS API calls (`@aws-mcp/call_aws`), destructive commands, secrets and any Claude model ID. Agent `deny` rules still apply even with `--trust-all-tools`.

When all phases pass:
```bash
scripts/autopilot.sh deploy          # cdk diff, asks you, then cdk deploy + frontend deploy
```

---

## B. Interactive goal loop (no API key needed)

```bash
kiro-cli chat --agent minnal-lead --trust-all-tools
```

Then paste this **master start prompt** (it runs one phase per goal so each loop has a concrete finish line):

```text
/goal --max 15 AUTOPILOT RUN: execute autopilot/phases/00-foundation.md exactly as .kiro/steering/autopilot.md describes. Do not ask me anything; decide, log decisions in docs/plans/decisions-log.md, and delegate by lane to the specialist agents in parallel. Done means this command passes with exit code 0: uv run pytest -q tests/test_no_claude.py && test "$(ls docs/domain/*.md | wc -l)" -ge 3 && test -f patterns/agui-minnal/config/models.yaml
```

Next, generate all the specs (reuse the same line pattern; every phase file and its verification command are in `autopilot/phases.tsv`):

```text
/goal --max 25 AUTOPILOT RUN: execute autopilot/phases/01-specs.md exactly as .kiro/steering/autopilot.md describes. Write all six Kiro specs (requirements, design with correctness properties, tasks), have code-reviewer review each one, write no code. Done means: scripts/specs-ready.sh
```

Then one build phase per goal, for example:

```text
/goal --max 20 AUTOPILOT RUN: execute autopilot/phases/03-grid-tools.md exactly as .kiro/steering/autopilot.md describes. Implement every task of the existing grid-tools spec through the lane agents and run the gates. Done means: scripts/spec-complete.sh grid-tools && uv run ruff check gateway && uv run pytest -q tests/tools tests/policy
```

Tips: prefer Kiro's native spec runner when you are present (`/spec new grid-tools`, then `/spec run grid-tools` executes tasks in dependency waves); use `/goal` when you want it to keep going until the check passes. `/sessions` shows running work; `Ctrl+G` checks sub-agents.

---

## Things only you can do (the challenge needs them as evidence)

- **Lesson 4 (property-based testing) is IDE-only.** After phases 01, 02 and 05, open the repo in **Kiro IDE**, open each spec, and run the property-test tasks so the IDE records PBT results; screenshot them into `docs/evidence/pbt.md`.
- **Powers:** install the registry powers and import `powers/minnal-gridops` in the IDE; trigger it once.
- **Kiro Web (Bonus 1, paid plan):** Configuration Sync, one cloud session on a GitHub issue, and the nightly replay-regression automation.
- **Deploy** (`scripts/autopilot.sh deploy`), record the video, post, submit.

## If you also cannot use Claude inside Kiro itself

This project already runs without Claude at runtime. If your Kiro account must avoid Claude too, pick another model for the build team: `kiro-cli chat --list-models`, then `kiro-cli settings chat.defaultModel <model-id>` (or `/model` in a session). Kiro offers GPT-5.6 and open-weight models such as MiniMax M2.5, GLM-5, DeepSeek 3.2 and Qwen3 Coder Next; avoid `Auto` if it may route to Claude.
