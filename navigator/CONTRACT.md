# Frozen contract (Wave 0) - Rental Housing Law Navigator

Frozen by the Opus lead after a full survey of the 54 supplied corpus texts.
Workers must not change anything here without the lead's approval.

## 1. Corpus findings that drive the design

Verified by reading the text, not assumed:

- D022 is the full chaptered text of CA AB 325 (Chapter 338, approved
  2025-10-06). It adds Bus. & Prof. Code 16729, banning use or distribution
  of a "common pricing algorithm". The text states NO operative date, so the
  California default applies: effective 2026-01-01. This is exactly what T1
  tests. Encode the default-date inference; do not hard-code the answer.
- SB 763 has no document in the corpus. T1 names it, but we must not invent
  a record. We extract AB 325 and note the gap in the audit log.
- D069 is the NJ FAIR Act, P.L. 2026 c.043, approved 2026-07-20, with
  "This act shall take effect on the first day of" a later month, giving
  2027-07-01. It also contains an express municipal preemption clause
  ("A municipality shall be prohibited from enacting"). That clause is the
  textual basis for T3's conflict flag; quote it, do not assert preemption.
- D001 is Berkeley Ordinance 7,992 amending BMC ch. 13.63 (coordinated
  pricing algorithms). The captured text contains no effective date. The
  README records two published dates (2026-03-01 and 2026-01). Emit the
  record with effective_date null, conflict_flag true, and a note naming
  both. Do not pick one.
- D039 is only a 2024 LA City Council motion asking for a report on
  algorithmic rent software. It is NOT a ban. Los Angeles must produce zero
  algorithmic_rent_setting rules. Any such record is a defect.
- D045 (H.5222) and D046/D047 (S.2983) are bills. D047 shows S.2983
  reported from committee on 2026-03-12. Both are status "pending".
- D048 is M.G.L. c.40P s.4: "No city or town may enact, maintain or enforce
  rent control of any kind", with narrow exceptions. This, not the ballot
  question, is the in-force basis for never reporting a MA rent cap.
- The MA ballot question (IP 25-21, struck 2026-06-23) has NO text in the
  corpus; D059 is link-only. Worker D must fetch it or the MA-RENT-P1
  "failed" record has no source. Until then it is not written.
- No corpus text exists for the Hoboken, Jersey City, Newark or Santa Ana
  algorithmic bans, or Hoboken/Newark rent control. T2 depends entirely on
  Worker D's fetches.
- Junk captures that must yield zero rules: D078 (HRC home page), D082
  (rate archive), D012, D013, D014, D029, D031, D036 (nav shells).

## 2. team_rule_id

Use the organizer id where the change tests name one, so our output lines up
with theirs:
  CA-ALG-01, HOB-ALG-01, JC-ALG-01, NJ-ALG-01, MA-ALG-P1 (H.5222),
  MA-ALG-P2 (S.2983), MA-RENT-P1 (IP 25-21).
Everything else: JURIS-CAT-NN, e.g. SF-RENT-01, CA-DEP-01, NJ-DEP-01.
JURIS is the state code or a city abbreviation; CAT is one of
RENT, EVIC, DEP, FEE, SCRN, ALG.

## 3. coverage_conditions (object form)

    "coverage_conditions": {
      "predicates": [
        {"fact": "jurisdiction", "op": "eq", "value": "San Francisco, CA"},
        {"fact": "year_built", "op": "lte", "value": "1979-06-13",
         "basis": "certificate_of_occupancy"}
      ],
      "needs_fact": ["owner_unit_count"],
      "note": "plain sentence for the UI"
    }

- fact is one of: jurisdiction, state, year_built, units.
- op is one of: eq, in, lte, gte, lt, gt.
- basis "certificate_of_occupancy" means the legal test is the CO date, not
  the assessor's year built. Any address whose year_built equals the cutoff
  year evaluates to unknown, never true or false.
- needs_fact lists conditions we can never resolve from the supplied data
  (owner type, owner unit count, exemption filings, tenancy length,
  registration status). Any rule with a non-empty needs_fact that would
  otherwise apply resolves to unknown.

## 4. Evaluation (deterministic, three-valued)

evaluate(predicate) returns true, false or unknown.
  - fact missing in the address row  -> unknown
  - basis certificate_of_occupancy and year_built == cutoff year -> unknown
AND over predicates: any false -> false; else any unknown -> unknown;
else true.

result for a rule at an address:
  - status pending   -> "pending"      (reported, never "applies")
  - status failed    -> omitted from lookups entirely
  - as_of < effective_date -> "not_yet_effective"
  - coverage false   -> omitted
  - coverage unknown -> "unknown"
  - coverage true and a yields_to rule applies here -> "superseded"
  - otherwise        -> "applies"

Every entry carries an explanation naming the deciding fact, and
conflict_flag.

## 5. Precedence

In "overrides" with "interaction" set to one of:
  - "yields_to": this rule becomes "superseded" where the named rule
    applies. CA-RENT-01 (Civ. Code 1947.12, AB 1482) yields to SF-RENT-01
    and LA-RENT-01.
  - "stacks": both apply, no supersession. State and local algorithmic
    rules stack (CA-ALG-01 with SF-ALG-01, SD-ALG-01, BERK-ALG-01).
  - "preempts_pending": NJ-ALG-01 against HOB-ALG-01 and JC-ALG-01. Sets
    conflict_flag true on those addresses for human review. Never silently
    removes the local rule.

## 6. Status assignment

  in_force          enacted, operative date on or before as_of
  not_yet_effective enacted, operative date after as_of
  pending           a bill that has not been enacted
  failed            struck, defeated or withdrawn

CA chaptered statutes with no stated operative date default to January 1 of
the year following enactment. Record the inference in the audit log.

## 7. Quoted spans

quoted_span must be an exact substring of the source text file after
normalising whitespace runs to single spaces. The verifier recomputes this.
A record that fails is retried once with the span re-copied, then dropped
and logged. No record ships with an unverified span.

## 8. Jurisdiction resolution

Census Geocoder, batch, "geographies" endpoint, benchmark Public_AR_Current,
vintage Current_Current. Legal jurisdiction is the incorporated place, never
postal_city. Unresolved rows are flagged and reported, never guessed.

CORRECTION (lead, after Worker B's finding, verified against the raw bytes):
an earlier version of this section said NJ zips had lost their leading zero
and must be left-padded. That was WRONG. The CSV stores 07030 correctly. The
error came from reading the file with pandas, which coerced the zip column to
float and showed 7030.0. Do NOT pad or alter any zip; padding would have
corrupted the 140 NJ rows. What is true: 130 rows have a blank zip, and 27
NJ-state rows carry a zip outside the NJ range (Brooklyn, Manhattan, Rockland,
one Austin TX zip, one Stamford CT zip). Flag those, match on street plus city
plus state, and never correct them silently.

CORRECTION 2 (lead, after Worker B2's finding): the geographies addressbatch
response does NOT include an incorporated-place column. It returns 12 fields
ending at state FIPS, county FIPS, tract and block. legal_city therefore
cannot come from the batch alone; a second call to geographies/coordinates
with layers=all is required per matched row. Not confirmed live.

## 9. Outputs

navigator/out/rules.json, lookups.json (exactly 500 address ids),
changes.json, audit_log.jsonl. Formats per README section 5.

## 10. Test commands

  python -m navigator.tests.test_schema      records validate
  python -m navigator.tests.test_spans       every span verbatim
  python -m navigator.tests.test_coverage    500 ids, no MA rent cap
  python -m navigator.tests.test_changes     T1 to T5 expected shapes
