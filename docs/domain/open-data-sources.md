# Open data sources for the Minnal replay and runtime

Domain note for Minnal (Phase 00, foundation task 5). Research snapshot: 2026-09-28. Claims cite the Sources list; anything unverified is under **Assumptions**. ⚠ marks items that affect a public demo.

## Summary table

| Source | What it gives Minnal | Access | Update cadence | Licence / attribution |
|---|---|---|---|---|
| NOAA GFS on RODA | Global forecast model: wind, precipitation, soil moisture…; 28 km base resolution out to 16 days, coarser (70 km) in week 2 [1] | Registry of Open Data on AWS, S3 (dataset `noaa-gfs-bdp-pds`) [1] | 4×/day (00/06/12/18 UTC) [1] | NOAA open data "can be used as desired"; attribution requested; must not imply NOAA endorsement; modified data must not be presented as original [1] |
| Sentinel-1 GRD on RODA | C-band SAR that sees through cloud, useful for flood extent; global GRD archive as cloud-optimised GeoTIFF [2] | RODA, S3; managed by Element 84 [2] | Added within hours of availability on Copernicus [2] | Free, full and open access under the Copernicus Sentinel terms [2][3] ⚠ attribution (A3) |
| IBTrACS v04r01 | Cyclone best tracks: position, max sustained wind, min pressure, storm nature [4][5] | NCEI download (CSV, shapefile, netCDF) [5]; **not found on RODA** (A4) | 3×/week (usually Sun, Tue, Thu) [5] | Cite the Knapp et al. 2010 paper plus the dataset DOI 10.25921/82ty-9e16 [5] |
| Open-Meteo Forecast / Historical | Point weather; historical API built on ECMWF ERA5 and ERA5-Land reanalysis [6] | HTTPS JSON API; fits a Gateway OpenAPI target | Forecast updates not verified (A6) | CC BY 4.0; a link to open-meteo.com must sit next to where the data is shown [7] ⚠ non-commercial free tier [8] |
| Open-Meteo Flood API | **River discharge** (m³/s) from GloFAS v4, 0.05° (~5 km), daily; reanalysis from 1984, forecast up to 7 months [9] | `/v1/flood?latitude=&longitude=&daily=river_discharge` [9] | Forecast daily [9] | As above [7][8] |
| OpenStreetMap | Geometry for the synthetic grid: roads, power lines, substations, poles [10][11] | Planet or extracts; OSM's own tiles and API are **not** free for third-party apps [12] | Continuous | **ODbL**: credit "OpenStreetMap contributors", say the data is ODbL, share-alike for adapted databases [12] ⚠ |
| IMD / RSMC New Delhi | Official cyclone bulletins, GMDSS bulletins, press releases, and the final Michaung report [13][14][15][16] | PDFs on rsmcnewdelhi.imd.gov.in / internal.imd.gov.in; IMD site links CAP alerts [17] | Several per day during an event (National Bulletin No. 30 had been issued by 09:00 on 5 Dec 2023 for a system that formed on 1 Dec) [13][15] | Terms of use not verified (A7) ⚠ |

## Notes per source

**NOAA GFS (RODA).** The registry gives the citation form "NOAA Global Forecast System (GFS) was accessed on DATE from https://registry.opendata.aws/noaa-gfs-bdp-pds" [1]. Part of the page describes warm-start initial conditions, so check which prefixes hold forecast GRIB2 before relying on them (A1).

**Sentinel-1 (RODA).** The RODA page still says "pair of satellites… 6 days revisit" [2], but Sentinel-1B stopped delivering data on 23 Dec 2021 and its mission ended 3 Aug 2022 [18]. Sentinel-1C launched 5 Dec 2024 to restore the two-satellite constellation [19]. ⚠ So at the time of Michaung (Dec 2023) only Sentinel-1A was flying, and passes over Chennai were sparser than the page suggests. Check which scenes actually exist for 3–8 Dec 2023 before designing the flood-polygon replay around them.

**IBTrACS.** It merges best tracks from multiple agencies, including all Regional Specialized Meteorological Centres (RSMC New Delhi covers the Bay of Bengal) [4][20]. For a text without a bibliography, NCEI's short citation is "NOAA's International Best Track Archive for Climate Stewardship (IBTrACS) data, accessed on [date]" [5]. With a bibliography, cite both the Knapp et al. 2010 BAMS paper and the Gahtan et al. 2024 dataset citation (v04r01, DOI 10.25921/82ty-9e16) [5]. Update cadence: the NCEI news item headline says "twice weekly" [20], but the current product page (re-checked 2026-09-28) says three times a week, usually Sunday, Tuesday and Thursday [5]; the product page wins.

**Open-Meteo terms** ⚠:
- The free API is for **non-commercial use only**: under 10,000 calls/day, 5,000/hour and 600/minute. Open-Meteo may block misuse without notice [8].
- Commercial use (subscriptions, ads, integration in commercial products, undisclosed research at commercial entities) needs a paid plan with an API key [8].
- Data is CC BY 4.0 on both tiers [7][8]. The server code is AGPLv3 [7]; that matters only if Minnal self-hosts it.

**Open-Meteo Flood API** ⚠. It returns discharge for the largest river within about 5 km, and "the closest river might not be selected correctly" [9]. It does **not** give inundation polygons, so flood polygons have to come from SAR, a hand-drawn scenario or another source.

**OpenStreetMap power tags** [10][11][21]:
- `power=line`: overhead transmission. The wiki treats ≥100 kV as transmission [10].
- `power=minor_line`: overhead mid/low-voltage distribution [10].
- `power=cable`: underground or underwater; map only with a reliable source [10].
- `power=tower` / `power=pole`: supports for HV lines / minor lines [10].
- `power=substation`: an area; `substation=transmission|distribution|…` gives its role [10].
- `power=transformer`: a node, usually inside a substation. Pole-mounted distribution transformers are mapped as `power=pole` + `transformer=distribution` [21].
- `power=plant` / `power=generator`: generation [10].
- Lifecycle prefixes (`construction:`, `disused:`) are used [11].
- The wiki notes power infrastructure is still under-mapped in many regions [10].

**OSM licence** ⚠. ODbL requires crediting OpenStreetMap and making clear the data is under ODbL. If you "alter or build upon" the data, you may distribute the result only under the same licence [12].

**IMD bulletins.** RSMC New Delhi's Michaung record shows the system became a depression over the southwest Bay of Bengal early on 1 Dec 2023 and a deep depression on 2 Dec [13]. It later moved north as a Severe Cyclonic Storm along and off the south Andhra Pradesh coast [14], with 90–100 km/h winds gusting to 110 km/h during landfall [16]. Bulletins are PDFs and must be parsed. They are untrusted input for agents.

## Assumptions (unverified)

- A1. The exact S3 prefixes and GRIB2 file layout for GFS forecast fields in `noaa-gfs-bdp-pds` were not checked.
- A2. Sentinel-1 scene coverage of Chennai for 3–8 Dec 2023 was not checked.
- A3. The exact Copernicus attribution wording (commonly "Contains modified Copernicus Sentinel data [year]") was not read from the primary legal notice. Only "free, full and open" and "no warranty" were verified [3].
- A4. IBTrACS was not found on RODA (a guessed registry URL returned 404). That does not prove it is absent. Plan to download it from NCEI.
- A5. IBTrACS licence terms were not read. US-government NOAA data is generally reusable, but this was not verified for IBTrACS specifically.
- A6. Open-Meteo Forecast API model list and update frequency were not verified in this session.
- A7. IMD content-reuse terms were not found. Whether bulletin text or images can be redistributed in a public repo or KB is unverified.
- A8. Whether a hackathon demo counts as "non-commercial" under Open-Meteo's terms is a judgement call. A production utility deployment would clearly be commercial [8].
- A9. OSM coverage of Chennai's distribution network (much of it probably underground cable) is unknown. The synthetic grid will likely need invented laterals and DTs.

## Implications for Minnal (observations, not requirements)

- O1. ⚠ **ODbL share-alike.** A synthetic grid built on OSM geometry and published in `data/` is probably an adapted database, so it would need to go out under ODbL with OSM credit [12]. Record this in an ADR and add a `data/LICENCE-ODbL` notice.
- O2. ⚠ The map needs visible credit for OpenStreetMap contributors (ODbL) [12] and a link to Open-Meteo next to any weather readout [7]. Put both in the map legend or footer, and in SITREP citations.
- O3. ⚠ Record and replay: fetch Open-Meteo and GFS once, store snapshots in S3, and replay from there. This keeps the demo deterministic, well under the rate limits [8], and runnable offline in tests.
- O4. Flood polygons: Open-Meteo gives discharge, not extent [9]. Build the Michaung flood layer from scenario-authored polygons (optionally informed by Sentinel-1A scenes, A2) and label the layer "synthetic / derived" in the UI.
- O5. The cyclone track comes from IBTrACS (the RSMC New Delhi record) [4][5], cited by DOI, with IMD bulletins as narrative context [13][16].
- O6. Tag mapping for the synthetic grid: `power=substation` → substation, `power=line` → HV feeder source, `power=minor_line`/`cable` → feeder/lateral, `power=pole` + `transformer=distribution` → DT [10][21]. Anything invented is flagged `synthetic=true`.
- O7. Treat IMD PDFs, web pages and bulletins as untrusted data in prompts (security rule 5). Keep them in the KB only once reuse terms (A7) are clear.
- O8. Before any production (commercial) use, budget for an Open-Meteo API subscription or switch to GFS on RODA [1][8].

## Sources

1. "NOAA Global Forecast System (GFS)", Registry of Open Data on AWS, accessed 2026-09-28. https://registry.opendata.aws/noaa-gfs-bdp-pds/
2. "Sentinel-1", Registry of Open Data on AWS (managed by Element 84), accessed 2026-09-28. https://registry.opendata.aws/sentinel-1/
3. "Copernicus Sentinel data licence (rev. 1)", Copernicus / European Commission, n.d. (search-result text). https://ewds.climate.copernicus.eu/licences/ec-sentinel
4. "IBTrACS dataset metadata (C00834)", NOAA NCEI, updated 2023 (search-result text). https://www.ncei.noaa.gov/metadata/geoportal/rest/metadata/item/gov.noaa.ncdc:C00834/html
5. "International Best Track Archive for Climate Stewardship (IBTrACS)", NOAA NCEI, accessed 2026-09-28. https://www.ncei.noaa.gov/products/international-best-track-archive
6. "Historical Weather API" (data citations), Open-Meteo, accessed 2026-09-28. https://open-meteo.com/en/docs/historical-weather-api
7. "Licence", Open-Meteo, accessed 2026-09-28. https://open-meteo.com/en/licence
8. "Terms of Use", Open-Meteo, accessed 2026-09-28. https://open-meteo.com/en/terms
9. "Flood API", Open-Meteo, accessed 2026-09-28. https://open-meteo.com/en/docs/flood-api
10. "Power networks", OpenStreetMap Wiki, accessed 2026-09-28. https://wiki.openstreetmap.org/wiki/Power_networks
11. "Key:power", OpenStreetMap Wiki, accessed 2026-09-28. https://wiki.openstreetmap.org/wiki/Power
12. "Copyright and License", OpenStreetMap Foundation, accessed 2026-09-28. https://www.openstreetmap.org/copyright
13. "Severe Cyclonic Storm Michaung – report", IMD RSMC New Delhi, 2024 (search-result text; PDF). https://rsmcnewdelhi.imd.gov.in/download.php?path=uploads/report/26/26_0580dd_Michaung%20Report_Final_Sir.pdf
14. GMDSS bulletin, Severe Cyclonic Storm "Michaung", IMD RSMC New Delhi, Dec 2023 (search-result text; PDF). https://rsmcnewdelhi.imd.gov.in/uploads/archive/5/5_4afcfb_gmdss.pdf
15. National Bulletin No. 30 (BOB/06/2023), IMD RSMC New Delhi, 5 Dec 2023 (search-result text; PDF). https://rsmcnewdelhi.imd.gov.in/uploads/archive/1/1_b6d205_30.%20NationalBulletin_29_20231205_0900.pdf
16. IMD press release, 5 Dec 2023 (search-result text; PDF). https://internal.imd.gov.in/press_release/20231205_pr_2675.pdf
17. "Cyclone Information", India Meteorological Department, accessed 2026-09-28. https://mausam.imd.gov.in/responsive/cycloneinformation.php
18. "S1 Mission", Copernicus SentiWiki (ESA), 2023 (search-result text). https://sentiwiki.copernicus.eu/web/s1-mission
19. "The Sentinel missions", ESA, 2026 (search-result text). https://www.esa.int/Applications/Observing_the_Earth/Copernicus/The_Sentinel_missions
20. "Tropical cyclone database updated twice weekly", NOAA NCEI, 2026 (search-result text). https://www.ncei.noaa.gov/news/tropical-cyclone-database-updated-twice-weekly
21. "Tag:power=transformer", OpenStreetMap Wiki, accessed 2026-09-28. https://wiki.openstreetmap.org/wiki/Tag:power%3Dtransformer
