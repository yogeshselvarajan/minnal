# Flood electrical safety, preventive shutdown, downed-wire advice and CAP 1.2

Domain note for Minnal (Phase 00, foundation task 5). Research snapshot: 2026-09-28. Claims cite the Sources list; anything unverified is under **Assumptions**.

## 1. Electrical safety in flooded areas (India)

- The governing Indian safety instrument is the **CEA (Measures relating to Safety and Electric Supply) Regulations, 2023**, notified 8 June 2023 and administered by CEA's Chief Electrical Inspectorate Division. The division's regional office for the south is in Chennai [1].
- Flood-specific clauses (de-energising submerged apparatus, inspection before re-energising) could **not** be verified from the regulation text (see Assumptions A1).
- Utility practice (EEI): flooding can damage electrical systems, a licensed electrician may need to inspect a premises before it can get power back, and customers should never touch damaged equipment [2].

## 2. Preventive shutdown and how it was communicated (Michaung, Chennai, Dec 2023)

- Power supply was suspended "as precautionary measure" across Chennai from late Sunday night (3 Dec 2023) [3].
- The Tamil Nadu government said power was kept off in some areas "as a preventive measure" because cables were under water [4][5]. In other words, the state framed it as a safety measure, not a fault.
- Restoration was partial while flooding continued: north Chennai got power back partly from noon on 7 Dec while many places were still inundated [6]. Industrial estates said machines had to be checked before restart even after supply returned [7].
- Afterwards, TANGEDCO decided to move substations and transmission towers to higher ground, because floodwater had stopped restoration in several areas for days [8]. A waterlogged substation (Padi) kept an industrial belt without power [9].

## 3. Downed-wire public advice

| Advice | Sources |
|---|---|
| Treat fallen lines as live even if broken; stay well clear, more than 8–10 m | [10] |
| Stay at least 10 m away from the wire **and anything it touches, including water**; electricity can travel through wet ground | [11][12] |
| Report to the electricity company (and police/emergency services) | [10][12] |

**Chennai emergency and complaint number.** TANGEDCO's 24×7 "Minnagam" centre takes calls on **94987 94987** [13][14]. It replaced the old 1912 line [15] and is still the central complaint system under TNPDCL in 2026 [16]. The police or fire service should also be called for immediate danger (see Assumption A3 for the exact numbers).

## 4. CAP 1.2 (OASIS, 2010)

**Structure.** One `<alert>` contains zero or more `<info>` blocks; each `<info>` can contain `<area>` and `<resource>` blocks. An `msgType` of `Alert` SHOULD include at least one `<info>` [17].

**`<alert>` required elements** [17]:
- `identifier`: unique per sender; no spaces, commas, `<` or `&`
- `sender`: globally unique, e.g. based on a domain name
- `sent`: DateTime with a numeric offset; the letter **"Z" MUST NOT be used**, and UTC is written `-00:00`
- `status`: Actual | Exercise | System | Test | Draft
- `msgType`: Alert | Update | Cancel | Ack | Error
- `scope`: Public | Restricted | Private (Restricted needs `restriction`; Private needs `addresses`)
- `references` (optional) lists earlier messages as `sender,identifier,sent`, used by Update and Cancel

**`<info>`** [17]:
- `language` is optional (RFC 3066 code; defaults to `en-US`).
- Required: `category` (Geo, Met, Safety, Security, Rescue, Fire, Health, Env, Transport, **Infra** – utility, …), `event`, `urgency` (Immediate | Expected | Future | Past | Unknown), `severity` (Extreme | Severe | Moderate | Minor | Unknown), `certainty` (Observed | Likely | Possible | Unlikely | Unknown).
- Optional: `responseType` (Shelter, Evacuate, Prepare, Execute, Avoid, Monitor, Assess, AllClear, None; `Assess` SHOULD NOT be used for public warnings), `expires`, `headline`, `description`, `instruction`.

**`<area>`** [17]:
- `areaDesc` is required.
- `polygon` is whitespace-separated **`lat,lon`** pairs, at least 4 pairs, first pair equal to the last.
- `circle` is `lat,lon radius_km`.
- Several areas combine as a union.

**Multiple languages.** Multiple `<info>` blocks may be used "to provide the information in multiple languages". Each set with the same language is its own sequence [17]. The standard's AMBER example carries `en-US` and `es-US` `<info>` blocks inside **one** `<alert>` with a single identifier [17].

**India's use of CAP.**
- NDMA's **SACHET** portal is a CAP-based Integrated Alert System for geo-targeted alerts in multiple languages [18].
- A Government of India release says the CAP system runs in all 36 States/UTs and sends SMS alerts to mobile users in geo-targeted areas [19].
- C-DOT, which built it, cites ITU-T X.1303 (CAP) [20].
- IMD's site links to "Latest CAP Alerts" [21].
- India added cell broadcast alerting in 2026 [22].

## Assumptions (unverified)

- A1. CEA 2023 clauses on submerged or flood-affected apparatus, and on inspection or testing before re-energising, were not read (PDF). Cite no clause number until verified.
- A2. No TNPDCL/TANGEDCO-published downed-wire safe distance was found. The 10 m figure comes from Canadian utilities [11][12] and Energy Safe Victoria's 8–10 m [10]. Using 10 m for Chennai is a product choice.
- A3. Indian emergency numbers (112 all-emergency, 101 fire) were not verified in this session.
- A4. That SACHET exposes a public CAP feed Minnal could consume, and how a utility becomes an authorised SACHET originator, were not verified (the integration guide PDF was not readable).
- A5. BCP 47 / RFC 3066 tags for Minnal's languages (`en-IN`, `hi-IN`, `ta-IN`) are assumed valid; this was not checked against the RFC in this session.

## Implications for Minnal (observations, not requirements)

- O1. **Language variants belong in one `<alert>`** (several `<info>` blocks, one per language). This makes "same identifier, severity and expires across languages" (P6) true by construction [17]. Updates are new alerts that point back through `references`.
- O2. **Time format clash.** CAP forbids "Z" in `sent`/`expires` [17], but `api-contracts.md` says wire times use `Z`. The CAP serialiser needs its own formatter (`-00:00` or `+05:30`), and P6 validation should check this.
- O3. **Coordinate order clash.** CAP polygons are `lat,lon` and closed with at least 4 pairs [17]; Minnal's GeoJSON is `[lon, lat]`. The converter needs a property test (round-trip plus a known-bad swapped case).
- O4. Use `category=Infra` for outage alerts and `Safety` for downed-wire or preventive-shutdown messages. `expires` is optional in CAP [17] but required by Minnal steering, so the validator must enforce it on top of the schema.
- O5. Present preventive shutdown the way Tamil Nadu did in 2023, as a safety measure because equipment is under water [4]. Say restoration waits for water to recede and for inspection [6][7][8].
- O6. The downed-wire script should cover: treat as live; stay ≥10 m from the wire and any water or objects touching it; call 94987 94987; plus the emergency flag [10][11][13]. The number must come from config, not the model.
- O7. Flooded substations (e.g. Padi) block whole areas for days [8][9]. The ETR service should hold an "unknown until water recedes" state instead of inventing a time.
- O8. SACHET is where public alerting happens in India [18][19]. Minnal should produce CAP that could be handed to the authorised originator, not claim to broadcast public warnings itself.

## Sources

1. "About CEI Division", Central Electricity Authority, accessed 2026-09-28. https://cea.nic.in/about-cei/?lang=en
2. "Reliability & Emergency Response: The Steps to the Power Restoration Process", Edison Electric Institute, n.d., accessed 2026-09-28. https://eei.org/issues-and-policy/reliability-emergency-response
3. "Chennai, surrounding areas marooned as Michaung wreaks havoc", The Hindu, 5 Dec 2023. https://www.thehindu.com/news/cities/chennai/cyclone-michaung-chennai-surrounding-areas-marooned-as-michaung-wreaks-havoc/article67605306.ece
4. "Chennai Grapples With Stagnant Water, Power Disruption", NDTV, 6 Dec 2023. https://www.ndtv.com/chennai-news/cyclone-michaung-chennai-grapples-with-stagnant-water-power-disruption-4640207
5. "Cyclone Michaung aftermath: Parts of Chennai suffer from inundation, relief work expedited", Economic Times, 6 Dec 2023. https://m.economictimes.indiatimes.com/news/india/cyclone-michaung-parts-of-chennai-suffer-from-inundation-relief-work-expedited/articleshow/105779114.cms
6. "Nightmare persists in north Chennai…", The Hindu, 9 Dec 2023. https://www.thehindu.com/news/cities/chennai/cyclone-michaung-nightmare-persists-in-north-chennai-families-struggle-amidst-toxic-floodwaters-lack-of-medical-aid-and-relief/article67622140.ece
7. "Michaung effect: Rainwater recedes but MSMEs await normalcy to return", The Hindu, 8 Dec 2023. https://www.thehindu.com/news/national/tamil-nadu/michaung-effect-rainwater-recedes-but-msmes-await-normalcy-to-return/article67615703.ece
8. "Tangedco to Move Substations: Ensuring Power Restoration After Cyclone Michaung", Times of India, 11 Dec 2023. https://timesofindia.indiatimes.com/city/chennai/tangedco-to-move-substations-ensuring-power-restoration-after-cyclone-michaung/articleshow/105887840.cms
9. "Cyclone Hits Chennai's Industrial Units, IT Hubs", Times of India, 7 Dec 2023. https://timesofindia.indiatimes.com/city/chennai/cyclone-hits-chennais-industrial-units-it-hubs/articleshow/105796791.cms
10. "Electrical emergencies", Energy Safe Victoria, accessed 2026-09-28. https://www.energysafe.vic.gov.au/community-safety/emergencies/electrical-emergencies
11. "Outdoor and power line safety", Hydro Ottawa, n.d. (verified from search-result text). https://hydroottawa.com/en/residential/outages-safety/outdoor-and-power-line-safety
12. "Power line safety", FortisBC, n.d. (verified from search-result text). https://fortisbc.com/safety-outages/electricity-safety/power-line-safety
13. "Tangedco launches 24x7 customer care cell called Minnagam", Times of India, 20 Jun 2021. https://timesofindia.indiatimes.com/city/chennai/tangedco-launches-24x7-customer-care-cell-called-minnagam/articleshow/83685522.cms/
14. "Tangedco taking steps to integrate Minnagam with mobile app…", The Hindu, 18 Mar 2024. https://www.thehindu.com/news/cities/chennai/tangedco-taking-steps-to-integrate-minnagam-with-mobile-app-for-seamless-registering-of-complaints-by-consumers/article67964458.ece
15. "Do you have these numbers in your phone book?", The Hindu, 22 Feb 2022. https://www.thehindu.com/news/cities/chennai/do-you-have-these-numbers-in-your-phone-book/article65070106.ece
16. "Power supply complaints in Chennai: TNPDCL to revive localised helpline 'Fuse on Call'", Times of India, May 2026. https://timesofindia.indiatimes.com/city/chennai/power-supply-complaints-in-chennai-tnpdcl-to-revive-localised-helpline-fuse-on-call/articleshow/131415007.cms
17. "Common Alerting Protocol Version 1.2", OASIS Standard, 1 Jul 2010. https://docs.oasis-open.org/emergency/cap/v1.2/CAP-v1.2-os.html
18. "SACHET – National Disaster Alert Portal", NDMA, accessed 2026-09-28. https://sachet.ndma.gov.in/
19. Press release on the CAP-based alert system, Department of Telecommunications / PIB, May 2026 (search-result text). https://www.dot.gov.in/static/uploads/2026/05/a48a72347af8421fa64b064ef23314a1.pdf
20. "CAP" product pamphlet, C-DOT, n.d. (search-result text). https://cdot.in/cdotweb/assets/docs/products/dms/cap.pdf
21. "Cyclone Information" (links to Latest CAP Alerts), India Meteorological Department, accessed 2026-09-28. https://mausam.imd.gov.in/responsive/cycloneinformation.php
22. "What is India's new Cell Broadcast System?", The Hindu, May 2026. https://www.thehindu.com/news/national/what-is-india-new-cell-broadcast-system-emergency-alert-ndma-everything-you-need-to-know/article70931015.ece
