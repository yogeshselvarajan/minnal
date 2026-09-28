# ADR-1: Trimmed OSM extract and Voronoi service areas

- **Status:** Accepted
- **Date:** 2026-09-28
- **Spec:** `.kiro/specs/replay-simulator` (supersedes assumptions A1, A5)

## Context

The replay simulator must build a synthetic Chennai distribution grid offline (R7.3) from OpenStreetMap geometry, keep GeoJSON files small enough for a browser map (R2.7, ≤5 MiB), attribute OSM under the ODbL (R3), and produce Service_Areas that satisfy Property 3 (valid, non-self-intersecting, interior-disjoint, inside the study bbox). OSM has little mapped distribution infrastructure in Chennai (mostly underground cable), so most of the network must be synthesised.

## Decision

1. **Commit a trimmed OSM extract.** `data/osm/` holds a one-time Overpass export of at most 2 MB covering the study area, containing **only** substation sites and features in the six Critical_Facility categories (hospital, water pumping, sewage pumping, telecom, emergency services, relief shelter). Its extraction date is recorded alongside it. No OSM data is fetched at runtime (R7.3).
2. **Synthesise the network.** Feeders, laterals and DTs are generated (`synthetic=true`); only substations and matched facilities may carry `synthetic=false` with a non-empty `osm_id` (R3.3).
3. **Service_Areas by Voronoi tessellation.** Each DT's Service_Area is its cell in the Voronoi tessellation of the DT points, clipped to the study bounding box (`shapely.voronoi_polygons`). By construction the cells cover the bbox with pairwise-disjoint interiors, so Property 3 holds by construction rather than by rejection sampling. Coordinates are rounded to 6 decimal places before serialisation.

## Alternatives considered

- **Full OSM extract of Chennai:** too large for the repo and the map budget; most distribution infrastructure is absent anyway.
- **Synthetic OSM-shaped fixture (no real OSM):** avoids the ODbL entirely but loses realism (substation and facility placement) that the demo relies on.
- **Hand-drawn or random polygon service areas:** would require rejection sampling to guarantee disjoint interiors and could still self-intersect; Voronoi removes that risk.

## Consequences

- Property 3 is guaranteed structurally; the grid build stays deterministic on one OS and lockfile (R1.9).
- The repo carries a small, attributed OSM database under the ODbL (`data/LICENCE-ODbL`, R3.1).
- Voronoi output depends on `shapely`'s geometry engine, so cross-OS grid byte-identity is not guaranteed (only same-OS + lockfile, R1.9); cross-OS byte-identity is required only for Event_Streams and Truth_Stores (R12.1).

## Sources

- OpenStreetMap / Overpass API; ODbL. Verify current Overpass endpoint and licence text at author time.
- `shapely` `voronoi_polygons` documentation (pin the version used in `uv.lock`).
