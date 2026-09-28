---
name: etr-estimation
description: Produce and communicate estimated restoration times (ETRs) for outage areas honestly - global ETR first, refined per area, never earlier than the data supports. Use when designing ETR services, customer messages or agent prompts that mention restoration times.
---

# Honest ETRs

1. **Global ETR** right after landfall: based on storm class, damage reports so far and crew capacity. State the uncertainty ("most customers by ..., some areas longer").
2. **Area ETR** once assessment exists: max over the area's blocking jobs of (queue wait + travel + repair estimate), plus a safety margin.
3. **Monotonic honesty:** an area ETR moves later when new damage appears; it moves earlier only when a job actually completes or a crew is actually assigned, and the message says why.
4. **Unknowns:** if an area is not yet assessed, say "not yet assessed" instead of a time.
5. Every message carries "last updated" time and the next update time.

Guardrail for any agent: never state an ETR that the ETR service did not return.
