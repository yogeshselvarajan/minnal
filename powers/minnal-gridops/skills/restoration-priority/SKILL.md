---
name: restoration-priority
description: Rank storm-restoration jobs for a power utility (make-safe, critical facilities, feeders, laterals, services) and design the ranking code and its property tests. Use when building or reviewing outage prioritisation, crew assignment or restoration planning logic.
---

# Restoration priority

## Tiers (lower number first)
0. Make-safe: downed, submerged or arcing equipment; any public-danger report.
1. Critical facilities: hospitals, water and sewage pumping, telecom sites, emergency services, relief shelters.
2. Substations and main feeders.
3. Laterals and distribution transformers.
4. Individual services.

## Within a tier
Sort by customers restored per crew-hour (descending), then by time waiting (descending), then by job ID for determinism.

## Hard constraints (filter before ranking)
- A job whose device lies inside an active flood polygon is `blocked_flooded` unless the job is make-safe isolation.
- A job needing a crew route through an active flood polygon is `blocked_access` until a safe route exists.

## Properties to test (Hypothesis)
- Critical-facility jobs never rank below a non-critical job of equal or lower effort.
- Ranking is a total, deterministic order (same input, same output).
- Blocked jobs never appear in the dispatchable queue.

```python
def rank_jobs(jobs: list[Job], floods: list[Polygon]) -> RankedQueue: ...
```
