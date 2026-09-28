# Spec brief: replay-simulator (+ synthetic grid data)

**Goal:** a deterministic storm replay over Chennai that drives the whole demo.

- `data/grid/`: synthetic network on OpenStreetMap geometry: ~4 substations, ~20 feeders, ~200 distribution transformers (DTs), service areas as polygons, with customer counts. Attribute OSM (ODbL).
- `data/facilities/`: hospitals, water pumping, telecom, shelters (from OSM tags), each linked to a DT.
- `data/crews/`: 12 two-person crews with depots and skills.
- `simulator/`: timeline of events on EventBridge bus `minnal-events`: `WeatherTick`, `FloodPolygonUpdated`, `OutageReported` (citizen), `MeterLastGasp`, `DeviceTripped` (hidden truth used only for scoring). Speed multiplier, seed, pause and reset.
- Scoring: after a run, compare inferred failed devices with hidden truth.

**Properties:** P7 idempotent intake (same outage report twice → one outage).
**Acceptance:** `python -m simulator run --scenario michaung-style --speed 60` publishes events and the grid loads on a map.
