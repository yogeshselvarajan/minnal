# Requirements Document

## Introduction

The replay simulator is the deterministic, offline storm replay that drives the whole Minnal demo. It builds a synthetic distribution grid for Chennai on OpenStreetMap (OSM) geometry (substations, feeders, laterals, distribution transformers, service areas with customer counts, critical facilities and 12 two-person crews) and replays a "michaung-style" cyclone as a time-ordered stream of versioned domain events: `WeatherTick`, `FloodPolygonUpdated`, `OutageReported`, `MeterLastGasp` and the hidden `DeviceTripped`. Events go to stdout, a file or the EventBridge bus `minnal-events`. The replay supports seed, speed, pause, resume and reset, runs through `python -m simulator`, and scores an agent team's inferred failed devices against the hidden truth.

Scope: grid and crew data in `data/`, simulator code in `simulator/`, event JSON Schemas in `gateway/schemas/events/` (this spec creates the five v1 schemas it emits). Out of scope: outage intake storage, topology trace and ETR (spec `grid-tools`), agents, and the map UI (spec `war-room-ui`), which consumes the GeoJSON produced here.

Sources: `docs/spec-briefs/01-replay-simulator.md`, `docs/domain/open-data-sources.md`, `docs/domain/ics-and-restoration.md`, `docs/domain/flood-safety-and-cap.md`, steering `api-contracts.md`, `domain-restoration.md`, `security.md`, `testing.md`.

## Delivery tiers

Criteria are delivered in two tiers so the challenge build stays focused:

- **Challenge tier (default):** every criterion not marked `[DEFERRED]`. These are gating: they must pass for the spec to be considered done, and their tasks are required (`- [ ]`).
- **`[DEFERRED]` tier (post-challenge):** criteria tagged `[DEFERRED]` are polish or hardening that the demo does not need. Their tasks are optional (`- [ ]*`) and non-gating; a `[DEFERRED]` criterion is neither renumbered nor removed, only tagged. The following are `[DEFERRED]`: **7.6** (EventBridge offline retry-count semantics), **8.9** (schema `.v2` evolution rule), **13.13** (interactive terminal keys), **14.11** (EventBridge per-event 250 ms pacing), **17.9** (`--debug` stack traces), **18.4** (per-Sink end-of-run delivery-count log line), and **all of Requirement 19** (performance limits), which becomes a non-gating benchmark marked `slow`. Requirement 19's demo-facing replacement is the gating demo-timing criterion **13.14**.

## Glossary

- **Simulator**: the whole replay-simulator feature (package `simulator/` plus the data and schemas it owns).
- **Grid_Builder**: the Simulator component that turns the committed OSM extract and a seed into the Synthetic_Grid files in `data/`.
- **OSM_Extract**: a committed snapshot of OpenStreetMap data for the Chennai study area, stored in `data/osm/`, with its extraction date.
- **Synthetic_Grid**: the generated radial distribution network: Substations, Feeders, Laterals, DTs and Service_Areas.
- **Substation**: a grid node that is the root of one or more Feeders.
- **Feeder**: a medium-voltage circuit leaving exactly one Substation, protected by a feeder breaker.
- **Lateral**: a fused tap line branching from exactly one Feeder and supplying one or more DTs.
- **DT**: distribution transformer; the leaf device supplying exactly one Service_Area.
- **Device**: any Substation, Feeder, Lateral or DT; the unit that can trip.
- **Service_Area**: the polygon of customers supplied by one DT, with an integer customer count.
- **Critical_Facility**: a hospital, water pumping station, sewage pumping station, telecom site, emergency-service station or relief shelter, linked to one DT.
- **Crew**: a field crew of exactly two synthetic members, with a depot location and a skill set.
- **Scenario**: a versioned file under `simulator/scenarios/` that defines the study area, simulated start and end time, cyclone track, weather snapshot reference, Flood_Polygons with validity windows, the damage script and signal-generation parameters. `michaung-style` is the shipped Scenario.
- **Weather_Snapshot**: a committed file of weather values (recorded from an open source or authored) referenced by a Scenario.
- **Flood_Polygon**: a scenario-authored GeoJSON polygon with an ID (`FP-<n>`), a status (`active`, `receding`, `cleared`) and a validity window in Simulated_Time.
- **Hidden_Truth**: the set of `DeviceTripped` events and the attribution of every generated outage signal to a tripped Device or to noise; used only by the Scorer.
- **Replay_Engine**: the Simulator component that turns a Scenario, a Synthetic_Grid and a Seed into the ordered Event_Stream and paces its emission.
- **Seed**: an unsigned 32-bit integer that fixes every random choice in the Grid_Builder and Replay_Engine.
- **Simulated_Time**: the scenario clock (ISO 8601 UTC with `Z`), independent of wall-clock time.
- **Speed_Multiplier**: the ratio of Simulated_Time to wall-clock time during pacing, or `max` for unpaced emission.
- **Event_Envelope**: the common wrapper of every emitted event (identity, type, schema version, source, run, sequence, Simulated_Time, payload).
- **Event_Stream**: the ordered sequence of Event_Envelopes produced by one run, comprising the public events sent to Public_Sinks and the truth-only `DeviceTripped` Event_Envelopes written only to the Truth_Store.
- **Canonical_Serialisation**: the single byte representation of an Event_Envelope used by every Sink (UTF-8 JSON, keys sorted, no insignificant whitespace, one event per line).
- **Event_Schema**: a JSON Schema file `gateway/schemas/events/<EventName>.v1.json`.
- **Sink**: a destination for the Event_Stream behind one interface: Stdout_Sink, File_Sink, EventBridge_Sink or Fake_Sink. The Truth_Store is not a Sink.
- **Public_Sink**: any Sink that agents or the UI can read (Stdout_Sink, File_Sink, EventBridge_Sink), and the Fake_Sink when it stands in for one of them in tests.
- **Truth_Store**: the run-local file that records Hidden_Truth, separate from every Public_Sink.
- **EventBridge_Sink**: the Sink that publishes to the EventBridge bus `minnal-events` through an injected client.
- **Fake_Sink**: an in-memory Sink used in tests in place of any AWS client.
- **Outage_Ledger**: a pure fold that groups `OutageReported` events into a set of distinct outages keyed by idempotency key. It is a **test oracle** in `tests/simulator/oracles/` (not Simulator product code); the product idempotent-intake implementation lives in the `grid-tools` `record_outage` tool.
- **Run_Start**: the moment a run begins, after every pre-flight check (CLI arguments, Seed, Speed_Multiplier, Scenario and Synthetic_Grid validation, Sink availability and Truth_Store path) has passed. A command that fails before Run_Start writes no Run_Manifest, no Truth_Store and no event.
- **Run_Manifest**: a JSON file written per run with run ID, Seed, Scenario ID and content hash, Simulator version, final status (`completed`, `interrupted`, `reset` or `failed`), counts per type of events delivered to Public_Sinks and attribution text.
- **Scorer**: the Simulator component that compares an Inferred_Device_Set with Hidden_Truth.
- **Inferred_Device_Set**: a JSON file listing the Device IDs that the agent team believes failed.
- **Score_Report**: the Scorer's output: precision, recall, F1 and the lists behind them.
- **CLI**: the command-line entry point `python -m simulator` with subcommands `build-grid`, `validate`, `run` and `score`.
- **Attribution_Text**: the OSM credit "© OpenStreetMap contributors, data available under the Open Database License (ODbL)" plus the credits of every other source used by the Scenario.

## Requirements

### Requirement 1: Synthetic grid topology

**User Story:** As a geo-data engineer, I want a reproducible synthetic distribution grid over Chennai, so that agents and tools can reason about a realistic radial network without private utility data.

#### Acceptance Criteria

1. WHEN `build-grid` runs with any valid Scenario, THE Grid_Builder SHALL produce exactly the number of Substations, Feeders, Laterals and DTs that the Scenario specifies; for the shipped `michaung-style` Scenario these counts are exactly 4 Substations, 20 Feeders, 200 DTs and the Lateral count that Scenario declares.
2. THE Grid_Builder SHALL connect every Feeder to exactly one Substation, every Lateral to exactly one Feeder and every DT to exactly one Lateral, so that the Synthetic_Grid is a forest of trees rooted at Substations with no cycles.
3. THE Grid_Builder SHALL give every Substation at least one Feeder, every Feeder at least one Lateral and every Lateral at least one DT, so that no Device other than a DT is a leaf of the Synthetic_Grid.
4. THE Grid_Builder SHALL give every Device an ID that is unique across the Synthetic_Grid and prefixed by type (`sub_` for Substation, `fdr_` for Feeder, `lat_` for Lateral, `dt_` for DT).
5. THE Grid_Builder SHALL give every Service_Area a customer count that is an integer within the minimum and maximum declared by the Scenario, and in all cases between 1 and 10,000 inclusive.
6. THE Grid_Builder SHALL set the customer count of every Lateral, Feeder and Substation equal to the sum of the customer counts of the Service_Areas downstream of that Device, so that the sum over all Substations equals the sum over all Service_Areas.
7. THE Grid_Builder SHALL assign exactly one Service_Area to every DT, SHALL give every Service_Area a valid, non-self-intersecting polygon with an area greater than zero, and SHALL keep the interiors of any two Service_Areas disjoint (shared edges or points are allowed).
8. THE Grid_Builder SHALL place the full geometry of every Device and Service_Area inside the study-area bounding box declared by the Scenario, with points on the bounding-box edge counted as inside.
9. WHEN `build-grid` runs twice with the same OSM_Extract, Scenario, Seed and Simulator version on the same operating system and dependency lockfile, THE Grid_Builder SHALL write byte-identical grid output files. (Grid GeoJSON byte-identity is guaranteed within one OS and lockfile; cross-operating-system byte-identity is required only for Event_Streams and Truth_Stores per criterion 12.1, because floating-point geometry libraries may differ across platforms. Coordinates are rounded to 6 decimal places before serialisation and `shapely` is pinned to reduce cross-platform divergence.)
10. WHEN `build-grid` completes successfully, THE Grid_Builder SHALL include the Attribution_Text in the grid output and SHALL exit with status zero after reporting the count of each Device type and Service_Areas written.
11. IF the OSM_Extract or Scenario is missing, unreadable, or lacks the inputs needed to build the requested topology (including a requested count that cannot satisfy criteria 2 and 3, such as fewer DTs than Laterals), THEN THE Grid_Builder SHALL stop with a validation error that names the missing or inconsistent input, THE CLI SHALL exit with code 3, and THE Grid_Builder SHALL leave the existing files in `data/` unchanged.
12. IF the supplied Seed is not an unsigned 32-bit integer (0 to 4,294,967,295 inclusive), THEN THE Grid_Builder SHALL reject the run with a validation error indicating the invalid Seed, before writing any file, and SHALL leave the existing files in `data/` unchanged; THE CLI SHALL exit with code 2 when the Seed was given on the command line (criterion 12.4) and with code 3 when the Seed comes from the Scenario file.

### Requirement 2: Grid data format and location

**User Story:** As a frontend or tools engineer, I want the grid in standard GeoJSON under `data/`, so that the map and Gateway tools can load it without conversion.

#### Acceptance Criteria

1. WHEN the Grid_Builder completes a build, THE Grid_Builder SHALL write every Substation, Feeder, Lateral, DT and Service_Area of the Synthetic_Grid as a Feature in exactly one GeoJSON FeatureCollection file under `data/grid/`, every Critical_Facility in exactly one file under `data/facilities/` and every Crew in exactly one file under `data/crews/`.
2. THE Grid_Builder SHALL write every file as valid GeoJSON per RFC 7946, in WGS84 with `[lon, lat]` coordinate order, with no `crs` member, and with every coordinate inside the Scenario's study area and at most 6 decimal places.
3. THE Grid_Builder SHALL write each Feature with this geometry type: Point for a Substation, DT, Critical_Facility or Crew depot; LineString for a Feeder or Lateral; Polygon for a Service_Area.
4. THE Grid_Builder SHALL write every polygon ring closed (first position equal to last) with at least 4 positions and no self-intersection, with exterior rings counter-clockwise and any interior rings clockwise (right-hand rule).
5. THE Grid_Builder SHALL record on every Feature: an ID unique across all files under `data/grid/`, `data/facilities/` and `data/crews/`; a feature type that is one of Substation, Feeder, Lateral, DT, Service_Area, Critical_Facility or Crew; a parent Device ID where one exists; a positive integer customer count on every Device and Service_Area per criteria 1.5 and 1.6; and the boolean `synthetic` flag per criterion 3.3.
6. THE Grid_Builder SHALL make every parent Device ID refer to an existing Feature of the required type: a Feeder's parent is a Substation, a Lateral's parent is a Feeder, a DT's parent is a Lateral, a Service_Area's parent is a DT, and a Critical_Facility's parent is a DT.
7. THE Grid_Builder SHALL keep each GeoJSON file under `data/` at or below 5,242,880 bytes (5 MiB) as stored on disk, so that a browser map can load it in one request.
8. IF a GeoJSON file the Grid_Builder is about to write would be larger than 5,242,880 bytes, THEN THE Grid_Builder SHALL stop with an error that names the file and its size, THE CLI SHALL exit with code 3, and THE Grid_Builder SHALL leave the files already under `data/` from the previous build unchanged.
9. WHEN the Scenario's Synthetic_Grid is loaded by a round-trip test, THE Simulator SHALL parse each GeoJSON file into grid objects and serialise them back to GeoJSON that parses into grid objects equal to the first parse, with the same Feature count, IDs, feature types, parent Device IDs, customer counts, `synthetic` flags and coordinates.
10. IF a GeoJSON file under `data/` breaks any rule in criteria 2 to 6 when the Simulator loads it, THEN THE Simulator SHALL reject the whole file with an error that names the file, the Feature ID where one is available, and the rule broken, SHALL not load a partial grid, and THE CLI SHALL exit with code 3.

### Requirement 3: OpenStreetMap attribution and ODbL obligations

**User Story:** As the project owner, I want every output that uses OSM data to carry correct credit and licence terms, so that Minnal complies with the ODbL in a public repository and demo.

#### Acceptance Criteria

1. THE Simulator SHALL include `data/LICENCE-ODbL` stating that the Synthetic_Grid is a database adapted from OpenStreetMap, that it is distributed under the ODbL, the full OSM credit of the Attribution_Text, the OSM_Extract date, and a reference to the ODbL licence text.
2. WHEN the Grid_Builder writes a GeoJSON FeatureCollection, THE Grid_Builder SHALL include a top-level `attribution` member containing the Attribution_Text and the OSM_Extract date as an ISO 8601 date, identical in every FeatureCollection written by the same build.
3. WHEN the Grid_Builder writes a Feature, THE Grid_Builder SHALL set `synthetic=true` if its geometry or any of its attributes was invented rather than taken from OSM, and SHALL set `synthetic=false` together with a non-empty source `osm_id` only if both its geometry and its attributes were taken from OSM, so that every Feature carries exactly one boolean `synthetic` value.
4. WHEN a run starts, THE Replay_Engine SHALL write the Attribution_Text, identical to the Attribution_Text defined for the Scenario (OSM credit plus the credit of every other source the Scenario uses), into the Run_Manifest before the first Event_Envelope is emitted to any Sink.
5. WHEN the CLI finishes a `build-grid`, `run` or `score` subcommand, whether it succeeds or fails, THE CLI SHALL print the Attribution_Text exactly once to stderr and SHALL NOT write it to stdout, so that stdout used by the Stdout_Sink contains only Canonical_Serialisation lines.
6. WHERE a Scenario uses a Weather_Snapshot or cyclone track from a third-party source, THE Scenario SHALL record a non-empty source title, licence and citation for each such source, and THE Replay_Engine SHALL copy all three values for every such source into the Run_Manifest.
7. IF the OSM_Extract has no extraction date, THEN THE Grid_Builder SHALL stop with an error message indicating the missing date, THE CLI SHALL exit with code 3, and THE Grid_Builder SHALL NOT write or overwrite any Synthetic_Grid file.
8. WHEN the CLI `validate` subcommand runs, IF any Synthetic_Grid FeatureCollection lacks the `attribution` member, or any Feature lacks a boolean `synthetic` value, or any Feature with `synthetic=false` lacks a non-empty `osm_id`, THEN THE CLI SHALL exit with code 3 and report each offending file and Feature ID.
9. IF a Scenario references a third-party Weather_Snapshot or cyclone track source that is missing its title, licence or citation, THEN THE Replay_Engine SHALL refuse to start the run with an error message naming only the first offending source in Scenario file order and the first missing field of that source (checked in the order title, licence, citation), SHALL emit no Event_Envelope and write no Run_Manifest, and THE CLI SHALL exit with code 3.

### Requirement 4: Critical facilities

**User Story:** As an Incident Commander, I want hospitals, pumping stations, telecom sites, emergency services and shelters placed on the grid, so that restoration priorities in the demo reflect real critical loads.

#### Acceptance Criteria

1. WHEN the Grid_Builder builds the Synthetic_Grid, THE Grid_Builder SHALL derive a Critical_Facility from each OSM_Extract feature inside the Scenario's study area whose tags match the tag-to-category mapping defined in the Scenario. Each Critical_Facility SHALL have a unique Critical_Facility ID, a name (or an empty value when the OSM feature has none), the source OSM feature ID, a single point location that lies within the source feature's geometry, and exactly one category from the closed set `hospital`, `water_pumping`, `sewage_pumping`, `telecom`, `emergency_services`, `relief_shelter`.
2. WHEN the Grid_Builder links a Critical_Facility, THE Grid_Builder SHALL link it to exactly one existing DT whose Service_Area contains the Critical_Facility's point location. If more than one Service_Area contains that location, THE Grid_Builder SHALL choose the DT with the lexicographically smallest DT ID.
3. WHEN the Grid_Builder finishes building the `michaung-style` Synthetic_Grid, THE Synthetic_Grid SHALL contain at least one linked Critical_Facility of each of the 6 categories. This count is taken after the exclusions in criterion 5 and the substitutions in criterion 4.
4. IF the OSM_Extract has no linkable facility for a category after exclusions, THEN THE Grid_Builder SHALL create exactly one synthetic Critical_Facility of that category, flagged `synthetic=true`, with no source OSM feature ID. THE Grid_Builder SHALL select that Critical_Facility's DT as the DT at zero-based index `H(Seed, category) mod n` in the list of all DT IDs sorted in ascending lexicographic order, where `n` is the number of DTs and `H` is a fixed, documented hash of only the Seed and the category name, so that the same Seed and category always select the same DT. Its point location SHALL be inside the Service_Area of the selected DT, and it SHALL be linked to that DT. THE Grid_Builder SHALL record the category and the reason for the substitution in the build log.
5. IF a matched OSM feature's point location lies outside every Service_Area of the Synthetic_Grid, THEN THE Grid_Builder SHALL exclude that feature from the Synthetic_Grid and SHALL record its OSM feature ID, its category and the reason for exclusion in the build log.
6. IF the Scenario's tag-to-category mapping is missing or maps a tag to a value outside the closed category set, THEN THE Grid_Builder SHALL stop the build with an error message naming the invalid mapping entry, THE CLI SHALL exit with code 3, and THE Grid_Builder SHALL leave the existing Synthetic_Grid files in `data/` unmodified.
7. WHEN the Grid_Builder runs twice with the same OSM_Extract, Scenario, Seed and Simulator version on the same operating system and dependency lockfile, THE Grid_Builder SHALL produce identical Critical_Facility output: the same set of IDs, categories, point locations, DT links and synthetic flags. (Same-OS-and-lockfile scope, per criterion 1.9.)

### Requirement 5: Crews

**User Story:** As a dispatch engineer, I want a roster of two-person crews with depots and skills, so that routing and dispatch can be exercised against safe staffing rules.

#### Acceptance Criteria

1. WHEN the Grid_Builder builds the Synthetic_Grid for the `michaung-style` Scenario, THE Grid_Builder SHALL write exactly 12 Crews. Each Crew SHALL have a `crew_`-prefixed ID that no other Crew uses, one depot location given as a single point inside the Scenario's study area, and between 1 and N distinct skills, where N is the number of skills in the closed skill set defined in the Scenario.
2. [SAFETY] WHEN the Grid_Builder writes Crews, THE Grid_Builder SHALL give every Crew exactly two members.
3. [SAFETY] IF a Scenario or crew file defines a Crew with any member count other than exactly two, THEN THE Simulator SHALL reject the file with a validation error that names the Crew ID and the member count it found, SHALL write no Synthetic_Grid or crew output, and THE CLI SHALL exit with code 3.
4. [SAFETY] IF a Scenario defines a Crew depot that lies inside or on the boundary of any Flood_Polygon at any Simulated_Time within that Flood_Polygon's validity window, THEN THE Simulator SHALL reject the Scenario with a validation error that names the Crew ID and the Flood_Polygon ID (`FP-<n>`), SHALL write no Synthetic_Grid or crew output, and THE CLI SHALL exit with code 3.
5. WHEN the Grid_Builder writes Crews, THE Grid_Builder SHALL identify each crew member only by a synthetic member ID that no other member uses across all Crews, and SHALL NOT write any personal name, phone number, email address or other contact attribute for any member.
6. IF a Scenario or crew file assigns a Crew a skill that is not in the Scenario's closed skill set, or assigns a Crew zero skills, THEN THE Simulator SHALL reject the file with a validation error that names the Crew ID and the offending skill (or the empty skill list), SHALL write no Synthetic_Grid or crew output, and THE CLI SHALL exit with code 3.
7. IF a Scenario or crew file defines a Crew depot outside the Scenario's study area, THEN THE Simulator SHALL reject the file with a validation error that names the Crew ID, SHALL write no Synthetic_Grid or crew output, and THE CLI SHALL exit with code 3.
8. IF a Scenario or crew file uses the same Crew ID or crew member ID more than once, THEN THE Simulator SHALL reject the file with a validation error that names the duplicated ID, SHALL write no Synthetic_Grid or crew output, and THE CLI SHALL exit with code 3.

### Requirement 6: Scenario definition and validation

**User Story:** As a geo-data engineer, I want the storm defined in a versioned scenario file, so that the replay is reviewable, reproducible and editable without code changes.

#### Acceptance Criteria

1. THE Simulator SHALL ship the `michaung-style` Scenario containing a Scenario ID, a version string, a study-area bounding box, a Simulated_Time start and end (ISO 8601 UTC with `Z`), a cyclone track of at least 2 timestamped positions, a Weather_Snapshot reference, at least 1 Flood_Polygon with an ID of the form `FP-<n>`, a status (`active`, `receding` or `cleared`) and a validity window, and a damage script that lists each Device ID to trip with its Simulated_Time.
2. THE `michaung-style` Scenario SHALL define approach, peak-wind, flooding and flood-recession phases as named, contiguous, non-overlapping Simulated_Time intervals in that order that together span the Scenario start to end, and SHALL include at least one Substation located inside a Flood_Polygon during that polygon's validity window, at least one Critical_Facility whose DT is at or downstream of a Device tripped by the damage script, at least one citizen downed-wire report, and at least one pair of citizen reports that share an idempotency key so that the Outage_Ledger counts them as one outage.
3. WHEN `validate` runs on a Scenario against a Synthetic_Grid, THE Simulator SHALL check the Scenario against its schema and against the Synthetic_Grid, SHALL finish within 10 seconds for the `michaung-style` Scenario, and SHALL exit with code 0 and report the Scenario ID and version when both checks pass.
4. IF a Scenario references a Device ID, Flood_Polygon ID or Weather_Snapshot that does not exist, THEN THE Simulator SHALL reject the Scenario with a validation error that names every missing reference by kind and ID (not only the first), SHALL exit with code 3, and SHALL write no files.
5. IF a Scenario has an end time at or before its start time, a timestamp that is not ISO 8601 UTC with `Z`, a Flood_Polygon validity window whose end is at or before its start, or a Flood_Polygon validity window that begins before the Scenario start or ends after the Scenario end, THEN THE Simulator SHALL reject the Scenario with a validation error that names the offending field and Flood_Polygon ID (where applicable) and SHALL exit with code 3.
6. THE Scenario SHALL label every Flood_Polygon as `derived: scenario-authored` so that consumers can show the layer as synthetic.
7. IF a Scenario is missing a required field, contains a field of the wrong type, contains two Flood_Polygons with the same ID, contains a Flood_Polygon whose geometry is not a closed, non-self-intersecting polygon, or contains a Flood_Polygon without the `derived: scenario-authored` label, THEN THE Simulator SHALL reject the Scenario with a validation error that names each offending field and SHALL exit with code 3.
8. IF `run` is invoked with a Scenario that fails any check in criteria 3 to 7, THEN THE Simulator SHALL exit with code 3 before emitting any Event_Envelope to any Sink and before writing the Truth_Store or Run_Manifest.
9. WHEN a new Scenario file that passes `validate` is added under `simulator/scenarios/`, THE Simulator SHALL accept that Scenario by its Scenario ID in `validate` and `run` without any change to Simulator code.

### Requirement 7: Offline operation

**User Story:** As the project owner, I want the replay to run with no network access, so that the demo is deterministic, cheap and independent of Open-Meteo's free tier or any other live service.

#### Acceptance Criteria

1. THE Replay_Engine SHALL read weather values only from the Weather_Snapshot referenced by the selected Scenario and from Scenario files committed to the repository, and SHALL NOT request weather data from Open-Meteo or any other network source.
2. WHILE outbound network access is blocked, THE Simulator SHALL complete `build-grid`, `validate`, `run` (with the Stdout_Sink or File_Sink, at any Speed_Multiplier) and `score` with exit code 0 and zero outbound network connection attempts.
3. THE Grid_Builder SHALL read OSM data only from the committed OSM_Extract and SHALL NOT request OSM data from any network source.
4. THE Simulator's pure modules (grid building, scenario validation, event generation, ordering, serialisation, Outage_Ledger and scoring) SHALL import nothing from `boto3` or `botocore`.
5. IF the Weather_Snapshot referenced by the selected Scenario or the OSM_Extract is missing or unreadable, THEN THE Simulator SHALL exit with code 3, report an error naming the missing input, emit zero events to any Public_Sink, leave existing Synthetic_Grid files unchanged, and SHALL NOT fall back to any network source.
6. [DEFERRED] WHILE outbound network access is blocked, IF `run` is invoked with the EventBridge_Sink, THEN THE Simulator SHALL make, per publish request, exactly 4 attempts (1 initial attempt plus exactly 3 retries, with no attempt skipped or ended early) before giving up, exit with code 4 within 60 seconds of wall-clock time, and report an error indicating that the bus `minnal-events` is unreachable.
7. WHEN `run` is executed with the same Seed, Scenario and Synthetic_Grid once with outbound network access blocked and once with it available, THE Simulator SHALL produce byte-identical Event_Streams under Canonical_Serialisation.

### Requirement 8: Event envelope and versioning

**User Story:** As an agent engineer, I want every event in one versioned envelope that follows the Minnal contracts, so that consumers can route, deduplicate and validate events uniformly.

#### Acceptance Criteria

1. THE Replay_Engine SHALL wrap every event in an Event_Envelope with exactly these members: `event_id` (ULID prefixed `evt_`), `event_type`, `schema_version`, `source` equal to `minnal.simulator`, `run_id` (ULID prefixed `run_`), `incident_id` (ULID prefixed `inc_`), `correlation_id` (ULID prefixed `corr_`), `sequence`, `sim_time` and `payload`, with no member null or absent.
2. THE Replay_Engine SHALL set `schema_version` to the integer `1` for every event type defined in this spec.
3. THE Replay_Engine SHALL write every timestamp, including `sim_time`, as ISO 8601 UTC in the form `YYYY-MM-DDTHH:MM:SSZ` (whole seconds, `Z` suffix, no numeric offset), and every duration as a non-negative integer with its unit (`_seconds` or `_minutes`) in the field name.
4. THE Replay_Engine SHALL write every geometry as GeoJSON in WGS84 with `[lon, lat]` order, longitude within -180 to 180 and latitude within -90 to 90.
5. FOR ALL pairs of runs with the same Scenario ID, Scenario content hash, Seed and reset count, THE Replay_Engine SHALL produce identical `run_id`, `incident_id`, `correlation_id` and per-`sequence` `event_id` values. THE Replay_Engine SHALL derive `run_id`, `incident_id` and `correlation_id` only from the Scenario ID, Scenario content hash, Seed and reset count, and SHALL derive each `event_id` only from those values plus the event's public `sequence` (criterion 11.3), or, for a truth-only record, its Truth_Store `sequence` (criterion 11.4) together with a fixed marker that separates Truth_Store records from public events, with no wall-clock value in any Event_Envelope.
6. THE Simulator SHALL create one Event_Schema per emitted event type, with `additionalProperties: false` on the envelope and on every object inside `payload`, `required` lists, and enums for closed sets. The four **public** event schemas (`WeatherTick`, `FloodPolygonUpdated`, `OutageReported`, `MeterLastGasp`) SHALL live at `gateway/schemas/events/<EventName>.v1.json`. The **truth-only** `DeviceTripped` schema SHALL live at `simulator/schemas/truth/DeviceTripped.v1.json` and SHALL NOT appear under `gateway/schemas/`, so that no public contract directory names the hidden event.
7. FOR ALL Event_Envelopes the Replay_Engine emits, THE Event_Envelope SHALL validate against the Event_Schema named by its `event_type` and `schema_version`.
8. IF an Event_Envelope fails Event_Schema validation before emission, THEN THE Replay_Engine SHALL withhold that Event_Envelope from every Sink, stop the run with no further Event_Envelopes emitted, leave already-emitted Event_Envelopes unchanged, and report an error that names the `event_type`, the `sequence` and the failing field, and THE CLI SHALL exit with code 3.
9. [DEFERRED] IF an Event_Schema change removes, renames or retypes a field, adds a required field, or removes an enum value, THEN THE Simulator SHALL publish the change as a new `<EventName>.v2.json` schema file, keep the `.v1` file byte-identical, and set `schema_version` to `2` on Event_Envelopes of that type that conform to the new schema.
10. FOR ALL Event_Envelopes, including payloads with non-ASCII UTF-8 text, parsing the Canonical_Serialisation of the Event_Envelope SHALL produce an Event_Envelope equal to the original, and serialising that result SHALL produce the same bytes (round trip).
11. FOR ALL Event_Streams, THE Replay_Engine SHALL assign `sequence` as a positive integer starting at 1 for the first Event_Envelope sent to Public_Sinks in a run and increasing by exactly 1 per Event_Envelope sent to Public_Sinks, with no gaps or repeats, and SHALL restart `sequence` at 1 after a reset; truth-only records are numbered separately per criterion 11.4.
12. FOR ALL Event_Envelopes emitted across every run and reset of the same Scenario and Seed, THE Replay_Engine SHALL assign a distinct `event_id`, and SHALL assign a different `run_id` for each distinct reset count.
13. IF the Canonical_Serialisation of an Event_Envelope exceeds 262,144 bytes, THEN THE Replay_Engine SHALL treat it as an Event_Schema validation failure and apply the stop behaviour and exit code of criterion 8.

### Requirement 9: Event payloads

**User Story:** As an agent engineer, I want each event type to carry the fields the Hazard, Diagnostics and Citizen Line agents need, so that they can build a situation picture from the stream alone.

#### Acceptance Criteria

1. THE Replay_Engine SHALL include in each `WeatherTick` the cyclone centre position as a GeoJSON point, wind speed in km/h from 0 to 350, gust speed in km/h from the tick's wind speed up to 400, rainfall in mm/h from 0 to 500, pressure in hPa from 870 to 1085, and a Weather_Snapshot source reference that names the Weather_Snapshot file and the snapshot record time whose values the tick carries, with every weather value equal to the value in that snapshot record.
2. THE Replay_Engine SHALL include in each `FloodPolygonUpdated` the Flood_Polygon ID, the GeoJSON polygon geometry identical to the Scenario's geometry for that Flood_Polygon ID, the status (`active`, `receding` or `cleared`) that the Scenario defines for that Flood_Polygon at the event's `sim_time`, a validity window whose start is earlier than its end, and the `derived` label with the value `scenario-authored`.
3. THE Replay_Engine SHALL include in each `OutageReported` a report ID that is unique within the run, an `idempotency_key`, the report location as a GeoJSON point inside the Scenario's study-area bounding box, a symptom from a closed set that includes `no_power`, `partial_power`, `downed_wire`, `sparking` and `submerged_equipment`, an `is_emergency` flag that is `false` for symptoms `no_power` and `partial_power`, and a synthetic callback token of at most 64 characters.
4. [SAFETY] WHEN the Replay_Engine emits an `OutageReported` with symptom `downed_wire`, `sparking` or `submerged_equipment`, THE Replay_Engine SHALL set `is_emergency` to `true`.
5. THE Replay_Engine SHALL include in each `MeterLastGasp` a synthetic meter ID, the ID of the supplying DT, which SHALL exist in the Synthetic_Grid, and the meter location as a GeoJSON point inside that DT's Service_Area.
6. THE Replay_Engine SHALL include in each `DeviceTripped` the Device ID of a Device that exists in the Synthetic_Grid, the Device type equal to that Device's type in the Synthetic_Grid, a cause from the closed set `wind`, `flood`, `vegetation`, `equipment_failure`, and a list (possibly empty) of the IDs of the outage signals attributed to the Device, where every listed ID identifies an `OutageReported` or `MeterLastGasp` emitted in the same run and no outage signal is listed under more than one Device.
7. THE Replay_Engine SHALL exclude personal names and real phone numbers from every event payload, such that no payload field holds a person's name and no free-text or callback-token string value contains a run of 7 or more consecutive digits, with or without a leading `+`. The digit-run check applies to every payload string field except fields the Event_Schema marks as identifiers (`*_id`, `idempotency_key`) and timestamps.
8. WHEN the Scenario marks a citizen report as a duplicate, THE Replay_Engine SHALL emit the duplicate as an `OutageReported` with a new report ID and the same `idempotency_key` as the original report, and SHALL give distinct (non-duplicated) reports distinct `idempotency_key` values.
9. THE Replay_Engine SHALL exclude from every `OutageReported` and `MeterLastGasp` payload the ID of the Device the signal is attributed to in Hidden_Truth, any noise attribution and any `DeviceTripped` cause.
10. IF the Replay_Engine generates a payload that references a Device ID, DT ID or Flood_Polygon ID absent from the Synthetic_Grid or Scenario, or holds a numeric value outside the bounds in criteria 1 to 6, THEN THE Replay_Engine SHALL stop the run at the moment of detection, with no further event generation or processing, no emission of that event or any later event to any Sink, and no Truth_Store write for that event or any later event, and SHALL report a validation error that names the event type and the offending field, and THE CLI SHALL exit with code 3.

### Requirement 10: Consistency between hidden truth and observable signals

**User Story:** As a QA engineer, I want observable signals to follow from the hidden damage, so that scoring is meaningful and the Diagnostics agent can in principle recover the truth.

#### Acceptance Criteria

1. THE Replay_Engine SHALL emit a `MeterLastGasp` for a meter only when the meter's Service_Area is supplied by a Device on the radial path from its DT up to and including its Substation, and a `DeviceTripped` for that Device exists in the same Event_Stream with a `sim_time` at or before the `MeterLastGasp` `sim_time`.
2. THE Replay_Engine SHALL record in the Truth_Store, for every `OutageReported` and `MeterLastGasp` in the Event_Stream, exactly one attribution: either the ID of a Device whose `DeviceTripped` occurs in the same run at or before the signal's `sim_time`, or the label `noise`.
3. WHERE the Scenario sets a noise rate between 0 and 100 events per simulated hour inclusive, THE Replay_Engine SHALL generate, in every complete simulated hour between the Scenario start and end time, a number of `OutageReported` events attributed to `noise` that differs from the configured rate by no more than one; IF the Scenario sets no noise rate, THEN THE Replay_Engine SHALL generate zero `noise` events.
4. [SAFETY] THE Replay_Engine SHALL assign cause `flood` to a `DeviceTripped` only when the Device's location (the point of a Substation or DT, or any point of the line geometry of a Feeder or Lateral) lies inside or on the boundary of a Flood_Polygon whose status is `active` and whose validity window contains the `DeviceTripped` `sim_time`.
5. THE Replay_Engine SHALL assign cause `wind` to a `DeviceTripped` only when the gust speed of the most recent `WeatherTick` at or before the `DeviceTripped` `sim_time` covering the Device location is greater than or equal to the Scenario's wind-damage threshold, expressed in the same unit; IF no such `WeatherTick` exists, THEN THE Replay_Engine SHALL NOT assign cause `wind`.
6. WHEN the Scenario marks a citizen report as a duplicate, THE Replay_Engine SHALL emit a second `OutageReported` with the same `idempotency_key` as the original and a payload identical to the original except for the report ID (criterion 9.8), a new event identity and sequence number, a `sim_time` strictly later than the original and no later than the Scenario end time, and SHALL record in the Truth_Store the same attribution as the original.
7. FOR ALL Event_Streams, folding the `OutageReported` events into the Outage_Ledger SHALL yield the same set of outages as folding them with every event applied twice, and the number of outages SHALL equal the number of distinct `idempotency_key` values among those events (idempotent intake, P7). The product implementation of idempotent intake belongs to the `grid-tools` `record_outage` tool (BLUEPRINT P7); in this spec the Outage_Ledger and Property 7 exist only as a **test oracle** under `tests/simulator/oracles/`, exercising the Simulator's emitted stream, and are not part of the Simulator's product code.
8. [SAFETY] IF the Scenario damage script assigns cause `flood` or `wind` to a Device trip that would violate criterion 4 or 5, THEN THE CLI `validate` and `run` subcommands SHALL reject the Scenario with exit code 3 and an error message identifying the Device ID, the trip `sim_time` and the violated cause rule, and SHALL emit no events to any Sink and write no Truth_Store.
9. IF the Scenario sets a noise rate that is negative, non-numeric or greater than 100 events per simulated hour, THEN THE CLI `validate` and `run` subcommands SHALL reject the Scenario with exit code 3 and an error message identifying the invalid noise rate, and SHALL emit no events to any Sink.
10. IF the Scenario marks a duplicate citizen report whose referenced original report does not exist in the Scenario, THEN THE CLI `validate` and `run` subcommands SHALL reject the Scenario with exit code 3 and an error message identifying the unresolved duplicate, and SHALL emit no events to any Sink.

### Requirement 11: Ordering by simulated time

**User Story:** As an agent engineer, I want events delivered in simulated-time order with a gap-free sequence, so that consumers can detect loss and reason causally.

#### Acceptance Criteria

1. THE Replay_Engine SHALL emit events in non-decreasing `sim_time`. This applies to the combined order of events sent to Public_Sinks and entries written to the Truth_Store within one run, and it holds for every Speed_Multiplier, including `max`.
2. WHEN two or more events in one run have equal `sim_time`, THE Replay_Engine SHALL order them first by the fixed event-type order (`WeatherTick`, `FloodPolygonUpdated`, `DeviceTripped`, `MeterLastGasp`, `OutageReported`) then by the Canonical_Serialisation of their `payload` in ascending code-point order, and finally by the Generation_Key defined in assumption A14 in ascending order. Because every event in a run has a distinct Generation_Key, this ordering SHALL be a total order in which no two events of one run compare equal, and the same Scenario, Synthetic_Grid and Seed SHALL therefore always give the same order.
3. THE Replay_Engine SHALL number the events sent to Public_Sinks with a `sequence` that starts at 1 for each run and increases by exactly 1 per public event, with no repeated or skipped values, so that gaps reveal nothing about Hidden_Truth. Every Public_Sink in the run SHALL receive the same `sequence` value for the same event.
4. THE Replay_Engine SHALL number Truth_Store entries with their own `sequence`, starting at 1 per run and increasing by exactly 1 per entry. With each entry it SHALL record the public `sequence` of the last public event emitted before that entry, or 0 if no public event has been emitted yet in the run.
5. [SAFETY] THE Replay_Engine SHALL emit the `FloodPolygonUpdated` that makes a Flood_Polygon `active` before any outage signal (`MeterLastGasp` or `OutageReported`) that the Truth_Store attributes to a Device tripped by cause `flood` inside that Flood_Polygon. The `FloodPolygonUpdated` SHALL have both a lower public `sequence` and a `sim_time` less than or equal to that of the outage signal. The Truth_Store entry for the `DeviceTripped` SHALL record a last public `sequence` greater than or equal to the `sequence` of that `FloodPolygonUpdated`.
6. WHEN the Scenario defines a Flood_Polygon status change at a `sim_time` between the Scenario start and end time inclusive, THE Replay_Engine SHALL emit exactly one `FloodPolygonUpdated` for that change. The event SHALL have a `sim_time` equal to the Scenario-defined time and carry the new status. This includes the transition to `cleared`.
7. [SAFETY] IF a Scenario's damage script trips a Device by cause `flood` at a `sim_time` earlier than the time its Flood_Polygon becomes `active`, or at a time when that Flood_Polygon is not `active`, or at a location outside every `active` Flood_Polygon, THEN THE Simulator SHALL reject the Scenario before emitting any event. The rejection SHALL be an error that identifies the Device ID and the Flood_Polygon ID concerned, and THE CLI SHALL exit with code 3. No Public_Sink and no Truth_Store SHALL receive output for that run.
8. WHEN a run is paused and resumed, or its Speed_Multiplier changes, THE Replay_Engine SHALL continue the public `sequence` and the Truth_Store `sequence` from the next unused number, without resetting, skipping or repeating any value. The event order SHALL be identical to an uninterrupted run with the same Scenario, Synthetic_Grid and Seed.
9. IF two or more status changes for the same Flood_Polygon are defined at the same `sim_time`, or a Scenario defines a status change that does not follow the order `active`, then `receding`, then `cleared`, THEN THE Simulator SHALL reject the Scenario before emitting any event. The rejection SHALL be an error that identifies the Flood_Polygon ID and the conflicting times, and THE CLI SHALL exit with code 3.

### Requirement 12: Determinism

**User Story:** As a QA engineer, I want identical inputs to give an identical event stream, so that replays, evaluations and regressions are reproducible.

#### Acceptance Criteria

1. WHEN two runs are started with the same Seed (any value from 0 to 4,294,967,295), the same Scenario content hash, the same Synthetic_Grid files, the same reset count and the same Simulator version, THE Replay_Engine SHALL write Event_Streams to the File_Sink that are byte-identical (equal length and equal content, byte for byte), and SHALL write Truth_Store files that are byte-identical. This byte-identity SHALL hold across operating systems (Windows, macOS, Linux): Event_Streams and Truth_Stores are byte-identical regardless of the host OS, achieved by rounding every coordinate to 6 decimal places before serialisation, pinning `shapely`, and writing UTF-8 without a BOM terminated by a single line-feed (criterion 14.2).
2. WHEN a run is paced at any permitted Speed_Multiplier, including `max`, and receives any number of pause and resume commands at any Simulated_Time, THE Replay_Engine SHALL emit an Event_Stream byte-identical to the one emitted by an uninterrupted run at Speed_Multiplier `max` with the same inputs listed in criterion 1.
3. WHEN a run is started without a Seed argument, THE CLI SHALL use the default Seed recorded in the Scenario, SHALL write the Seed used to stderr as one base-10 integer before the first event is emitted, SHALL NOT write the Seed to stdout, and SHALL record the Seed in the Run_Manifest.
4. IF the Seed argument is not a base-10 integer in the range 0 to 4,294,967,295 inclusive (for example a negative, fractional or non-numeric value, or a value above 4,294,967,295), THEN THE CLI SHALL exit with code 2 and a usage error that states the accepted range. It SHALL do this before emitting any event and without writing any Event_Stream, Truth_Store or Run_Manifest file.
5. THE Grid_Builder and THE Replay_Engine SHALL draw every random value from generators seeded only from the Seed and the Scenario ID. They SHALL NOT use wall-clock time, host name, process ID, environment variables or operating-system entropy to decide Synthetic_Grid content, the content of any event, the Simulated_Time of any event or the position of any event in the Event_Stream.
6. THE Replay_Engine SHALL derive the run and event identity values of every Event_Envelope only from the inputs listed in criterion 8.5 (Scenario ID, Scenario content hash, Seed, reset count, and the event's public `sequence` or, for a truth-only record, its Truth_Store `sequence`), and no Event_Envelope SHALL contain a wall-clock timestamp.
7. WHEN the same run inputs listed in criterion 1 are emitted to the Stdout_Sink, the File_Sink and the Fake_Sink, THE Replay_Engine SHALL deliver the same ordered sequence of Canonical_Serialisation lines to each of those Sinks, byte for byte.
8. WHEN the Grid_Builder runs twice with the same OSM_Extract, the same Scenario, the same Seed and the same Simulator version on the same operating system and dependency lockfile, THE Grid_Builder SHALL write byte-identical Synthetic_Grid files. Cross-operating-system byte-identity is required for Event_Streams and Truth_Stores (criterion 1) but not for grid GeoJSON files, per criterion 1.9.

### Requirement 13: Speed, pause, resume and reset

**User Story:** As a demo operator, I want to control replay speed and pause, resume or reset the storm, so that I can narrate the demo and rerun it on demand.

#### Acceptance Criteria

1. WHILE a run is emitting events with a numeric Speed_Multiplier from 1 to 3,600 inclusive, THE Replay_Engine SHALL emit each event no earlier than, and no more than 250 ms after, the instant on its injected clock at which the pacing clock reaches the event's `sim_time`. The pacing clock starts at the Scenario start when the run starts and advances Simulated_Time by the Speed_Multiplier times the elapsed injected-clock time, excluding paused intervals.
2. WHEN a run starts with Speed_Multiplier `max`, THE Replay_Engine SHALL emit all events in `sequence` order without advancing or waiting on its injected clock between events.
3. IF the Speed_Multiplier given to `run` is neither `max` nor a decimal number from 1 to 3,600 inclusive, THEN THE CLI SHALL reject the value with a usage error that names the accepted range, exit with code 2, emit no event to any Sink and write no Run_Manifest.
4. WHEN a pause command is received while events are being emitted, THE Replay_Engine SHALL emit no event later than 250 ms after the command's receipt on its injected clock. THE Replay_Engine SHALL also freeze Simulated_Time at its value at receipt until a resume or reset command is received.
5. WHEN a resume command is received while paused, THE Replay_Engine SHALL continue from the next unemitted event in `sequence` order, with Simulated_Time continuing from its frozen value, and with no event skipped or repeated.
6. WHEN a reset command is received, THE Replay_Engine SHALL stop the current run, finalise its Run_Manifest with status `reset` and the counts per event type of the events delivered to Public_Sinks so far, and start a new run only after the write of that Run_Manifest has completed. For the new run, THE Replay_Engine SHALL return Simulated_Time to the Scenario start, increment the reset count, derive a new `run_id`, restart `sequence` at 1, write a new Run_Manifest and a new Truth_Store, and keep the Speed_Multiplier and paused-or-emitting state that were in effect before the reset.
7. WHEN a reset run uses the same Seed and Scenario as the run before it, THE Replay_Engine SHALL emit the same event types, `sim_time` values and payloads, in the same `sequence` order, as that earlier run.
8. WHEN a speed-change command with a value accepted by criterion 3 is received during a run, THE Replay_Engine SHALL pace every unemitted event at the new Speed_Multiplier, starting from the current Simulated_Time, with no jump in Simulated_Time and no event skipped or repeated.
9. THE Replay_Engine SHALL accept pause, resume, reset and speed-change commands through its Python API.
10. WHEN the last event of the Scenario is emitted, THE Replay_Engine SHALL flush every Sink, finalise the Run_Manifest with the counts per event type of the events delivered to Public_Sinks, end the run and stop accepting commands. The CLI SHALL then exit with code 0.
11. IF a command is received that does not apply to the current state (pause while paused, resume while emitting, or any command after the run has ended), THEN THE Replay_Engine SHALL ignore it, leave Simulated_Time, `sequence`, Speed_Multiplier and the paused-or-emitting state unchanged, and report that the command was ignored.
12. IF a speed-change command carries a value that criterion 3 does not accept, THEN THE Replay_Engine SHALL reject the command with an error indicating the accepted range and keep the current Speed_Multiplier and pacing unchanged.
13. [DEFERRED] WHILE `run` is attached to a terminal, THE CLI SHALL map pause, resume, reset and speed-change to distinct interactive keyboard commands on stdin and list those keys in the `run` help output.
14. WHEN `run` executes the `michaung-style` Scenario with Speed_Multiplier 360, the Stdout_Sink or File_Sink and the Scenario's default Seed, THE Replay_Engine SHALL replay the complete Scenario in at most 3 minutes of wall-clock time and SHALL deliver between 1,000 and 3,000 public events (inclusive) to the Public_Sink; the default Speed_Multiplier when `--speed` is omitted remains 60 (criterion 17.1).

### Requirement 14: Sinks

**User Story:** As a platform engineer, I want the event stream delivered to stdout, a file or EventBridge through one interface, so that local runs, tests and the deployed demo use the same replay code.

#### Acceptance Criteria

1. WHEN a run starts, THE Replay_Engine SHALL deliver events through a single Sink interface to 1 to 3 Sinks chosen at run start (at most one each of Stdout_Sink, File_Sink and EventBridge_Sink, or the Fake_Sink in tests), and every selected Sink SHALL receive the same Event_Envelopes in the same Event_Stream order.
2. THE Stdout_Sink and File_Sink SHALL write each event as one line of Canonical_Serialisation (JSON Lines), encoded as UTF-8 without a byte-order mark and terminated by a single line-feed character on every operating system, so that File_Sink output is byte-identical across platforms.
3. THE EventBridge_Sink SHALL publish each event to the bus `minnal-events` with `Source` equal to `minnal.simulator`, `DetailType` equal to the event's `event_type` and `Detail` equal to the Canonical_Serialisation of the Event_Envelope, with the entries of each publish request in Event_Stream order.
4. THE EventBridge_Sink SHALL send at most 10 entries and at most 256 KB (262,144 bytes) of total entry size per publish request, and IF a single entry exceeds 256 KB, THEN THE EventBridge_Sink SHALL send neither that entry nor any later entry, SHALL stop the run, and THE CLI SHALL exit with code 3 and a message on stderr naming the event ID and its size.
5. IF EventBridge reports failed entries with a throttling, service-unavailable, internal or timeout error, or the publish request fails with such a transient error, THEN THE EventBridge_Sink SHALL rely on botocore's standard retry mode (configured on the injected client) for request-level transient errors, and SHALL additionally re-send only the `PutEvents` failed entries, with unchanged entry content, until every entry has succeeded or exactly 3 entry-level re-sends have been made after the initial attempt (so that an entry that keeps failing receives exactly 1 initial attempt and exactly 3 re-sends), applying exponential backoff between entry-level re-sends measured against the injected clock; the backoff and any retry SHALL NOT change the Event_Stream bytes or order.
6. IF entries still fail after the initial attempt and exactly 3 retries, THEN THE EventBridge_Sink SHALL stop the run, THE Replay_Engine SHALL send no further event to any Sink, flush the other selected Sinks and record the run as failed in the Run_Manifest, and THE CLI SHALL exit with code 4 and a message on stderr naming the first failed event ID and the EventBridge error code.
7. THE EventBridge_Sink SHALL receive its EventBridge client by injection, and THE Simulator's tests SHALL use the Fake_Sink or a stubbed client and SHALL pass with no AWS credentials configured and no live AWS call.
8. THE Simulator SHALL write all log output to stderr so that the Stdout_Sink output contains only events.
9. IF the CLI Sink selection names an unknown Sink type, gives `--sink` with no value, chooses `--sink file` without `--out` (criterion 17.7), or gives a File_Sink path that already exists, THEN THE CLI SHALL reject the configuration with exit code 2 and a usage error naming the offending Sink or option before emitting any event, and SHALL leave any existing file at the File_Sink path unchanged. A File_Sink path that cannot be created or written, and missing EventBridge credentials or bus, are Sink errors handled by criterion 17.6 (exit code 4).
10. IF EventBridge rejects a publish request or entry with a non-transient error (access denied, bus not found or request validation), THEN THE EventBridge_Sink SHALL not retry and SHALL stop the run as in criterion 6, and THE CLI SHALL exit with code 4.
11. [DEFERRED] WHILE a run is paced with a numeric Speed_Multiplier, THE EventBridge_Sink SHALL send each event within 250 ms of its emission by the Replay_Engine, measured against the injected clock, and SHALL NOT hold an event back to fill a batch of 10.
12. IF the Replay_Engine is started through its Python API with no Sink, or with the EventBridge_Sink selected and no injected EventBridge client, THEN THE Replay_Engine SHALL raise a configuration error naming the missing Sink or client before emitting any event and before writing the Truth_Store or Run_Manifest.

### Requirement 15: Hidden truth isolation

**User Story:** As a QA engineer, I want the true failed devices kept away from everything agents can see, so that the Diagnostics agent is scored on inference and not on leaked answers.

#### Acceptance Criteria

1. [SAFETY] WHEN the Replay_Engine generates a `DeviceTripped` event or attributes an `OutageReported` or `MeterLastGasp` event to a tripped Device or to noise, THE Replay_Engine SHALL write that record to the Truth_Store and SHALL send it to no Public_Sink, so that the Truth_Store holds exactly one attribution record per outage signal sent to a Public_Sink.
2. [SAFETY] THE Replay_Engine SHALL send to a Public_Sink only Event_Envelopes whose type is not `DeviceTripped` and whose payload validates against its Event_Schema with no properties beyond those the Event_Schema defines, and no Event_Schema of a type sent to a Public_Sink SHALL define a field that names a tripped Device as a signal's cause, a signal's cause or a noise label.
3. [SAFETY] THE Replay_Engine SHALL assign sequence numbers to the Event_Envelopes sent to a Public_Sink as a contiguous run of integers starting at 1, with no gaps at the positions of `DeviceTripped` events, and the Run_Manifest SHALL contain no count of `DeviceTripped` events.
4. [SAFETY] IF the Truth_Store path, after resolution to an absolute path, equals a File_Sink output path or lies inside a directory that a File_Sink writes to, or the Truth_Store is configured as a Public_Sink, THEN THE CLI SHALL reject the configuration with a usage error and exit code 2, SHALL emit no event to any Sink and SHALL create no Truth_Store file.
5. WHEN a run starts and no Truth_Store path is given, THE Replay_Engine SHALL write the Truth_Store to `simulator/runs/<run_id>/truth.jsonl`.
6. [SAFETY] IF a write to the Truth_Store fails during a run, THEN THE Replay_Engine SHALL stop sending events to every Public_Sink before emitting the next event, and THE CLI SHALL exit with code 4 and an error message indicating that the Truth_Store could not be written.
7. THE Scorer SHALL read Hidden_Truth only from the Truth_Store and SHALL NOT derive Hidden_Truth from any Public_Sink output.
8. IF the Truth_Store for the scored run is missing, unreadable or contains a record that does not parse, THEN THE Scorer SHALL produce no Score_Report and THE CLI SHALL exit with code 3 and an error message indicating that Hidden_Truth is unavailable.

### Requirement 16: Scoring against hidden truth

**User Story:** As an evaluator, I want a bounded, reproducible score of inferred failed devices against the truth, so that agent-team changes can be compared run to run.

#### Acceptance Criteria

1. WHEN `score` runs with a valid Truth_Store and a valid Inferred_Device_Set, THE Scorer SHALL compute precision, recall and F1 over the truth set. The truth set is the set of distinct Device IDs named by `DeviceTripped` records in the Truth_Store whose `sim_time` is less than or equal to the greatest `sim_time` recorded in that Truth_Store. The Scorer SHALL count a Device that tripped more than once as one member, SHALL exclude outage signals attributed to noise, and SHALL compare Device IDs by exact, case-sensitive string equality.
2. FOR ALL Truth_Stores and Inferred_Device_Sets, THE Scorer SHALL return precision, recall and F1 each in the closed interval [0, 1], each rounded half-to-even to 4 decimal places.
3. WHEN both the truth set and the Inferred_Device_Set are empty, THE Scorer SHALL return precision, recall and F1 of 1.
4. WHEN exactly one of the truth set and the Inferred_Device_Set is empty, THE Scorer SHALL return precision, recall and F1 of 0.
5. FOR ALL Truth_Stores and Inferred_Device_Sets, THE Scorer SHALL return a byte-identical Score_Report for any ordering of the Inferred_Device_Set, for any duplicated entries in it, and on every repeated run with the same inputs.
6. IF the Inferred_Device_Set contains a Device ID absent from the Synthetic_Grid, THEN THE Scorer SHALL count the ID once as a false positive, list it under both the false positives and `unknown_device_ids` in the Score_Report, and continue scoring the remaining IDs.
7. IF the Inferred_Device_Set is missing, unreadable or not valid against its JSON Schema, THEN THE Scorer SHALL exit with code 3 and a message naming the file and, for schema failures, the first invalid field. It SHALL write no Score_Report and leave no partial Score_Report file.
8. IF the Truth_Store is missing, unreadable or contains a record that cannot be parsed, THEN THE Scorer SHALL exit with code 3 and a message naming the Truth_Store file. It SHALL write no Score_Report and leave no partial Score_Report file.
9. WHEN scoring completes, THE Scorer SHALL write the Score_Report as JSON with true positives, false positives, false negatives, precision, recall and F1, with every Device ID list sorted in ascending lexicographic order.
10. WHEN scoring completes, THE Scorer SHALL print exactly one line to stderr that states precision, recall, F1 and the counts of true positives, false positives and false negatives, and SHALL then exit with code 0.

### Requirement 17: CLI, exit codes and errors

**User Story:** As a demo operator, I want one predictable command line with clear errors, so that I can run the replay and scripts can react to failures.

#### Acceptance Criteria

1. THE CLI SHALL provide `python -m simulator run --scenario <id> [--seed <n>] [--speed <n|max>] [--sink stdout|file|eventbridge]... [--out <path>] [--truth-out <path>]`, plus `build-grid`, `validate` and `score` subcommands, where `--sink` may be given one to three times, a repeated identical `--sink` value selects that Sink once, an omitted `--sink` selects the Stdout_Sink only, an omitted `--seed` selects the Scenario's default Seed, an omitted `--speed` selects a Speed_Multiplier of 60, and an omitted `--truth-out` selects the default Truth_Store path.
2. WHEN `python -m simulator run --scenario michaung-style --speed 60` runs with no other options, THE CLI SHALL publish the complete `michaung-style` Event_Stream to the Stdout_Sink only, using the Scenario's default Seed, and SHALL exit with code 0.
3. THE CLI SHALL exit with 0 on success; 1 on an unexpected internal error; 2 on a usage error (unknown subcommand or option, missing required option, out-of-range Seed or Speed_Multiplier given on the command line, rejected Sink selection per criterion 14.9, or rejected Truth_Store configuration per criterion 15.4); 3 on a Scenario, data or schema validation error, including an unknown Scenario ID, an invalid Seed recorded in a Scenario file, and a missing, unreadable or unparsable Truth_Store or Inferred_Device_Set in `score`; 4 on a Sink or upstream error, including a Truth_Store or Run_Manifest write failure; and 130 when interrupted by the operator.
4. WHEN an error ends a command, THE CLI SHALL print to stderr, and not to stdout, a plain-language message of at most 5 lines that names the failing input (option, file path, Scenario ID, field or event ID) and states one corrective action, with no stack trace lines unless `--debug` is set (criterion 9).
5. WHEN the operator interrupts a run with Ctrl+C, THE CLI SHALL within 10 seconds flush events already produced to every Sink and entries already produced to the Truth_Store, finalise the Run_Manifest with status `interrupted` and event counts equal to the events delivered to the Public_Sinks, and exit with code 130.
6. IF, before the first event is emitted, a chosen Sink cannot be used (`--sink eventbridge` without AWS credentials or without the bus `minnal-events`, or a File_Sink or Truth_Store path that cannot be created or written), THEN THE CLI SHALL exit with code 4 before emitting any event and SHALL name the missing credentials, the missing bus or the unwritable path.
7. IF `--sink file` is chosen without `--out`, THEN THE CLI SHALL exit with code 2 before emitting any event and SHALL name the missing `--out` option.
8. [SAFETY] IF a usage, validation or Sink error ends a command before the first event is emitted, THEN THE CLI SHALL write no event to any Sink and no entry to the Truth_Store.
9. [DEFERRED] WHERE `--debug` is set, WHEN an error ends a command, THE CLI SHALL print the plain-language message followed by the full stack trace to stderr and SHALL exit with the same exit code as without `--debug`.
10. WHEN a run ends after Run_Start, THE CLI SHALL finalise the Run_Manifest with exactly one final status in one-to-one correspondence with how the run ended: `reset` if and only if the run ended because of a reset command (criterion 13.6), `completed` if and only if the CLI exits with code 0, `interrupted` if and only if the CLI exits with code 130, and `failed`, together with the exit code, if and only if the CLI exits with code 1, 3 or 4; no other combination of final status and run ending SHALL be written.

### Requirement 18: Observability and run records

**User Story:** As an SRE, I want structured logs and a manifest per run, so that failed replays and evaluation runs can be investigated afterwards.

#### Acceptance Criteria

1. THE Simulator SHALL write every log record to stderr as exactly one JSON object per line with the keys `level` (one of `debug`, `info`, `warning`, `error`), `message`, `service` equal to `minnal-simulator`, `incident_id` and `correlation_id`, where `incident_id` and `correlation_id` equal the values in the current run's Event_Envelopes, or are `null` for `build-grid`, `validate` and `score` and for log records written before the run's IDs are derived.
2. THE Simulator SHALL exclude from every log line and from the Run_Manifest any AWS access key, secret key or session token value available to the process, any value read from AWS Secrets Manager, every Crew member name and every phone number present in the Simulator's data files or Scenario.
3. WHEN a run ends with final status `completed`, `interrupted`, `reset` or `failed`, THE Replay_Engine SHALL write the Run_Manifest to `simulator/runs/<run_id>/manifest.json` before the CLI exits (or, for status `reset`, with the write completed before initialisation of the next run begins, so that the next run's initialisation waits for that write), replacing any earlier file at that path, with run ID, Seed, Scenario ID and content hash, Simulator version, the Speed_Multiplier at run start and each speed change with the Simulated_Time at which it applied, the final status, the CLI exit code (`null` for status `reset`), the count of events per event type delivered to Public_Sinks, and Attribution_Text.
4. [DEFERRED] WHEN a run ends, THE Replay_Engine SHALL write one log line, at level `info` for status `completed`, `interrupted` or `reset` and at level `error` for status `failed`, containing the run ID, the final status and, for each Sink used in the run, the count of events delivered and the count of events that failed delivery, where each delivered count equals the number of events that Sink accepted.
5. [SAFETY] THE Replay_Engine SHALL exclude from the Run_Manifest and from every log line any tripped Device ID, any noise label, any signal-to-Device attribution and any count of `DeviceTripped` events.
6. IF the Run_Manifest cannot be written, THEN THE Replay_Engine SHALL write an `error` log line naming the manifest path and the cause, and THE CLI SHALL exit with code 4, or keep the run's existing non-zero exit code if the run already ended with one.

### Requirement 19: Performance

**[DEFERRED] — post-challenge, non-gating benchmark (marked `slow`).** These limits are measured and reported but do not fail the challenge build. The gating demo-timing guarantee is criterion 13.14.

**User Story:** As a QA engineer, I want the replay and grid build to be fast unpaced, so that property tests and CI can run full scenarios.

#### Acceptance Criteria

1. [DEFERRED] WHEN `run` executes the `michaung-style` Scenario with Speed_Multiplier `max`, the File_Sink and any Seed from 0 to 4,294,967,295, THE Replay_Engine SHALL finish writing the complete Event_Stream, the Truth_Store and the Run_Manifest, and the CLI SHALL exit with code 0, within 30 seconds of wall-clock time from process start to process exit on the reference developer machine (assumption A9).
2. [DEFERRED] WHEN `build-grid` executes for the `michaung-style` Scenario with any Seed from 0 to 4,294,967,295, THE Grid_Builder SHALL finish writing every Synthetic_Grid file, and the CLI SHALL exit with code 0, within 60 seconds of wall-clock time from process start to process exit on the reference developer machine (assumption A9).
3. [DEFERRED] WHEN the Replay_Engine replays the `michaung-style` Scenario in-process with Speed_Multiplier `max` and the Fake_Sink, THE Replay_Engine SHALL deliver the complete Event_Stream to the Fake_Sink within 10 seconds of wall-clock time on the reference developer machine, so that one property-test example of a full Scenario fits a CI test run.
4. [DEFERRED] THE Simulator SHALL measure the time limits in criteria 1 to 3 as the median of 5 consecutive runs with the same Seed, excluding one preceding warm-up run, with no other Minnal process running on the reference developer machine.
5. [DEFERRED] IF a performance check for criterion 1, 2 or 3 exceeds its time limit, THEN THE Simulator performance test SHALL fail and SHALL report the measured median duration, the time limit and the Seed used.

### Requirement 20: Verification by property-based testing

**User Story:** As a QA engineer, I want every stated correctness property of the Simulator verified by a property-based test, so that the "for all" guarantees in this spec are checked against many generated inputs, not just hand-picked examples, and regressions are caught reproducibly.

#### Acceptance Criteria

1. THE Simulator test suite SHALL include, for every correctness property named in `design.md` (Property 1 to Property N), exactly one property-based test written with Hypothesis, so that each property has one owning test and each such test validates exactly one property.
2. THE Simulator SHALL name each property-based test `test_property_P<n>_<slug>` where `<n>` is the property number in `design.md` and `<slug>` describes the property, so that a test can be traced to its property and its property to the requirement criteria the property validates.
3. THE Simulator SHALL run each property-based test under one of two registered Hypothesis profiles (documented as an exception to `testing.md` in ADR-4) and SHALL include in each property-based test at least one explicitly provided known-bad example that would fail the property if the corresponding behaviour regressed: (a) a **pure-core** profile of at least 200 generated examples for every property that exercises only the pure core; and (b) a **replay** profile of at least 50 generated examples over small scenarios (at most 20 DTs and at most 2 simulated hours) plus exactly one explicit example that runs the full `michaung-style` Scenario, for every property that drives the Replay_Engine end to end. THE Simulator's CI Hypothesis profile SHALL set `derandomize=True`, and the `.hypothesis` example database SHALL NOT be committed to the repository.
4. THE Simulator SHALL make every property-based test deterministic and offline: each test SHALL derive its inputs only from Hypothesis's own generation (seeded by Hypothesis) and from committed fixtures, SHALL NOT read wall-clock time, host name, process ID, environment entropy or any network source, and SHALL block outbound sockets for the test session.
5. FOR ALL correctness properties tagged `[SAFETY]` in `design.md` (the flood-cause rule, flood-signal ordering, the emergency flag, hidden-truth isolation and two-person crews), THE Simulator SHALL provide a property-based test that asserts the safety property directly and SHALL treat that test as a required gate that, if failing, fails the whole test run.
6. THE Simulator SHALL exercise each property-based test that depends on a Seed across the full Seed range (0 to 4,294,967,295) by drawing the Seed from that range within the property-based test, so that determinism and safety properties are not verified for a single Seed only.
7. WHEN the property-based test suite runs, THE Simulator SHALL complete every property-based test that needs a full Scenario replay using the in-process Replay_Engine with Speed_Multiplier `max` and the Fake_Sink, running the small-scenario examples of the replay profile (criterion 3) plus one full `michaung-style` example, so that these examples fit a CI test run without relying on the deferred Requirement 19 performance limits.
8. IF a property-based test discovers a failing example, THEN Hypothesis SHALL shrink and report the minimal failing input, and THE Simulator test suite SHALL fail the run and record the failing example so that it can be re-run deterministically.
9. THE Simulator SHALL include a test that asserts every correctness property in `design.md` is covered by exactly one property-based test and that every property-based test maps to a property in `design.md`, so that a property added to the design without a test, or a test without a property, fails the suite.
10. THE Simulator SHALL keep the property-to-requirement traceability true: for every property-based test, the property it validates SHALL cite in `design.md` the requirement criteria it validates, so that each property-based test is transitively traceable to a requirement ID.

## Assumptions

- A1. OSM has little mapped distribution infrastructure in Chennai (much of it is underground cable). **Decided (ADR-1):** the committed OSM_Extract contains only Substation sites and the six Critical_Facility categories; Feeders, Laterals and DTs are synthetic and flagged `synthetic=true`. Service_Areas are the Voronoi tessellation of the DT points clipped to the study bounding box (via `shapely` `voronoi_polygons`), which makes them cover the area with disjoint interiors by construction, so Property 3 holds by construction.
- A2. Customer counts are derived, not observed: from Service_Area size and OSM building or land-use density where present, otherwise from a Scenario density parameter, scaled so Feeders fall in the typical hundreds-to-over-1,000 customer range cited in `ics-and-restoration.md`.
- A3. Flood_Polygons are scenario-authored, not measured. Open-Meteo's Flood API gives river discharge, not extent, and Sentinel-1A coverage of Chennai for 3 to 8 Dec 2023 is unverified. Polygons may later be informed by SAR scenes but stay labelled derived.
- A4. The Weather_Snapshot is either a one-time recording of Open-Meteo historical data (CC BY 4.0, attributed) committed to the repo, or authored values; the cyclone track is IBTrACS-derived (cited by DOI) or authored. No live weather API is called at runtime.
- A5. **Decided (ADR-1):** the OSM_Extract is a committed, trimmed one-time Overpass export of at most 2 MB, containing only Substation sites and the six facility categories inside the study area, with its extraction date recorded in `data/osm/`. It comfortably fits the repository.
- A6. The 10 m downed-wire distance, the Minnagam number and emergency advice are not emitted by the Simulator; downstream agents own them. The Simulator only sets `is_emergency`.
- A7. Crew skills, depot locations, the wind-damage threshold and the noise rate are Scenario parameters chosen by us, not utility data.
- A8. EventBridge limits (10 entries per `PutEvents` request, 256 KB per entry) come from the EventBridge API reference and must be re-checked in design.
- A9. "Reference developer machine" means the owner's laptop; the design records its specification when setting the performance baseline.
- A10. The `michaung-style` Scenario is inspired by Cyclone Michaung (Dec 2023) but is not a reconstruction; event timings are compressed so a demo run at speed 60 or higher stays within minutes.
- A11. A DT's customer count equals the customer count of its Service_Area, so criterion 2.5 ("every Device") is covered by criteria 1.5 and 1.6.
- A12. Payload-level identifiers (report IDs, meter IDs, `idempotency_key` values, callback tokens) are derived from the Seed and Scenario only, not from the reset count, so a reset run replays identical payloads (criterion 13.7) while envelope identity changes per reset (criterion 8.12). The outage-signal IDs listed in a `DeviceTripped` (criterion 9.6) are these payload-level IDs (the `OutageReported` report ID, or the `MeterLastGasp` meter ID, with each meter emitting at most one `MeterLastGasp` per run), not `event_id` values.
- A13. Because `event_id` is derived from `sequence` (criterion 8.5) and `sequence` follows the order, criterion 11.2 uses the payload's Canonical_Serialisation rather than `event_id` as its second-to-last tie-break key, and the Generation_Key defined in assumption A14 as its final key. Events equal in type, `sim_time` and payload are interchangeable, so their relative order, fixed by the Generation_Key, does not change the Event_Stream bytes.
- A14. The final ordering key in criterion 11.2 is the Generation_Key: the pair (a) the zero-based index of the source item that produced the event in Scenario source order (Scenario file order, with Weather_Snapshot records in snapshot file order; source items are Weather_Snapshot records, Flood_Polygon status changes, damage-script entries, citizen report entries and the noise-rate setting), and (b) the zero-based ordinal of the event among the events that item produced, in generation order. The Generation_Key is assigned during generation, is unique per event within a run, is never serialised into an Event_Envelope, and its derivation rule does not depend on the Seed.
