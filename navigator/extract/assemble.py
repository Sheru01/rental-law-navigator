"""Cross-document assembly: duplicates, invariants, schema validation, ids, precedence.

Per-document extraction cannot know other rules' ids, so this step runs once over all
surviving candidates. It is code only; there is no per-document branching.

ids (CONTRACT.md section 2): JURIS-CAT-NN, with JURIS the state code or a city
abbreviation, and CAT one of RENT, EVIC, DEP, FEE, SCRN, ALG. Bills and ballot
measures (instrument_type bill or ballot_measure) use the pending series
JURIS-CAT-P<n> (MA-ALG-P1, MA-ALG-P2, MA-RENT-P1). Numbering follows document order.
"""
import copy
import json
import re
from pathlib import Path

from navigator.extract.records import EPI_STRENGTH, weakest

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schema" / "rule_record.schema.json"

CITY_CODES = {
    "berkeley": "BERK", "boston": "BOS", "cambridge": "CAM", "hoboken": "HOB",
    "jersey city": "JC", "los angeles": "LA", "newark": "NWK", "san diego": "SD",
    "san francisco": "SF", "santa ana": "SANA",
}
CAT_CODES = {
    "rent_increase_limits": "RENT", "just_cause_eviction": "EVIC", "security_deposits": "DEP",
    "application_screening_fees": "FEE", "screening_restrictions": "SCRN",
    "algorithmic_rent_setting": "ALG",
}
PENDING_SERIES_TYPES = ("bill", "ballot_measure")


class Cand(object):
    """One model candidate, tracked from raw output to final outcome."""
    def __init__(self, doc_id, index, raw):
        self.doc_id = doc_id
        self.index = index
        self.raw = raw
        self.built = None
        self.span_report = []
        self.outcome = None            # kept | withheld | dropped
        self.final_id = None
        self.drop_reasons = []
        self.assembly_notes = []

    @property
    def rec(self):
        return self.built.record if self.built else None


class AssembleResult(object):
    def __init__(self):
        self.rules = []
        self.withheld = []
        self.violations = []
        self.duplicates = []
        self.relation_log = []


def juris_code(jurisdiction):
    if re.fullmatch(r"[A-Z]{2}", jurisdiction):
        return jurisdiction
    city = jurisdiction.rsplit(",", 1)[0].strip()
    code = CITY_CODES.get(city.casefold())
    if code:
        return code
    initials = "".join(w[0] for w in re.findall(r"[A-Za-z]+", city)).upper()
    return initials or "XX"


def load_schema(path=SCHEMA_PATH):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def validators(schema):
    try:
        from jsonschema import Draft202012Validator as V
    except ImportError:                      # jsonschema 3.x: the schema only uses draft-7 keywords
        from jsonschema import Draft7Validator as V
    relaxed = copy.deepcopy(schema)
    relaxed["properties"]["status"] = {"type": "null",
                                       "description": "withheld projection: status is not establishable"}
    return V(schema), V(relaxed)


def _citekey(citation):
    base = re.sub(r"\s*\(cited from .*$", "", citation or "")
    return re.sub(r"[^a-z0-9]", "", base.lower())


def validate_record(rec, full_validator, relaxed_validator):
    """Return a list of error strings (empty if valid)."""
    v = relaxed_validator if rec.get("status") is None else full_validator
    probe = dict(rec)
    if not probe.get("team_rule_id"):
        probe["team_rule_id"] = "TMP-00"
    return ["%s: %s" % (".".join(str(p) for p in e.absolute_path) or "(record)", e.message)
            for e in sorted(v.iter_errors(probe), key=lambda e: list(e.absolute_path))]


def assemble(cands, forbidden=(), schema=None):
    res = AssembleResult()
    schema = schema or load_schema()
    full_v, relaxed_v = validators(schema)

    for c in cands:
        if c.rec is None:
            c.outcome = "dropped"
            c.drop_reasons = list(c.built.drops) if c.built else ["no_build_result"]
    live = [c for c in cands if c.rec is not None]

    # 1. same pending measure seen on two pages
    seen = {}
    for c in live:
        r = c.rec
        if r["status"] in ("pending", "failed"):
            key = (r["jurisdiction"].casefold(), r["category"], r["status"], _citekey(r["citation"]))
            if key in seen:
                first = seen[key]
                c.outcome = "dropped"
                c.drop_reasons.append("duplicate_of:%s#%d (same pending or failed measure: %s)"
                                      % (first.doc_id, first.index, r["citation"]))
                first.rec["provenance"].setdefault("also_seen_in", []).append(c.doc_id)
                res.duplicates.append({"doc_id": c.doc_id, "index": c.index,
                                       "duplicate_of": "%s#%d" % (first.doc_id, first.index)})
            else:
                seen[key] = c
    live = [c for c in live if c.outcome != "dropped"]

    # 2. hard invariants
    for c in list(live):
        r = c.rec
        for f in forbidden:
            if r["jurisdiction"] == f["jurisdiction"] and r["category"] == f["category"]:
                c.outcome = "dropped"
                msg = "invariant_violation: %s must emit zero %s rules (%s)" % (
                    f["jurisdiction"], f["category"], f.get("why", ""))
                c.drop_reasons.append(msg)
                res.violations.append({"doc_id": c.doc_id, "index": c.index, "message": msg})
                live.remove(c)
                break

    # 3. schema validation (before ids, so a failure leaves no gap)
    for c in list(live):
        errs = validate_record(c.rec, full_v, relaxed_v)
        if errs:
            c.outcome = "dropped"
            c.drop_reasons.append("schema_invalid: " + "; ".join(errs[:5]))
            live.remove(c)

    # 4. ids
    counters = {}
    for c in live:
        r = c.rec
        series = "P" if r["instrument_type"] in PENDING_SERIES_TYPES else "N"
        key = (juris_code(r["jurisdiction"]), CAT_CODES[r["category"]], series)
        counters[key] = counters.get(key, 0) + 1
        n = counters[key]
        r["team_rule_id"] = "%s-%s-P%d" % (key[0], key[1], n) if series == "P" else \
            "%s-%s-%02d" % (key[0], key[1], n)
        c.final_id = r["team_rule_id"]
        c.outcome = "withheld" if r["status"] is None else "kept"

    # 5. precedence relations
    for c in live:
        r = c.rec
        resolved, unresolved = [], []
        for rel in r["relations_declared"]:
            targets = [t for t in live if t is not c
                       and t.rec["jurisdiction"].casefold() == rel["target_jurisdiction"].casefold()
                       and t.rec["category"] == rel["target_category"]]
            if not targets:
                unresolved.append(dict(rel, reason="no_matching_rule_in_output"))
                continue
            for t in targets:
                if t.rec["status"] is None:
                    unresolved.append(dict(rel, target_id=t.final_id, reason="target_is_a_withheld_projection"))
                else:
                    resolved.append(dict(rel, target_id=t.final_id))
        chosen = resolved[0]["interaction"] if resolved else None
        use = [x for x in resolved if x["interaction"] == chosen]
        for x in resolved:
            if x["interaction"] != chosen:
                unresolved.append(dict(x, reason="mixed_interaction_not_representable_in_one_record"))
        r["relations_unresolved"] = unresolved
        if use:
            r["overrides"] = sorted({x["target_id"] for x in use})
            r["interaction"] = chosen
            cat = weakest([x["category"] for x in use])
            basis = "; ".join(sorted({x["basis"] for x in use if x["basis"]})) or "Relation declared by the extraction."
            for f in ("overrides", "interaction"):
                r["epistemics"][f] = {"category": cat, "basis": basis, "derivation_rule": None,
                                      "span_ids": sorted({s for x in use for s in x["span_ids"]})}
        else:
            basis = ("No precedence relationship was extracted from this document; cross-document "
                     "precedence is not established by single-document extraction.")
            if unresolved:
                basis = "A relation was declared but could not be resolved to a rule in the output (see relations_unresolved)."
            for f in ("overrides", "interaction"):
                r["epistemics"][f] = {"category": "unknown", "basis": basis, "derivation_rule": None,
                                      "span_ids": []}
        res.relation_log.append({"team_rule_id": c.final_id, "resolved": resolved, "unresolved": unresolved})

    for c in live:
        (res.withheld if c.outcome == "withheld" else res.rules).append(c.rec)
    return res
