# FETCH REPORT (Worker D)

Run: 2026-10-04, approx 02:17 to 02:28 UTC. Mechanism: WebFetch only (plus WebSearch to discover URLs). No curl, wget, cache or mirror.

IMPORTANT LIMITATION: WebFetch returns a small-model extraction, not raw page text. Every "quoted" passage in these files is quoted as the extractor returned it and has NOT been byte-verified against the raw source. Each file says so. Files D056, D086 and D087 are marked entirely SUMMARY because their "quotes" looked like paraphrase. RETRIEVED stamps are the file write times; fetches happened in the window above.

| doc_id | url | outcome | what we got | still missing |
|---|---|---|---|---|
| D059 | wbur.org/.../massachusetts-high-court-rent-control-ballot-question-struck | retrieved | Headline, June 23 2026 date, holding (religion exclusion, Art. 48), verbatim-per-extractor sentence that the question "cannot go forward this November" | IP number, justice names (not in article, as the lead found) |
| SUPP_D059_* (5 files, no doc_id) | gtlaw.com alert; caanet.org slip opinion copy; mass.gov petition text; mass.gov AG summary; mass.gov ballot initiatives list | retrieved | IP 25-21 confirmed from official mass.gov pages (AG summary heading "SUMMARY OF NO. 25-21"; official 2026 initiatives listing, petition number 25-21, Certified: Yes) and from SJC slip opinion copy and gtlaw. Decision June 23 2026 (argued May 6 2026), SJC-13893, Cella v. Attorney General, Gaziano J. | Official mass.gov copy of the slip opinion returned 404; the opinion text we have is from caanet.org (advocacy host) |
| D035 | hudsoncountyview.com/...approves-realpage-ban... | retrieved (partial) | Article dated 2025-05-22, 9-0 approval, $2,000 daily fine, no ordinance language | Ordinance number, adoption date (exact), effective date, prohibition text |
| SUPP_JC_insidernj, SUPP_JC_riverviewobserver | insidernj.com press release; riverviewobserver.net | retrieved (partial) | Ordinance numbers 25-056 and 25-057 (algorithm ban is 25-057, single source), first reading 2025-05-08, final vote scheduled 2025-05-21 | Second reading date confirmed, effective date, ordinance text |
| D034 | ecode360.com/46833413 | retrieved | Hoboken Ch. 158 (Rent Increases), algorithmic rent fixing, Ord. B-781, adopted 7-9-2025, prohibition, definitions, enforcement, penalty (fine up to $2,000 / community service up to 90 days / N.J.S.A. 40:49-5) | Effective date (not on page) |
| D032 | ecode360.com/15252438 | retrieved | Hoboken Ch. 155 Rent Control landing page, TOC only, Ord. C329 (1-16-1984) | Operative text (not on this page); not algorithmic |
| D033 | ecode360.com/15252470 | retrieved | Hoboken Ch. 155 Art. II sections 155-3 to 155-17, amendment list, one operative sentence | Not algorithmic; most section text |
| D070 | ecode360.com/36623772 | retrieved | Newark Ch. 19:2 rent control, CPI-based cap at 4%, history through Ord. 6PSF-I 06-17-2026 | Rest of chapter |
| D071 | ecode360.com/36637822 | retrieved (no operative text) | Newark Ch. 2:10 TOC and history (latest Ord. 6 PSF-D 09-09-2026); section 2:10-11 text truncated | Section 2:10-11 text |
| D072 | ecode360.com/36642000 | retrieved | Newark Ch. 2:31 Ban the Box (housing criminal history rules), Ord. 6 PSF-B 4-15-2015 | Complete text (extractor truncated) |
| D037 | morganlewis.com/pubs/2026/08/... | retrieved | Hoboken ch. 158-2 effective "July 2025"; Jersey City Code 218-12 effective "June 2025" (month only) | Adoption dates, ordinance numbers; article does not mention FAIR Act or Massachusetts |
| D060 | daypitney.com/...fair-act... | retrieved | FAIR Act P.L. 2026 c. 43, signed 2026-07-20, effective 2027-07-01, AG complaint portal | Article contains no preemption discussion (the statute does, see below) |
| D056 | mass.gov/doc/803-cmr-5-...-cori-housing/download | retrieved (summary only) | Paraphrase of 803 CMR 5.00 housing CORI rules; document dated 6/11/21 | Verified quotable text, effective date |
| D086 | ocbj.com/...santa-ana... | retrieved (summary only) | Santa Ana ban, first vote 2026-02-17, $1,000 fine | Ordinance number, final vote date, effective date |
| D087 | publicceo.com/2026/02/... | retrieved (summary only) | Santa Ana ban, final vote scheduled 2026-03-03, remedies, exclusions | Ordinance number, effective date |

## Not-found / failed attempts (all disclosed)
- mass.gov guessed URLs /info-details/2025-2026-initiative-petitions and /info-details/2026-ballot-initiatives: 404. These were my guesses, not enumeration; I then used WebSearch to find the real URLs.
- mass.gov /doc/cella-v-attorney-general-sjc-w13893/download (official slip opinion): 404.
- No fetch was refused or blocked by any site's terms. ecode360 pages were each fetched once, except D034 which was fetched twice (second call to get character-for-character text and the history line). No sibling links followed, no ids enumerated.

## T2 status
- Hoboken: have it. Ch. 158 (section 158-2 per extractor), Ord. B-781, adopted 2025-07-09, from ecode360 (D034). Effective date NOT on the page; only secondary "July 2025" (D037).
- Jersey City: NOT fully. Have ordinance numbers 25-056/25-057 (algorithm ban believed to be 25-057, single source), first reading 2025-05-08, council approval reported 2025-05-22 (final vote scheduled 2025-05-21). No ordinance text, no confirmed adoption date, no effective date (only "June 2025" from D037). Needs the Jersey City Clerk ordinance PDF or code page (not fetched; city code host not on the target list).

## T3 note (from corpus D069, not from fetching)
D069 section 6.b: "A municipality shall be prohibited from enacting an ordinance that conflicts with this act." It does not state express preemption of existing ordinances. Section 9: takes effect "on the first day of the twelfth month next following the date of enactment" (approved July 20, 2026), which Day Pitney reports as July 1, 2027. The Hoboken and Jersey City bans are therefore in force until at least that date, and the conflict question is a legal analysis, not a retrieved fact. Day Pitney's silence on preemption is not evidence either way.

## T5 status
IP number 25-21 confirmed from official mass.gov documents (AG summary; 2026 ballot initiatives listing) and the SJC opinion copy. Strike date 2026-06-23 confirmed by WBUR (article date and "ruled"), gtlaw and the slip opinion copy (decided June 23, 2026). Residual risk: all via WebFetch extraction; official slip opinion URL returned 404.
