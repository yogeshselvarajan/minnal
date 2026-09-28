---
inclusion: always
---

# Domain: storm restoration (read before designing any agent behaviour)

## ICS roles → Minnal agents
| ICS role | Minnal agent | Owns |
|---|---|---|
| Incident Commander | `commander` | Objectives per operational period, final plan, requests human approval |
| Planning / situation unit | `hazard` | Weather, cyclone track, flood extent, situation picture |
| Operations / grid branch | `diagnostics` | Outage clustering, failed-device inference |
| Safety Officer | `safety` | Veto on unsafe plans; make-safe tasks |
| Logistics | `dispatch` | Crews, routes, work orders |
| Public Information Officer | `pio` | Citizen and media messages, CAP alerts, ETRs |
| Liaison (public) | `citizen_line` | Voice intake and answers |
| Documentation unit | `scribe` | SITREPs, after-action, lessons |

## Restoration order (standard utility practice)
1. **Make-safe:** downed, submerged or arcing equipment; public danger.
2. **Critical facilities:** hospitals, water and sewage pumping, telecom sites, emergency services, relief shelters.
3. **Substations and main feeders** (most customers restored per repair).
4. **Laterals / distribution transformers.**
5. **Individual service connections.**
Within a tier: customers restored per crew-hour, then time waiting.

## Hard rules
- Never propose energising equipment inside an active flood polygon; preventive shutdown is allowed and must be communicated as a safety measure.
- Never route a crew through an active flood polygon. Minimum two-person crews for storm work.
- ETR: publish a global ETR early, refine by area; never publish an ETR earlier than the data supports; say what is unknown.
- Public alerts follow **CAP 1.2** (identifier, sender, sent, status, msgType, scope, info: category, event, urgency, severity, certainty, area, expires). Same identifier across language variants.
- Downed wire reported by a citizen → immediate safety advice (stay 10 m away, do not touch water near it) and an emergency flag.

## Vocabulary
Feeder, lateral, distribution transformer (DT), ring main unit (RMU), sectionaliser, recloser, ETR, SAIDI, SAIFI, operational period, SITREP, make-safe.

## Sources to put in the knowledge base (verify current versions)
Utility storm-restoration practice guides; CEA safety regulations; NDMA Incident Response System guidelines; CAP 1.2 (OASIS); the state disaster management plan. Cite every source by title and date.
