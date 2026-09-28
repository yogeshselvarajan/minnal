# ADR-3: Weather snapshot and cyclone-track provenance

- **Status:** Accepted
- **Date:** 2026-09-28
- **Spec:** `.kiro/specs/replay-simulator` (records A4; supports R3.6, R3.9, R7.1)

## Context

The `michaung-style` scenario needs weather values (wind, gust, rain, pressure) and a cyclone track, consumed only from committed files at runtime (R7.1, offline). Every third-party source must carry a title, licence and citation (R3.6), and a missing field must be rejected in a defined order (R3.9). The demo must not depend on any live weather API.

## Decision

- **Weather_Snapshot:** a committed file of weather values, sourced either as (a) a one-time recording of Open-Meteo historical data (CC BY 4.0, attributed) or (b) authored values. The chosen option's title, licence and citation are recorded in the scenario `sources` list.
- **Cyclone track:** either IBTrACS-derived (cited by DOI) or authored, again recorded in `sources` with title/licence/citation.
- **No live weather API** is called at any time; the Replay_Engine reads only the committed snapshot and scenario files (R7.1).
- The `michaung-style` scenario is *inspired by* Cyclone Michaung (Dec 2023), not a reconstruction; timings are compressed for the demo (A10).

### Option chosen when authoring `michaung-style` (task 5.4)

Both weather values and cyclone track were **authored synthetic** (option (b)); no third-party weather values are reproduced, so neither Open-Meteo (CC BY 4.0) nor IBTrACS is a live or copied source. The scenario `sources` list records:

- **Weather_Snapshot** — "Synthetic weather snapshot (authored)", licence **CC0 1.0**, citation "Authored synthetic Weather_Snapshot for the Minnal demo; no third-party weather values reproduced."
- **Cyclone track** — "IMD Cyclone Michaung bulletins (synthetic, Michaung-style)", licence "Government of India Open Data (synthetic derivative)", citation "India Meteorological Department, Cyclone Michaung bulletins, December 2023; weather values here are synthetic and Michaung-style."
- **Grid geometry** — "OpenStreetMap contributors", licence ODbL v1.0.

This matches `data/LICENCE-ODbL` and the Run_Manifest attribution string.

## Alternatives considered

- **Live Open-Meteo at runtime:** breaks offline determinism (R7) and adds a free-tier dependency.
- **Fully authored weather with no real source:** simplest, but loses realism and the CC BY 4.0 provenance trail.

## Consequences

- The run is deterministic and offline; provenance is auditable via `sources` and copied into the Run_Manifest (R3.6).
- The exact source was finalised when the scenario was authored (task 5.4): **authored synthetic** weather (CC0 1.0) and synthetic Michaung-style track (see "Option chosen" above). This ADR fixes the provenance-recording rule regardless of which option is chosen.

## Sources

- Open-Meteo historical weather API terms (CC BY 4.0). Verify at author time.
- IBTrACS (NOAA) best-track data; cite the specific DOI used.
