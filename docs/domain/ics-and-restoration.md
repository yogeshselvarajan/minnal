# ICS / IRS roles and utility storm-restoration practice

Domain note for Minnal (Phase 00, foundation task 5). Research snapshot: 2026-09-28. Context: a Chennai distribution utility (TANGEDCO, now TNPDCL for distribution [15]) during a Michaung-style cyclone. Every claim cites a source in the list at the end; anything unverified is under **Assumptions**.

## 1. Incident Command System (ICS) and India's Incident Response System (IRS)

**ICS (US NIMS).** ICS integrates facilities, equipment, personnel, procedures and communications in one common organisational structure, normally across six functional areas: Command, Operations, Planning, Logistics, Intelligence and Investigations, and Finance/Administration [1]. The Incident Commander (IC) assigns Command Staff as needed; it typically includes a Public Information Officer (PIO), a Safety Officer and a Liaison Officer, all reporting directly to the IC [2].

- **PIO:** interfaces with the public, media and other agencies; gathers, verifies and disseminates accessible, timely information; all PIOs speak "with one voice"; the IC approves the release of incident information [2].
- **Safety Officer:** monitors operations, maintains the incident Safety Plan and "stops and/or prevents unsafe acts"; ultimate responsibility for safety still rests with the IC and supervisors [2].
- **Liaison Officer:** point of contact for other government agencies, NGOs and private organisations that are not part of command [2].
- **Planning Section units:** Resources and Situation Units are busiest early in an incident; Documentation and Demobilization Units later [3]. A Situation Unit can be activated without a Planning Section Chief, in which case the IC supervises it [3].
- ICS is modular: elements are activated only when incident objectives need them, and non-standard position titles should not be invented [3].

**IRS (India, NDMA).** NDMA issued the Incident Response System guidelines in 2010 under Section 6 of the Disaster Management Act, 2005 [4][5]. IRS is a flexible system of Sections, Branches and Units with pre-assigned roles, activated only when required [5]. Responsible Officers (ROs) are designated at State and District level; an RO may delegate to an Incident Commander, who manages the incident through Incident Response Teams (IRTs) [4]. District IRS orders designate a Safety Officer among IRS positions [6], and NIDM publishes IRS training modules for the Operations Section Chief and Logistics Section Chief [7][8].

### Mapping to Minnal agents

| Minnal agent | ICS position (FEMA) | IRS equivalent | Verified basis | Note |
|---|---|---|---|---|
| `commander` | Incident Commander | Incident Commander (under the RO) | [2][4] | IC approves release of public information [2]; this fits the human-approval gate |
| `hazard` | Planning Section, Situation Unit | Planning Section (see Assumptions) | [3] | The Situation Unit can run without a Planning Chief [3] |
| `diagnostics` | Operations Section (a utility "grid branch") | Operations Section | [1][7] | "Branch" is a standard ICS element [3] |
| `safety` | Safety Officer (Command Staff) | Safety Officer | [2][6] | Stopping unsafe acts is part of the role [2], so a veto is doctrinally correct |
| `dispatch` | Logistics Section (resources); tasking is Operations | Logistics Section | [1][8] | See observation O2 |
| `pio` | Public Information Officer | See Assumptions (IRS title) | [2] | "One voice" matches the one-alert, many-languages design |
| `citizen_line` | Public inquiries under the **PIO** | none | [2] | ICS Liaison deals with agencies, not the public [2]; see O3 |
| `scribe` | Planning Section, Documentation Unit | Planning Section | [3] | |

## 2. Standard utility restoration order

EEI (the US investor-owned utility association) describes utility storm restoration this way: one of the first steps is making sure power no longer flows through downed lines; restoration then follows set priorities: (1) power plants, (2) high-voltage transmission lines, (3) substations, (4) essential services critical to public health and safety (hospitals, nursing homes, fire and police, water systems), (5) lines that return the most customers in the least time, (6) service lines to individual homes and small groups [9]. The US DOE version of the same graphic also lists communications systems among the essential services [10]. A utility's customer guide gives the scale at each level: feeders from substations serve several hundred to more than 1,000 customers, tap lines (laterals) serve 20 to a few hundred, and individual connections are the slowest work [11].

Minnal's steering order (make-safe → critical facilities → substations/main feeders → laterals/DTs → services) matches this sequence below the transmission level. The one difference: EEI puts substations **before** essential services, because a hospital cannot be re-energised until its supply path is live [9]. See O1.

## 3. Make-safe

EEI puts "make sure power is no longer flowing through downed lines" at the start of restoration [9]. EEI also warns that flood-damaged customer premises may need an electrician's inspection before they can receive power, and that the public should never touch damaged equipment [9]. Public-facing downed-wire and flood advice is in `flood-safety-and-cap.md`.

## 4. Crew safety

- **Two-person work.** In the US, OSHA 29 CFR 1910.269(l)(1) requires at least two employees for certain work on or near parts energised above 600 V, such as installing, removing or repairing lines where a worker is exposed to such parts [12]. An equivalent Indian (CEA) clause was not verified (see Assumptions).
- **No driving through flood water.** The US National Weather Service says over half of flood drownings involve vehicles driven into flood water; 6 inches of fast water can knock an adult over, 12 inches can carry away most cars, 2 feet can carry away SUVs and trucks, and flooded roads may have collapsed underneath [13].
- **Rest limits:** not verified (see Assumptions).

## 5. ETR practice

Maryland's regulation on Estimated Times of Restoration (COMAR 20.50.12.18, effective 21 Aug 2023) is a concrete regulatory model [14]:
- Post an "assessment" message that includes a safety message within 4 hours of the end of the event [14].
- Issue a **Global ETR** within 24 hours of the end of the event; the deadline can be extended if damage assessment needs longer [14].
- Review Global and Zonal ETRs at least every 24 hours and update them when better information arrives [14].
- A **Zonal ETR** states when at least 90% of the affected customers in the zone will be restored. An ETR counts as accurate if at least 90% are restored by the last ETR given [14].
- After the event, self-assess ETR quality: accuracy of the first and final ETR, the number of changes, and the reasons for them [14].

A US utility (PSEG Long Island) defines its global ETR the same way, as the time to restore 90% of affected customers, and notes it moves later as damage grows (seen in search-result text; the page itself returned HTTP 403) [16].

Chennai context: during Michaung the state government reported 80% of supply restored while other areas were still without power [17]; TANGEDCO officials publicly promised "100% by Thursday evening" while people were still flooded [18]. This shows the public cost of an optimistic global figure.

## Assumptions (unverified, do not state as fact)

- A1. **IRS position titles.** That IRS Command Staff includes an "Information & Media Officer" and a "Liaison Officer", and that IRS has a Planning Section with Situation/Documentation units. The NDMA 2010 guideline PDF could not be read by the tools; only RO, IC, IRT, Safety Officer, Operations Section Chief and Logistics Section Chief were verified [4][6][7][8].
- A2. **CEA two-person / supervision rule.** The CEA (Measures relating to Safety and Electric Supply) Regulations, 2023 (notified 8 June 2023 [19]) probably contain work-permit and supervision clauses, but the text was not read. Do not cite a CEA clause number until it is checked.
- A3. **Crew rest limits.** No authoritative source for storm-shift duty and rest hours was found. Treat any number (for example "16 on / 8 off") as a TNPDCL-configurable policy parameter, not a regulation.
- A4. **TNPDCL restoration SOP.** No published TNPDCL/TANGEDCO cyclone restoration order was found; the order above comes from US (EEI/DOE) practice.
- A5. **Indian ETR rules.** No Indian rule equivalent to COMAR 20.50.12.18 was verified; the Electricity (Rights of Consumers) Rules were not checked.

## Implications for Minnal (observations, not requirements)

- O1. A critical-facility job should carry its **upstream supply path** (substation → feeder → lateral). Ranking "critical facilities" above "substations" only makes sense when the needed substation work is pulled up with the facility [9].
- O2. In ICS, **assigning** crews to tasks belongs to Operations and **supplying and tracking** resources to Logistics and the Planning Resources Unit [1][3]. `dispatch` straddles both. Worth one line in the design so a utility reviewer is not confused.
- O3. Put `citizen_line` under the **PIO** (public inquiries), not the Liaison Officer. ICS Liaison is for agencies and NGOs [2]. Consider reserving Liaison for Greater Chennai Corporation / TNSDMA coordination.
- O4. The Safety veto matches ICS doctrine: the Safety Officer "stops and/or prevents unsafe acts" [2]. Final responsibility still sits with the IC, which matches human approval.
- O5. ETR model worth adopting: an assessment and safety message early, a Global ETR once assessment allows, Zonal ETRs defined at the 90% level, review at least every 24 h, and a post-event accuracy self-assessment for `scribe` [14].
- O6. COMAR allows ETRs to change in either direction when better information arrives [14]. Minnal's stricter property P5 (never silently earlier) is a product choice, not a regulation. Keep it, but describe it as such.
- O7. Routing must treat any flooded road segment as impassable, regardless of estimated depth [13].
- O8. Two-person crews are a US legal minimum for energised HV work [12]. For India, keep it as a configurable policy until A2 is checked.

## Sources

1. "NIMS Components" (Incident Command System section), FEMA, n.d., accessed 2026-09-28. https://www.fema.gov/emergency-managers/nims/components
2. "IS-200.c: Command Staff Functions", FEMA Emergency Management Institute, n.d. (text sourced to NIMS), accessed 2026-09-28. https://emilms.fema.gov/_is_0200c/groups/394.html
3. "IS-200.c: Organizational Flexibility / Modular Organization", FEMA EMI, n.d., accessed 2026-09-28. https://emilms.fema.gov/_is_0200c/groups/246.html
4. District Collector, Thiruvananthapuram, order on IRS at District and Taluk level (cites NDMA IRS Guidelines, ISBN 978-93-80440-03-3, dated 10-07-2010), Kerala SDMA, 2026. https://sdma.kerala.gov.in/wp-content/uploads/2026/06/document-2026-05-27T103740.656.pdf
5. District Collector, Kasaragod, "Incident Response System – designated officers", Kerala SDMA, 03-06-2022. http://sdma.kerala.gov.in/wp-content/uploads/2022/07/KSD.pdf
6. District IRS designation order listing "Safety Officer (SO)", Kerala SDMA (Malappuram), 2022. http://sdma.kerala.gov.in/wp-content/uploads/2022/07/Malappuram-1.pdf
7. "Training Module: Incident Response System Operation Section Chief", National Institute of Disaster Management (MHA), 2015. https://www.nidm.gov.in/PDF/modules/irs-4.pdf
8. "Training Module: Incident Response System Logistics Section Chief", NIDM (MHA), n.d. https://nidm.gov.in/PDF/modules/irs-5.pdf
9. "Reliability & Emergency Response: The Steps to the Power Restoration Process", Edison Electric Institute, n.d., accessed 2026-09-28. https://eei.org/issues-and-policy/reliability-emergency-response
10. "Restoration Process Steps" (graphic), US Department of Energy, Sep 2018. https://www.energy.gov/sites/default/files/2018/09/f55/Restoration%20Process%20Steps_graphic.pdf
11. "Power Outages – Restoring Your Power" (hierarchy of repair), Eugene Water & Electric Board, n.d. https://www.eweb.org/documents/outages-safety/restoring-your-power-illustration.pdf
12. OSHA standard interpretation on 1910.269(l)(1) two-employee requirement, US OSHA, 27 Aug 2001 (verified from search-result text; page returned HTTP 403). https://www.osha.gov/laws-regs/standardinterpretations/2001-08-27-1
13. "Turn Around Don't Drown®", US National Weather Service, n.d., accessed 2026-09-28. https://www.weather.gov/safety/flood-turn-around-dont-drown
14. "Md. Code Regs. 20.50.12.18 – Estimated Times of Restoration and Associated Messaging", State of Maryland via Cornell LII, effective 21 Aug 2023. https://www.law.cornell.edu/regulations/maryland/COMAR-20-50-12-18
15. "Power supply complaints in Chennai: TNPDCL to revive localised helpline 'Fuse on Call'", Times of India, May 2026. https://timesofindia.indiatimes.com/city/chennai/power-supply-complaints-in-chennai-tnpdcl-to-revive-localised-helpline-fuse-on-call/articleshow/131415007.cms
16. "Estimated Restoration Times", PSEG Long Island, n.d. (search-result text; page returned HTTP 403). https://www.psegliny.com/outages/estimatedrestorationtimes
17. "80% Power Supply Restored In Rain-Hit Chennai: Tamil Nadu Government", NDTV, 5 Dec 2023. https://www.ndtv.com/chennai-news/80-power-suppy-restored-in-rain-hit-chennai-tamil-nadu-government-4636172
18. "Public anger simmers in Chennai areas still flooded", Times of India, 8 Dec 2023. https://timesofindia.indiatimes.com/city/chennai/public-anger-simmers-in-chennai-areas-still-flooded/articleshow/105824071.cms
19. "About CEI Division", Central Electricity Authority, accessed 2026-09-28. https://cea.nic.in/about-cei/?lang=en
