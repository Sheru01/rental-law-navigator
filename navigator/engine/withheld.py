"""Evaluate source-backed projections whose operative status was withheld.

They remain unknown even when a deterministic date rule can establish a date
boundary. A date boundary does not establish rental-housing applicability.
"""
import datetime
import re

from navigator.engine import coverage
from navigator.normalize.effective_date import derive_effective_date

_APPROVED = re.compile(
    r"Approved\s+by\s+Governor\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),\s*(\d{4})", re.IGNORECASE)


def partition(rules, withheld=()):
    """Return (established, withheld), accepting null-status records in either input."""
    established, uncertain = [], []
    seen = set()
    for rule, supplied_as_withheld in ([(r, False) for r in rules] + [(r, True) for r in withheld]):
        rid = rule.get("team_rule_id") if isinstance(rule, dict) else None
        if not isinstance(rid, str) or not rid:
            raise coverage.RuleInputError("a rule record has no team_rule_id")
        if rid in seen:
            raise coverage.RuleInputError("duplicate team_rule_id %s across rules and withheld" % rid)
        seen.add(rid)
        if rule.get("status") is None:
            uncertain.append(rule)
        elif supplied_as_withheld:
            raise coverage.RuleInputError("withheld record %s has substantive status %r" % (rid, rule["status"]))
        else:
            established.append(rule)
    return established, uncertain


def _source_date(rule):
    """Return an approval date only when verified source spans establish the inputs."""
    if (rule.get("jurisdiction") != "CA" or rule.get("level") != "state"
            or rule.get("instrument_type") != "statute"
            or rule.get("source_kind") != "primary_legal_text"
            or rule.get("provenance_grade") != "source_text"
            or rule.get("effective_date") not in (None, "")):
        return None
    spans = [s.get("text", "") for s in rule.get("supporting_spans", [])
             if isinstance(s, dict) and s.get("verified") is True]
    if not any(re.search(r"\bCHAPTER\s+\d+\b", text, re.IGNORECASE) for text in spans):
        return None
    dates = set()
    for text in spans:
        for match in _APPROVED.finditer(text):
            try:
                dates.add(datetime.datetime.strptime(
                    " ".join(match.groups()), "%B %d %Y").date().isoformat())
            except ValueError:
                return None
    return next(iter(dates)) if len(dates) == 1 else None


def temporal_evidence(rule, as_of):
    """Return date evidence without converting a withheld rule into operative law."""
    enacted = _source_date(rule)
    if enacted is None:
        return "not_established", None
    date, derivation = derive_effective_date(
        enacted_on=enacted, stated_effective_date=None, jurisdiction="CA",
        instrument="chaptered_statute")
    if date is None:
        return "not_established", derivation
    boundary = coverage.parse_effective_date(date)[0]
    return ("not_yet_effective" if coverage.parse_as_of(as_of) < boundary else "date_reached"), derivation


def needed_facts(rule, address):
    """List unresolved coverage inputs without treating missing facts as false."""
    needed = list(coverage.needs_facts(rule))
    predicates = coverage.scope_predicates(rule) + coverage.explicit_predicates(rule)
    for pred in predicates:
        verdict = coverage.evaluate_with_reason(pred, address)
        if verdict.value != coverage.UNKNOWN:
            continue
        fact = "certificate_of_occupancy_date" if verdict.kind == "co_year" else verdict.fact
        if verdict.kind == "bad_predicate":
            fact = "coverage_predicate_review"
        if fact and fact not in needed:
            needed.append(fact)
    return needed


def evaluate(rule, address, as_of):
    """Return (entry, omission_reason). False geographic coverage is traced."""
    cov = coverage.evaluate_rule(rule, address)
    if cov.value == coverage.FALSE:
        return None, "withheld coverage false: " + "; ".join(cov.unmet)
    temporal, derivation = temporal_evidence(rule, as_of)
    reason = (rule.get("projection_withheld_reason") or
              (rule.get("epistemics") or {}).get("status", {}).get("basis") or
              "operative status is not established by the supplied evidence")
    parts = ["Unknown: operative status of %s is not established: %s." % (
        rule.get("title") or rule["team_rule_id"], str(reason).rstrip("."))]
    if temporal == "not_yet_effective":
        parts.append("A separate deterministic date derivation places its date after %s; "
                     "this does not establish rental-housing applicability." % coverage.parse_as_of(as_of))
    elif temporal == "date_reached":
        parts.append("A separate deterministic date derivation places its date on or before %s; "
                     "this does not establish rental-housing applicability." % coverage.parse_as_of(as_of))
    if cov.value == coverage.UNKNOWN:
        parts.append("Coverage also depends on %s." % "; ".join(cov.unknown))
    needs = needed_facts(rule, address)
    if needs:
        parts.append("Needs fact: %s." % ", ".join(needs))
    flag = bool(rule.get("conflict_flag")) and rule.get("interaction") != "preempts_pending"
    if flag and rule.get("conflict_note"):
        parts.append("Conflict flagged for review: %s" % str(rule["conflict_note"]).rstrip("."))
    entry = {
        "team_rule_id": rule["team_rule_id"],
        "result": "unknown",
        "explanation": " ".join(parts),
        "conflict_flag": flag,
        "evidence_status": "not_established",
        "reason_code": "projection_withheld",
        "needs_fact": needs,
        "temporal_result": temporal,
        "date_derivation": derivation,
        "result_state": "indeterminate",
        "reason": str(reason),
        "as_of": coverage.parse_as_of(as_of).isoformat(),
        "source_doc_id": rule.get("source_doc_id"),
        "source_url": rule.get("source_url"),
        "citation": rule.get("citation"),
        "evidence_basis": {
            "status_basis": rule.get("status_basis"),
            "provenance_grade": rule.get("provenance_grade"),
            "source_kind": rule.get("source_kind"),
        },
    }
    return entry, None
