"""Turn one raw model candidate into a provenance-checked rule record.

Everything here is deterministic code. The model proposes; this module enforces the
epistemic rules and logs every change it makes as {"field","before","after","reason"}.

Enforced here, independent of model behaviour:
  * a field is "stated" only if it cites at least one VERIFIED span; otherwise it is
    lowered to "inferred" (and logged)
  * a field is "derived" only if it names a rule and cites a verified span
  * a field with no value is "unknown", whatever the model claimed
  * effective_date is kept only if stated (the date must be recoverable from the cited
    span) or derived by a rule that extraction is allowed to apply; the engine-owned
    California default rule is refused
  * status is never a substantive value when it is not establishable: it becomes null,
    effective_date becomes null, conflict_flag true, and the record is a WITHHELD
    PROJECTION that is kept out of rules.json
  * coverage conditions that cannot be expressed as predicates are kept in needs_fact,
    so scope is never silently widened
  * rental-housing applicability is "stated" only if a verified span contains rental or
    housing words
  * a record whose source is a summary or commentary is marked as such in title,
    requirement and citation, and its confidence is capped
"""
import datetime
import json
import re

from navigator.extract.corpus import collapse
from navigator.extract.spans import VERIFIED, REFUSED_SUMMARY
from navigator.normalize.effective_date import ENGINE_OWNED_RULE_IDS

CATEGORIES = ("rent_increase_limits", "just_cause_eviction", "security_deposits",
              "application_screening_fees", "screening_restrictions", "algorithmic_rent_setting")
STATUS_ENUM = ("in_force", "not_yet_effective", "pending", "failed")
EPI = ("stated", "derived", "inferred", "unknown")
EPI_STRENGTH = {"stated": 3, "derived": 2, "inferred": 1, "unknown": 0}
MODEL_EPI_FIELDS = ("jurisdiction", "category", "title", "requirement", "key_value",
                    "coverage_conditions", "exemptions", "status", "effective_date", "citation",
                    "rental_housing_applicability")
SOURCE_KINDS = ("primary_legal_text", "bill_text", "official_summary", "secondary_commentary",
                "motion_or_report_request", "navigation_or_index", "other")
INSTRUMENT_TYPES = ("statute", "ordinance", "regulation", "bill", "ballot_measure", "motion",
                    "guidance", "other")
NON_PRIMARY_MARKS = {
    "official_summary": "official summary, not the legal text",
    "secondary_commentary": "secondary source, not the legal text",
}
SUMMARY_ONLY_MARK = "fetch-tool summary, not source text"
SUSPECT_KINDS = ("navigation_or_index", "motion_or_report_request")
FACTS = ("jurisdiction", "state", "year_built", "units")
STRING_FACTS = ("jurisdiction", "state")
OPS = ("eq", "in", "lte", "gte", "lt", "gt")
CO_BASIS = "certificate_of_occupancy"
RELATION_KINDS = ("yields_to", "preempts_pending", "stacks")

_DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
_HOUSING_RE = re.compile(
    r"\b(rent|rents|rental|rentals|renter|renters|housing|landlord|landlords|tenant|tenants|"
    r"tenancy|tenancies|lease|leases|leased|dwelling|dwellings|residential|apartment|apartments)\b",
    re.I)
_JURIS_STATE_RE = re.compile(r"^[A-Z]{2}$")
_JURIS_CITY_RE = re.compile(r"^.+, [A-Z]{2}$")


# ------------------------------------------------------------------ helpers

class Built(object):
    """Result of build_record for one candidate."""
    def __init__(self):
        self.record = None
        self.withheld = False
        self.drops = []
        self.norms = []


def note(norms, field, before, after, reason):
    norms.append({"field": field, "before": before, "after": after, "reason": reason})


def has_housing_terms(text):
    return bool(text and _HOUSING_RE.search(text))


_MON = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
        r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_MONTH_NUM = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
              "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _mnum(name):
    return _MONTH_NUM[name.lower()[:3]]


def dates_in_text(text):
    """(full dates, year-months, years) that appear in text, as sets of tuples/ints."""
    fulls, months, years = set(), set(), set()
    t = text or ""

    def add_full(y, m, d):
        if 1 <= m <= 12 and 1 <= d <= 31:
            fulls.add((y, m, d))

    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", t):
        add_full(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\b", t):
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        add_full(y, int(m.group(1)), int(m.group(2)))
    for m in re.finditer(r"\b" + _MON + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", t, re.I):
        add_full(int(m.group(3)), _mnum(m.group(1)), int(m.group(2)))
    for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MON + r"\.?,?\s+(\d{4})\b", t, re.I):
        add_full(int(m.group(3)), _mnum(m.group(2)), int(m.group(1)))
    for m in re.finditer(r"\b" + _MON + r"\.?,?\s+(\d{4})\b", t, re.I):
        months.add((int(m.group(2)), _mnum(m.group(1))))
    for y, mo, _d in fulls:
        months.add((y, mo))
    for m in re.finditer(r"\b(1[89]\d{2}|20\d{2})\b", t):
        years.add(int(m.group(1)))
    for y, _m in months:
        years.add(y)
    return fulls, months, years


def date_supported(claim, text):
    """True if the claimed YYYY, YYYY-MM or YYYY-MM-DD appears in text in some common format."""
    fulls, months, years = dates_in_text(text)
    parts = [int(p) for p in claim.split("-")]
    if len(parts) == 3:
        return tuple(parts) in fulls
    if len(parts) == 2:
        return tuple(parts) in months
    return parts[0] in years


def _valid_date(s):
    if not isinstance(s, str) or not _DATE_RE.fullmatch(s.strip()):
        return False
    parts = [int(p) for p in s.strip().split("-")]
    try:
        datetime.date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except ValueError:
        return False
    return True


def is_engine_owned(rule_name):
    """True for a derivation rule that belongs to the engine (the California default
    effective-date rule), by exact id or by a conservative description match."""
    if not isinstance(rule_name, str) or not rule_name.strip():
        return False
    r = rule_name.strip()
    if r in ENGINE_OWNED_RULE_IDS:
        return True
    low = r.lower().replace("_", " ")
    if "default" in low and any(w in low for w in ("effective", "operative", "january", "jan ")):
        return True
    if "jan" in low and re.search(r"\b1\b|\bfirst\b", low) and ("next" in low or "following" in low) \
            and ("year" in low or "ca " in low or "california" in low):
        return True
    return False


def level_for(jurisdiction):
    if _JURIS_STATE_RE.match(jurisdiction):
        return "state"
    if _JURIS_CITY_RE.match(jurisdiction):
        return "city"
    return None


# ---------------------------------------------------------------- epistemics

def _enforce(field, present, entry, verified_ids, norms, default_missing="inferred"):
    cat, basis, rule, sids = None, "", None, []
    if isinstance(entry, dict):
        c = entry.get("category")
        cat = c.strip().lower() if isinstance(c, str) else None
        if cat not in EPI:
            if cat is not None:
                note(norms, "epistemics.%s.category" % field, c, None, "not one of the four categories")
            cat = None
        basis = collapse(entry.get("basis") or "")
        r = entry.get("derivation_rule")
        rule = r.strip() if isinstance(r, str) and r.strip() else None
        raw_ids = entry.get("span_ids")
        raw_ids = raw_ids if isinstance(raw_ids, list) else []
        sids = [s for s in raw_ids if isinstance(s, str) and s in verified_ids]
        lost = [s for s in raw_ids if s not in sids]
        if lost:
            note(norms, "epistemics.%s.span_ids" % field, list(raw_ids), sids,
                 "span id(s) %s are not verified spans of this record" % lost)
    if not present:
        if cat not in (None, "unknown"):
            note(norms, "epistemics.%s.category" % field, cat, "unknown",
                 "the field has no value, so it cannot be %s" % cat)
        return {"category": "unknown", "basis": basis or "The source does not supply this.",
                "derivation_rule": None, "span_ids": []}
    if cat is None:
        cat = default_missing
        note(norms, "epistemics.%s.category" % field, None, cat,
             "the model gave no valid category for a field that has a value; treated as %s" % cat)
        basis = (basis + " " if basis else "") + "[category assigned by pipeline: the model did not classify this field]"
    if cat == "unknown":
        cat = "inferred"
        note(norms, "epistemics.%s.category" % field, "unknown", "inferred",
             "the model called the field unknown but supplied a value; kept as inferred, not stated")
    if cat == "stated" and not sids:
        note(norms, "epistemics.%s.category" % field, "stated", "inferred",
             "stated requires at least one verified supporting span")
        cat = "inferred"
        basis = (basis + " " if basis else "") + "[lowered by pipeline: no verified span cited]"
    if cat == "derived" and (not rule or not sids):
        why = "no named derivation rule" if not rule else "no verified span cited"
        note(norms, "epistemics.%s.category" % field, "derived", "inferred",
             "derived requires a named rule and a verified span (%s)" % why)
        cat = "inferred"
        basis = (basis + " " if basis else "") + "[lowered by pipeline: %s]" % why
    if cat != "derived":
        rule = None
    return {"category": cat, "basis": basis, "derivation_rule": rule, "span_ids": sids}


def weakest(categories):
    cats = [c for c in categories if c in EPI]
    return min(cats, key=lambda c: EPI_STRENGTH[c]) if cats else "unknown"


# ------------------------------------------------------------------ coverage

def _clean_predicate(p):
    """Return (clean predicate or None, reason)."""
    if not isinstance(p, dict):
        return None, "not an object"
    fact, op, value, basis = p.get("fact"), p.get("op"), p.get("value"), p.get("basis")
    if fact not in FACTS:
        return None, "fact %r is not one of %s" % (fact, ", ".join(FACTS))
    if op not in OPS:
        return None, "op %r is not one of %s" % (op, ", ".join(OPS))
    if basis not in (None, "", CO_BASIS):
        return None, "basis %r is not %s" % (basis, CO_BASIS)
    if basis == CO_BASIS and fact != "year_built":
        return None, "basis %s only applies to year_built" % CO_BASIS
    vals = value if (op == "in" and isinstance(value, list)) else [value]
    if op == "in" and not isinstance(value, list):
        return None, "op in needs a list"
    if not vals:
        return None, "empty value list"
    if fact in STRING_FACTS:
        if op not in ("eq", "in"):
            return None, "op %s is not valid for %s" % (op, fact)
        for v in vals:
            if not isinstance(v, str) or not v.strip():
                return None, "value %r is not a non-empty string" % (v,)
            v = v.strip()
            ok = (_JURIS_STATE_RE.match(v) or _JURIS_CITY_RE.match(v)) if fact == "jurisdiction" \
                else _JURIS_STATE_RE.match(v)
            if not ok:
                return None, "value %r is not a state code%s" % (
                    v, " or 'City, ST'" if fact == "jurisdiction" else "")
        clean_vals = [v.strip() for v in vals]
    else:
        clean_vals = []
        for v in vals:
            if isinstance(v, bool):
                return None, "boolean value"
            if isinstance(v, int):
                clean_vals.append(v)
            elif isinstance(v, str) and fact == "year_built" and _valid_date(v):
                clean_vals.append(v.strip())
            elif isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
                clean_vals.append(int(v.strip()))
            else:
                return None, "value %r is not a number%s" % (v, " or date" if fact == "year_built" else "")
    out = {"fact": fact, "op": op, "value": clean_vals if op == "in" else clean_vals[0]}
    if basis == CO_BASIS:
        out["basis"] = CO_BASIS
    return out, ""


def normalize_coverage(raw, norms):
    out = {"predicates": [], "needs_fact": [], "note": ""}
    if raw is None:
        return out
    if isinstance(raw, str):
        txt = collapse(raw)
        if txt:
            out["needs_fact"].append("unparsed_coverage_condition: " + txt)
            out["note"] = txt
            note(norms, "coverage_conditions", raw, out,
                 "a free-text coverage condition cannot be evaluated by the engine; kept in needs_fact "
                 "so the rule is not silently universal")
        return out
    if not isinstance(raw, dict):
        txt = collapse(json.dumps(raw, ensure_ascii=False))
        out["needs_fact"].append("unparsed_coverage_condition: " + txt)
        note(norms, "coverage_conditions", raw, out, "coverage_conditions was not an object or string")
        return out
    preds = raw.get("predicates")
    for p in (preds if isinstance(preds, list) else []):
        clean, why = _clean_predicate(p)
        if clean:
            out["predicates"].append(clean)
        else:
            txt = collapse(json.dumps(p, ensure_ascii=False, sort_keys=True))
            out["needs_fact"].append("unparseable_coverage_predicate: " + txt)
            note(norms, "coverage_conditions.predicates", p, None,
                 "predicate dropped (%s); preserved in needs_fact so scope is not widened" % why)
    nf = raw.get("needs_fact")
    if isinstance(nf, str):
        nf = [nf]
    for x in (nf if isinstance(nf, list) else []):
        s = collapse(x) if isinstance(x, str) else collapse(json.dumps(x, ensure_ascii=False))
        if s and s not in out["needs_fact"]:
            out["needs_fact"].append(s)
    n = raw.get("note")
    out["note"] = collapse(n) if isinstance(n, str) else ""
    return out


# ----------------------------------------------------------------- relations

def normalize_relations(raw, own_category, verified_ids, norms):
    out = []
    for r in (raw if isinstance(raw, list) else []):
        if not isinstance(r, dict):
            note(norms, "relations", r, None, "relation entry is not an object; dropped")
            continue
        kind = r.get("interaction")
        tj = r.get("target_jurisdiction")
        tc = r.get("target_category") or own_category
        if kind not in RELATION_KINDS or not isinstance(tj, str) or not tj.strip() or tc not in CATEGORIES:
            note(norms, "relations", r, None, "relation has an invalid interaction, jurisdiction or category; dropped")
            continue
        ent = _enforce("relations", True, {"category": r.get("category"), "basis": r.get("basis"),
                                           "derivation_rule": r.get("derivation_rule"),
                                           "span_ids": r.get("span_ids")}, verified_ids, norms)
        out.append({"interaction": kind, "target_jurisdiction": collapse(tj),
                    "target_category": tc, "category": ent["category"],
                    "basis": ent["basis"], "span_ids": ent["span_ids"]})
    return out


# -------------------------------------------------------------- build_record

def build_record(cand, index, doc, spans, ctx):
    """spans: {span_id: {"result", "normalized", "text", "supports"}} after verification.
    ctx: dict with as_of (date), doc_source_kind, prompt_version, prompt_sha256, transport,
         model, external (list of claim dicts), summary_policy ("drop" or "flag")."""
    b = Built()
    norms = b.norms
    if not isinstance(cand, dict):
        b.drops.append("candidate_not_an_object")
        return b

    for k in ("jurisdiction", "category", "title", "requirement", "citation", "quoted_span"):
        v = cand.get(k)
        if not isinstance(v, str) or not v.strip():
            b.drops.append("missing_required_field:%s" % k)
    if b.drops:
        return b

    category = cand["category"].strip()
    if category not in CATEGORIES:
        b.drops.append("category_outside_schema:%s" % category)
        return b
    juris = collapse(cand["jurisdiction"])
    level = level_for(juris)
    if level is None:
        b.drops.append("bad_jurisdiction:%s" % juris)
        return b
    if cand.get("level") not in (None, level):
        note(norms, "level", cand.get("level"), level, "level is derived from the jurisdiction format")

    primary = spans.get("quoted_span")
    summary_only_flag = False
    if primary is None or primary["result"] != VERIFIED:
        res = primary["result"] if primary else "missing"
        if res == REFUSED_SUMMARY and ctx.get("summary_policy") == "flag":
            summary_only_flag = True
            note(norms, "quoted_span", cand.get("quoted_span"), primary["normalized"],
                 "summary-policy flag: the span lies inside a [SUMMARY, NOT SOURCE TEXT] region and is "
                 "NOT source evidence; the record is kept as summary-only with lowered confidence and "
                 "conflict_flag true")
        else:
            extra = (" (summary_only_support: the only quotable support lies inside a "
                     "[SUMMARY, NOT SOURCE TEXT] region)" if res == REFUSED_SUMMARY else "")
            b.drops.append("quoted_span_%s%s" % (res, extra))
            return b

    verified_ids = {sid for sid, s in spans.items() if s["result"] == VERIFIED}
    sup_out = []
    for sid, s in spans.items():
        if sid == "quoted_span":
            continue
        if s["result"] == VERIFIED:
            sup_out.append({"id": sid, "text": s["normalized"], "supports": s.get("supports", []),
                            "verified": True})
        else:
            note(norms, "supporting_spans", {"id": sid, "text": s.get("text")}, None,
                 "supporting span removed: %s" % s["result"])

    raw_epi = cand.get("epistemics") if isinstance(cand.get("epistemics"), dict) else {}
    epi = {}

    # source kind and instrument type
    sk = cand.get("source_kind") or ctx.get("doc_source_kind")
    if sk not in SOURCE_KINDS:
        note(norms, "source_kind", sk, "other", "not one of the allowed source kinds")
        sk = "other"
    inst = cand.get("instrument_type")
    if inst not in INSTRUMENT_TYPES:
        note(norms, "instrument_type", inst, "other", "not one of the allowed instrument types")
        inst = "other"

    # simple text fields
    title = collapse(cand["title"])
    requirement = collapse(cand["requirement"])
    citation = collapse(cand["citation"])
    kv = cand.get("key_value")
    key_value = collapse(kv) if isinstance(kv, str) and collapse(kv) else (
        collapse(str(kv)) if isinstance(kv, (int, float)) and not isinstance(kv, bool) else None)
    ex = cand.get("exemptions")
    exemptions = collapse(ex) if isinstance(ex, str) and collapse(ex) else None

    # coverage
    coverage = normalize_coverage(cand.get("coverage_conditions"), norms)

    # rental-housing applicability
    rha_entry = raw_epi.get("rental_housing_applicability")
    if isinstance(rha_entry, dict):
        rha = _enforce("rental_housing_applicability", True, rha_entry, verified_ids, norms)
    else:
        q = spans["quoted_span"]["normalized"] if spans["quoted_span"]["result"] == VERIFIED else ""
        if has_housing_terms(q):
            rha = {"category": "derived",
                   "basis": "The model gave no classification; the verified primary span contains rental or housing words.",
                   "derivation_rule": "HOUSING_TERMS_IN_PRIMARY_SPAN", "span_ids": ["quoted_span"]}
            note(norms, "epistemics.rental_housing_applicability", None, rha["category"],
                 "no entry from the model; derived mechanically from the primary span")
        else:
            rha = {"category": "unknown",
                   "basis": "The model gave no classification and the primary span contains no rental or housing words.",
                   "derivation_rule": None, "span_ids": []}
            note(norms, "epistemics.rental_housing_applicability", None, "unknown", "no entry from the model")
    if rha["category"] == "stated":
        texts = " ".join(spans[i]["normalized"] for i in rha["span_ids"] if i in spans)
        if not has_housing_terms(texts):
            note(norms, "epistemics.rental_housing_applicability.category", "stated", "inferred",
                 "no cited verified span contains rental or housing words, so applicability to rental "
                 "housing is not stated by the source")
            rha["category"] = "inferred"
            rha["basis"] = (rha["basis"] + " " if rha["basis"] else "") + \
                "[lowered by pipeline: no cited span contains rental or housing words]"
    epi["rental_housing_applicability"] = rha
    if rha["category"] != "stated" and not any(
            n.startswith("rental_housing_applicability") for n in coverage["needs_fact"]):
        coverage["needs_fact"].append(
            "rental_housing_applicability: the source text does not itself say this rule governs "
            "residential rental housing (category: %s)" % rha["category"])
        note(norms, "coverage_conditions.needs_fact", None, coverage["needs_fact"][-1],
             "rental-housing applicability is not stated, so the scope is not left universal")

    # epistemics for plain fields
    epi["jurisdiction"] = _enforce("jurisdiction", True, raw_epi.get("jurisdiction"), verified_ids, norms)
    epi["category"] = _enforce("category", True, raw_epi.get("category"), verified_ids, norms)
    if rha["category"] != "stated" and epi["category"]["category"] in ("stated", "derived"):
        note(norms, "epistemics.category.category", epi["category"]["category"], "inferred",
             "filing a general provision under a rental category is a project mapping, not a statement of the source")
        epi["category"]["category"] = "inferred"
        epi["category"]["derivation_rule"] = None
    epi["title"] = _enforce("title", True, raw_epi.get("title"), verified_ids, norms)
    epi["requirement"] = _enforce("requirement", True, raw_epi.get("requirement"), verified_ids, norms)
    epi["key_value"] = _enforce("key_value", key_value is not None, raw_epi.get("key_value"), verified_ids, norms)
    has_cov = bool(coverage["predicates"] or coverage["needs_fact"] or coverage["note"])
    epi["coverage_conditions"] = _enforce("coverage_conditions", has_cov, raw_epi.get("coverage_conditions"),
                                          verified_ids, norms)
    epi["exemptions"] = _enforce("exemptions", exemptions is not None, raw_epi.get("exemptions"), verified_ids, norms)
    epi["citation"] = _enforce("citation", True, raw_epi.get("citation"), verified_ids, norms)

    # ---- status
    raw_status = cand.get("status")
    est = cand.get("status_establishable")
    sbasis = cand.get("status_basis")
    reasons = []
    if raw_status not in STATUS_ENUM:
        reasons.append("status is missing or not one of %s (got %r)" % (", ".join(STATUS_ENUM), raw_status))
    if est is False:
        reasons.append("the model declared status_establishable false")
    if sbasis == "unknown":
        reasons.append("status_basis is unknown")
    status_entry_raw = raw_epi.get("status")
    if isinstance(status_entry_raw, dict) and str(status_entry_raw.get("category", "")).lower() == "unknown":
        reasons.append("epistemics.status.category is unknown")
    if isinstance(status_entry_raw, dict) and is_engine_owned(status_entry_raw.get("derivation_rule")):
        reasons.append("status rests on a derivation rule that belongs to the engine (%s)"
                       % status_entry_raw.get("derivation_rule"))
    if raw_status in STATUS_ENUM and est is None and sbasis not in ("stated", "derived", "inferred") \
            and not isinstance(status_entry_raw, dict):
        reasons.append("no status_establishable flag or basis was given")
    withheld = bool(reasons)

    if withheld:
        epi["status"] = {"category": "unknown",
                         "basis": collapse((status_entry_raw or {}).get("basis") or "") if isinstance(status_entry_raw, dict)
                         else "Operative status is not establishable from this document.",
                         "derivation_rule": None, "span_ids": []}
        if not epi["status"]["basis"]:
            epi["status"]["basis"] = "Operative status is not establishable from this document."
        if raw_status in STATUS_ENUM:
            note(norms, "status", raw_status, None,
                 "withheld, not guessed: %s" % "; ".join(reasons))
        status = None
    else:
        epi["status"] = _enforce("status", True, status_entry_raw if isinstance(status_entry_raw, dict) else
                                 {"category": sbasis, "basis": ""}, verified_ids, norms)
        status = raw_status

    # ---- effective date
    ed = cand.get("effective_date")
    if isinstance(ed, str):
        ed = ed.strip() or None
    if ed is not None and not _valid_date(ed):
        note(norms, "effective_date", ed, None, "not a YYYY, YYYY-MM or YYYY-MM-DD calendar date")
        ed = None
    if withheld and ed is not None:
        note(norms, "effective_date", ed, None, "status is not establishable, so no operative date is carried")
        ed = None
    ed_entry = _enforce("effective_date", ed is not None, raw_epi.get("effective_date"), verified_ids, norms)
    if ed is not None:
        reason = None
        if ed_entry["category"] not in ("stated", "derived"):
            reason = ("a date is carried only if stated or derived; this one is %s" % ed_entry["category"])
        elif ed_entry["category"] == "derived" and is_engine_owned(ed_entry["derivation_rule"]):
            reason = ("derivation rule %r belongs to the engine "
                      "(navigator/normalize/effective_date.py); extraction records what the source says"
                      % ed_entry["derivation_rule"])
        elif ed_entry["category"] == "stated":
            texts = " ".join(spans[i]["normalized"] for i in ed_entry["span_ids"] if i in spans)
            if not date_supported(ed, texts):
                reason = "the stated date does not appear in the cited verified span(s)"
        if reason:
            note(norms, "effective_date", ed, None, reason)
            ed = None
            ed_entry = {"category": "unknown",
                        "basis": "Date withheld by pipeline: %s. %s" % (reason, ed_entry["basis"]),
                        "derivation_rule": None, "span_ids": []}
    epi["effective_date"] = ed_entry

    # ---- relations
    relations = normalize_relations(cand.get("relations"), category, verified_ids, norms)

    # ---- conflict flag and note
    cflag = cand.get("conflict_flag") is True
    cnote = collapse(cand["conflict_note"]) if isinstance(cand.get("conflict_note"), str) and \
        collapse(cand["conflict_note"]) else None
    if withheld:
        if not cflag:
            note(norms, "conflict_flag", cand.get("conflict_flag"), True, "status not establishable")
        cflag = True
        if not cnote:
            cnote = ("Operative status is not establishable from this document. "
                     + epi["status"]["basis"])
            note(norms, "conflict_note", cand.get("conflict_note"), cnote, "withheld record needs a note")
    suspect = [k for k in (sk, ctx.get("doc_source_kind")) if k in SUSPECT_KINDS]
    if suspect:
        cflag = True
        extra = ("The model classed the source as %s yet returned a rule from it; review before use." % suspect[0])
        cnote = (cnote + " " if cnote else "") + extra
        note(norms, "conflict_flag", None, True, extra)
    if summary_only_flag:
        cflag = True
        extra = ("SUMMARY-ONLY SOURCE: the document contains no quotable source text for this rule; "
                 "its content comes from a fetch-tool summary that was not byte-verified.")
        cnote = (cnote + " " if cnote else "") + extra
    for claim in ctx.get("external") or []:
        extra = ("External claim, outside the source and unverified: %s (origin: %s)"
                 % (claim.get("text"), claim.get("origin")))
        cnote = (cnote + " " if cnote else "") + extra
        if claim.get("sets_conflict_flag", True):
            cflag = True
        note(norms, "conflict_note", None, extra, "attached from navigator/extract/external_claims.json")

    # ---- status / date consistency against as_of (flag only, never change values)
    as_of = ctx.get("as_of")
    if as_of and ed and len(ed) == 10 and status in ("in_force", "not_yet_effective"):
        d = datetime.date.fromisoformat(ed)
        bad = (status == "in_force" and d > as_of) or (status == "not_yet_effective" and d <= as_of)
        if bad:
            extra = ("Status %s is inconsistent with effective_date %s as of %s; review." % (status, ed, as_of))
            cnote = (cnote + " " if cnote else "") + extra
            cflag = True
            note(norms, "conflict_flag", None, True, extra)

    # ---- source-kind marking at the point of use
    mark = NON_PRIMARY_MARKS.get(sk)
    lead = {"official_summary": "Per an official summary, not the legal text: ",
            "secondary_commentary": "Per a secondary source, not the legal text: "}.get(sk)
    if summary_only_flag:
        mark = SUMMARY_ONLY_MARK
        lead = "Per a fetch-tool summary, not source text: "
    if mark:
        if "[" + mark + "]" not in title:
            title = "%s [%s]" % (title, mark)
        if "not the legal text" not in requirement and "not source text" not in requirement:
            requirement = lead + requirement
        suffix = " (cited from: %s; the legal text itself was not available)" % mark
        if suffix not in citation:
            citation += suffix
        note(norms, "title/requirement/citation", None, "marked: " + mark,
             "the source is not the legal text, so the distinction is shown at the point of use")

    # ---- confidence
    conf = cand.get("confidence")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not (0 <= conf <= 1):
        if conf is not None:
            note(norms, "confidence", conf, None, "not a number between 0 and 1")
        conf = None
    caps = []
    if mark:
        caps.append((0.4 if summary_only_flag else 0.6, "source is not the legal text"))
    if withheld:
        caps.append((0.5, "projection withheld: status not establishable"))
    if any(epi[f]["category"] == "inferred" for f in
           ("status", "effective_date", "requirement", "coverage_conditions", "category")):
        caps.append((0.75, "a key field is inferred, not stated or derived"))
    if rha["category"] != "stated":
        caps.append((0.75, "rental-housing applicability is not stated"))
    for cap, why in caps:
        if conf is None:
            conf = cap
            note(norms, "confidence", None, cap, "no confidence given; set to the cap (%s)" % why)
        elif conf > cap:
            note(norms, "confidence", conf, cap, why)
            conf = cap

    # ---- overrides/interaction placeholders (resolved at assembly)
    epi["overrides"] = {"category": "unknown", "basis": "No precedence relationship has been resolved yet.",
                        "derivation_rule": None, "span_ids": []}
    epi["interaction"] = dict(epi["overrides"])
    epi["level"] = {"category": "derived", "basis": "Level follows from the jurisdiction format "
                    "(a bare state code is state, 'City, ST' is city).",
                    "derivation_rule": "LEVEL_FROM_JURISDICTION_FORMAT", "span_ids": []}

    status_basis = epi["status"]["category"]
    rec = {
        "team_rule_id": None,
        "jurisdiction": juris,
        "level": level,
        "category": category,
        "status": status,
        "title": title,
        "requirement": requirement,
        "key_value": key_value,
        "coverage_conditions": coverage,
        "exemptions": exemptions,
        "overrides": [],
        "interaction": None,
        "effective_date": ed,
        "citation": citation,
        "source_doc_id": doc.source_doc_id,
        "source_url": doc.url,
        "quoted_span": spans["quoted_span"]["normalized"],
        "confidence": conf,
        "conflict_flag": bool(cflag),
        "conflict_note": cnote,
        # extensions (the schema allows additional properties)
        "status_establishable": status is not None,
        "status_basis": status_basis if status is not None else "unknown",
        "epistemics": epi,
        "supporting_spans": sup_out,
        "source_kind": sk,
        "instrument_type": inst,
        "retrieved_at": doc.retrieved_at,
        "relations_declared": relations,
        "relations_unresolved": [],
        "external_unverified_claims": [dict(c) for c in (ctx.get("external") or [])],
        "provenance": {
            "doc_id": doc.doc_id,
            "text_file": doc.rel_path,
            "text_file_sha256_local": doc.text_file_sha256_local,
            "manifest_hash_scope": "unknown",
            "manifest_sha256_reported_unverified": doc.manifest_sha256,
            "prompt_version": ctx.get("prompt_version"),
            "prompt_sha256": ctx.get("prompt_sha256"),
            "transport": ctx.get("transport"),
            "model": ctx.get("model"),
            "as_of": as_of.isoformat() if as_of else None,
            "candidate_index": index,
        },
    }
    if summary_only_flag:
        rec["provenance_grade"] = "summary_only"
        rec["quoted_span_kind"] = "summary_not_source_text"
    else:
        rec["provenance_grade"] = "source_text"
        rec["quoted_span_kind"] = "source_text"
    if status is None:
        rec["projection_valid"] = False
        rec["projection_withheld_reason"] = "; ".join(reasons)
    b.record = rec
    b.withheld = status is None
    return b
