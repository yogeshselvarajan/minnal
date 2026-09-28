---
inclusion: always
---

# Product: Minnal

Minnal is an agentic storm-restoration war room for power utilities. A team of AI agents, organised by the **Incident Command System (ICS)**, helps a utility keep people safe, find failed equipment, restore critical facilities first, route crews around floods, and tell citizens honest restoration times in their own language. Humans approve every switching and dispatch decision.

## Users
- **Incident Commander / control-room engineer** (war-room UI): decides, approves, overrides.
- **Field crew lead** (mobile view): receives work orders and routes.
- **Citizen** (voice line and mobile page): reports outages, hears ETRs and safety advice.

## Definition of done for any feature
1. Traces to a requirement ID in `.kiro/specs/`.
2. Keeps the safety rules in `domain-restoration.md` true.
3. Visible in the war room (the glass box shows it) and covered by tests; stated properties have Hypothesis tests.

## Non-goals
- Real SCADA or switching control. Minnal proposes; humans and existing systems act.
- Storing citizen PII beyond a callback number and location.
- Being a general chatbot.

## Demo scenario
Replay of a Michaung-style cyclone over Chennai (synthetic grid on OpenStreetMap geometry, public weather and cyclone data).
