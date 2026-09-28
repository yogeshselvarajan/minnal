---
name: cap-alert
description: Build Common Alerting Protocol (CAP 1.2) public alerts for power outages and electrical safety during storms, in several languages with Amazon Translate. Use when generating citizen alerts, SMS or public warnings.
---

# CAP 1.2 alerts for outages and electrical safety

Required `alert` fields: `identifier`, `sender`, `sent` (ISO 8601 with offset), `status` (Actual / Exercise / Test), `msgType` (Alert / Update / Cancel), `scope` (Public).
Each `info` block: `language`, `category` (Infra or Safety), `event`, `urgency`, `severity`, `certainty`, `headline`, `description`, `instruction`, `expires`, `area` (`areaDesc` + `polygon`).

Rules:
- One `info` block per language, same `identifier`, `severity`, `expires` and `area` in every block.
- Translate `headline`, `description` and `instruction` with Amazon Translate; keep numbers, times and phone numbers untranslated.
- Use `Exercise` status for demos and replays.
- Safety instruction for downed wires: stay at least 10 metres away, do not touch water near it, call the emergency number.
- Validate against the CAP 1.2 schema before sending.
