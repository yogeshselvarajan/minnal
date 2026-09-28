---
inclusion: auto
description: Power-utility storm restoration on AWS - ICS roles, restoration priority, flood safety, ETRs and CAP alerts.
---

# Storm-restoration conventions

- Model agents on ICS roles (Incident Commander, Planning, Operations, Safety Officer, Logistics, PIO) and give the Safety Officer a veto that is also enforced as code (AgentCore Policy / Cedar), not only in a prompt.
- Humans approve every dispatch and switching action.
- Treat flood polygons as Amazon Location geofences; route crews with avoid-areas.
- Use the skills in this power: restoration-priority, etr-estimation, cap-alert.
