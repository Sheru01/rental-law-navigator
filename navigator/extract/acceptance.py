"""Post-run confirmation. READ ONLY: nothing here changes an extraction result.

Extraction logic has no skip list and no per-document branching. This module holds the
lead's expectations as DATA and reports pass or fail against what came back, so a model
that over-reads is caught and reported rather than trusted.

Two things are enforced as hard invariants in assemble.py using FORBIDDEN_RECORDS:
  Los Angeles must emit zero algorithmic_rent_setting rules (D039 is only a motion
  requesting a report).
Everything else here is a check whose outcome depends on model behaviour.
"""
from navigator.extract.corpus import collapse

SHELL_DOCS = ("D078", "D082", "D012", "D013", "D014", "D029", "D031", "D036")

FORBIDDEN_RECORDS = (
    {"jurisdiction": "Los Angeles, CA", "category": "algorithmic_rent_setting",
     "why": "D039 is a 2024 motion requesting a report, not a ban"},
)

KNOWN_GAPS = (
    {"name": "SB 763",
     "detail": "No corpus document exists for SB 763. T1 names it, but no record may be invented; "
               "AB 325 (D022) is extracted instead."},
    {"name": "Jersey City ordinance text",
     "detail": "The Jersey City algorithmic-rent ordinance text was not obtained. Only secondary "
               "summaries exist (corpus/fetched/SUPP_JC_*, D035). No Jersey City rule may be invented."},
    {"name": "Hoboken Ord. B-781 effective date",
     "detail": "Hoboken Ord. B-781 is recorded as adopted 7-9-2025 with NO effective date in the "
               "source. No effective date may be invented."},
)

NJ_SENTENCE_1 = "A municipality shall be prohibited from enacting an ordinance that conflicts with this act."
NJ_SENTENCE_2 = ("This subsection shall not be construed to prohibit the enactment of ordinances explicitly "
                 "authorized or required by any other law.")


def _check(name, passed, detail, applies=True):
    return {"name": name, "applies": applies, "passed": passed, "detail": detail}


def _recs_for(doc_id, rules, withheld):
    return [r for r in list(rules) + list(withheld) if r["provenance"]["doc_id"] == doc_id]


def _span_texts(rec):
    return [rec["quoted_span"]] + [s["text"] for s in rec.get("supporting_spans", [])]


def _all_text(rec):
    cc = rec.get("coverage_conditions") or {}
    return " ".join(str(x) for x in (rec.get("requirement"), rec.get("exemptions"), rec.get("conflict_note"),
                                     cc.get("note"), " ".join(cc.get("needs_fact", []))) if x).lower()


def run_acceptance(doc_outcomes, rules, withheld, transport_name):
    """doc_outcomes: {doc_id: {"status": "ok"|"error"|"skipped", "n_candidates": int}}."""
    checks = []
    real = transport_name != "echo"

    # eight navigation shells must come back empty
    for d in SHELL_DOCS:
        o = doc_outcomes.get(d)
        if o is None:
            checks.append(_check("shell_empty:%s" % d, None, "document not in this run", applies=False))
        elif o["status"] != "ok":
            checks.append(_check("shell_empty:%s" % d, False, "document errored: %s" % o.get("error")))
        else:
            empty = o["n_candidates"] == 0
            detail = "model returned %d candidate rule(s)" % o["n_candidates"]
            if not real:
                detail += " (echo transport: empty by construction, NOT confirmed by a model)"
            checks.append(_check("shell_empty:%s" % d, empty, detail))

    # Los Angeles algorithmic rules
    la = [r for r in list(rules) + list(withheld)
          if r["jurisdiction"] == "Los Angeles, CA" and r["category"] == "algorithmic_rent_setting"]
    checks.append(_check("la_zero_algorithmic_rent_setting", not la,
                         "%d Los Angeles algorithmic_rent_setting record(s) in the output" % len(la)))

    # SB 763 must not be invented
    mention = [r["team_rule_id"] for r in list(rules) + list(withheld)
               if "sb 763" in _all_text(r) or "sb763" in _all_text(r) or "sb 763" in r["citation"].lower()
               or "sb-763" in r["citation"].lower()]
    checks.append(_check("no_sb763_record", not mention, "records mentioning SB 763: %s" % (mention or "none")))

    # D022
    r22 = _recs_for("D022", rules, withheld)
    if "D022" in doc_outcomes:
        if not r22:
            checks.append(_check("D022_record_present", False, "no D022 record (kept, withheld or otherwise)"))
        else:
            r = r22[0]
            texts = " ".join(" ".join(_span_texts(x)) for x in r22)
            checks.append(_check("D022_effective_date_null", all(x["effective_date"] is None for x in r22),
                                 "effective_date=%r" % r["effective_date"]))
            checks.append(_check("D022_status_not_substantive",
                                 all(x["status"] is None and x["status_establishable"] is False for x in r22),
                                 "status=%r establishable=%r" % (r["status"], r["status_establishable"])))
            checks.append(_check("D022_stated_facts_quoted",
                                 all(s in texts for s in ("CHAPTER 338", "October 06, 2025", "10/06/25 - Chaptered")),
                                 "needs spans containing CHAPTER 338, October 06, 2025 and 10/06/25 - Chaptered"))
            rha = r["epistemics"]["rental_housing_applicability"]["category"]
            checks.append(_check("D022_rental_applicability_not_stated", rha in ("inferred", "unknown"),
                                 "rental_housing_applicability=%s" % rha))
            nf = (r["coverage_conditions"] or {}).get("needs_fact", [])
            checks.append(_check("D022_scope_not_universal", bool(nf),
                                 "needs_fact=%s" % (nf or "empty (scope would read as every CA address)")))
            low = _all_text(r)
            checks.append(_check("D022_conditional_prohibition_captured",
                                 "coerc" in low and ("combination" in low or "conspiracy" in low or "contract" in low),
                                 "requirement/notes should mention the agreement-or-coercion condition"))
            checks.append(_check("D022_antitrust_savings_captured", "antitrust" in low,
                                 "exemptions/notes should mention that 16729(c) preserves antitrust law"))

    # D001
    r01 = _recs_for("D001", rules, withheld)
    if "D001" in doc_outcomes:
        if not r01:
            checks.append(_check("D001_record_present", False, "no D001 record"))
        else:
            r = r01[0]
            note = (r["conflict_note"] or "")
            checks.append(_check("D001_status_unestablishable",
                                 r["status"] is None and r["effective_date"] is None and r["conflict_flag"] is True,
                                 "status=%r effective_date=%r conflict_flag=%r"
                                 % (r["status"], r["effective_date"], r["conflict_flag"])))
            checks.append(_check("D001_external_dates_named",
                                 "2026-03-01" in note and "2026-01" in note,
                                 "conflict_note must name 2026-03-01 and 2026-01 as external and unverified"))

    # D069
    r69 = _recs_for("D069", rules, withheld)
    if "D069" in doc_outcomes:
        if not r69:
            checks.append(_check("D069_record_present", False, "no D069 record"))
        else:
            span_blob = collapse(" ".join(" ".join(_span_texts(x)) for x in r69))
            checks.append(_check("D069_preemption_sentence_verbatim", collapse(NJ_SENTENCE_1) in span_blob,
                                 "exact sentence must be inside a verified span"))
            checks.append(_check("D069_following_sentence_verbatim", collapse(NJ_SENTENCE_2) in span_blob,
                                 "following sentence must be inside a verified span"))
            note = " ".join((x["conflict_note"] or "") for x in r69).lower()
            want = ("enact", "maintain", "jersey city", "hoboken")
            missing = [w for w in want if w not in note]
            checks.append(_check("D069_conflict_note_limits_stated", not missing,
                                 "conflict_note should say enacting only, not maintain/enforce/void/supersede, "
                                 "and that neither Jersey City nor Hoboken is named; missing words: %s" % (missing or "none")))

    # D076
    r76 = _recs_for("D076", rules, withheld)
    if "D076" in doc_outcomes:
        if not r76:
            checks.append(_check("D076_record_present", False, "no D076 record"))
        else:
            r = r76[0]
            checks.append(_check("D076_final_passage_unsupported",
                                 r["status"] not in ("in_force", "not_yet_effective") and r["effective_date"] is None,
                                 "status=%r effective_date=%r" % (r["status"], r["effective_date"])))
            blob = _all_text(r)
            checks.append(_check("D076_incorporated_definitions_flagged", "98.0702" in blob,
                                 "needs_fact or notes must flag definitions incorporated from SDMC 98.0702"))

    # D081
    r81 = _recs_for("D081", rules, withheld)
    if "D081" in doc_outcomes:
        if not r81:
            checks.append(_check("D081_record_present", False, "no D081 record"))
        else:
            r = r81[0]
            ok = r["source_kind"] == "official_summary" and "not the legal text" in r["title"] \
                and "not the legal text" in r["requirement"]
            checks.append(_check("D081_summary_visible_at_point_of_use", ok,
                                 "source_kind=%r; marker in title=%s, requirement=%s" % (
                                     r["source_kind"], "not the legal text" in r["title"],
                                     "not the legal text" in r["requirement"])))

    failed = [c["name"] for c in checks if c["applies"] and c["passed"] is False]
    return {"transport": transport_name, "model_confirmed": real, "checks": checks,
            "failed": failed, "all_passed": not failed}
