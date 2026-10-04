"""Evidence-aware comparison of one address at two explicit dates.

Missing records are not evidence that a law was absent. Callers may supply an
explicit, nonempty absence basis for a side when authoritative evidence does
establish absence. This module contains no challenge-test identifiers or clock
access.
"""
from navigator.engine import coverage, lookup, withheld as withheld_engine


def _absence_basis(value, rule_id):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("documented absence for %s needs a nonempty evidence basis" % rule_id)
    return value.strip()


def _observations(address, rules, withheld, as_of, documented_absent):
    established, uncertain = withheld_engine.partition(rules, withheld)
    entries, omissions = lookup.resolve_with_trace(address, established, as_of,
                                                   withheld=uncertain)
    present = {r["team_rule_id"]: r for r in established + uncertain}
    by_id = {e["team_rule_id"]: e for e in entries}
    omitted = {o["team_rule_id"]: o["reason"] for o in omissions}
    return present, by_id, omitted, documented_absent


def _evidence(rule):
    if rule is None:
        return None
    return {
        "status_basis": rule.get("status_basis"),
        "provenance_grade": rule.get("provenance_grade"),
        "source_kind": rule.get("source_kind"),
        "quoted_span_kind": rule.get("quoted_span_kind"),
    }


def _one(address, rule_id, snapshot, as_of):
    present, entries, omitted, absent = snapshot
    rule = present.get(rule_id)
    entry = entries.get(rule_id)
    base = {
        "rule_id": rule_id,
        "source_doc_id": rule.get("source_doc_id") if rule else None,
        "source_url": rule.get("source_url") if rule else None,
        "citation": rule.get("citation") if rule else None,
        "as_of": as_of,
        "needs_fact": [],
        "needs_evidence": [],
        "evidence_basis": _evidence(rule),
    }
    if rule is None:
        basis = _absence_basis(absent.get(rule_id), rule_id)
        if basis:
            return dict(base, result_state="not_present", reason=basis)
        return dict(base, result_state="could_not_determine",
                    reason="No rule record or documented absence establishes this side.",
                    needs_evidence=["rule_record_or_absence_evidence"])
    if entry is None:
        reason = omitted.get(rule_id, "The engine returned no result despite a rule record.")
        if rule.get("status") == "failed":
            return dict(base, result_state="does_not_cover",
                        reason="Failed proposal is not operative: " + reason,
                        noncoverage_basis="failed_proposal")
        if lookup.guard_ma_rent_control(rule, address):
            return dict(base, result_state="does_not_cover", reason=reason,
                        noncoverage_basis="ma_rent_control_guard")
        cov = coverage.evaluate_rule(rule, lookup.normalize_address(address))
        if cov.value == coverage.FALSE:
            return dict(base, result_state="does_not_cover", reason=reason,
                        noncoverage_basis="coverage_false")
        return dict(base, result_state="could_not_determine", reason=reason,
                    needs_fact=withheld_engine.needed_facts(rule, lookup.normalize_address(address)))

    state = entry["result"]
    needs = list(entry.get("needs_fact") or [])
    if state == "unknown":
        if entry.get("evidence_status") == "not_established" or \
                "source-established effective date" in entry["explanation"] or \
                "effective date recorded only as" in entry["explanation"]:
            state = "indeterminate"
        if not needs:
            needs = withheld_engine.needed_facts(rule, lookup.normalize_address(address))
    missing_evidence = []
    if state == "indeterminate":
        missing_evidence.append("operative_status_or_date")
    return dict(base, result_state=state, reason=entry["explanation"], needs_fact=needs,
                needs_evidence=missing_evidence,
                temporal_result=entry.get("temporal_result"),
                date_derivation=entry.get("date_derivation"))


def classify(prior, current):
    """Classify two observations without test-specific expectations."""
    before, after = prior["result_state"], current["result_state"]
    uncertain = {"unknown", "indeterminate", "could_not_determine"}
    if before in uncertain or after in uncertain:
        return "remains_unresolved"
    if before == "not_present" and after == "applies":
        return "added"
    if before == "applies" and after == "not_present":
        return "removed"
    if before == "not_yet_effective" and after == "applies":
        return "became_effective"
    if before == "applies" and after == "not_yet_effective":
        # A later recorded start date may be a correction or conflicting
        # evidence; it does not establish that an operative rule terminated.
        return "remains_unresolved"
    if before == "pending" and after == "applies":
        return "became_effective"
    if before == "does_not_cover" and after == "applies" and \
            prior.get("noncoverage_basis") == "coverage_false" or \
            before == "applies" and after == "does_not_cover" and \
            current.get("noncoverage_basis") == "coverage_false":
        return "changed_coverage"
    if before == "does_not_cover" and after == "applies" or \
            before == "applies" and after == "does_not_cover":
        return "remains_unresolved"
    if before == "superseded" and after == "applies" or \
            before == "applies" and after == "superseded":
        return "changed_precedence"
    if before == after:
        return "unchanged"
    return "changed_without_established_application"


def compare_address(address, prior_rules, current_rules, prior_as_of, current_as_of,
                    prior_withheld=(), current_withheld=(),
                    documented_absent_before=None, documented_absent_after=None):
    """Return one structured change record per rule ID seen on either side.

    `documented_absent_*` maps rule IDs to an evidence basis; plain omission is
    always `could_not_determine`, not an added or removed rule.
    """
    prior_date = coverage.parse_as_of(prior_as_of).isoformat()
    current_date = coverage.parse_as_of(current_as_of).isoformat()
    if prior_date > current_date:
        raise ValueError("prior_as_of must be on or before current_as_of")
    before_absent = documented_absent_before or {}
    after_absent = documented_absent_after or {}
    if not isinstance(before_absent, dict) or not isinstance(after_absent, dict):
        raise ValueError("documented absence must be a mapping from rule ID to evidence basis")
    before = _observations(address, prior_rules, prior_withheld, prior_date, before_absent)
    after = _observations(address, current_rules, current_withheld, current_date, after_absent)
    if set(before[0]) & set(before_absent) or set(after[0]) & set(after_absent):
        raise ValueError("a rule cannot be both recorded and documented absent on one side")
    ids = sorted(set(before[0]) | set(after[0]) | set(before_absent) | set(after_absent))
    output = []
    for rule_id in ids:
        prior = _one(address, rule_id, before, prior_date)
        current = _one(address, rule_id, after, current_date)
        change = classify(prior, current)
        output.append({
            "rule_id": rule_id,
            "source_doc_id": current["source_doc_id"] or prior["source_doc_id"],
            "source_url": current["source_url"] or prior["source_url"],
            "citation": current["citation"] or prior["citation"],
            "result_state": change,
            "reason": "%s: %s → %s." % (change.replace("_", " "),
                                       prior["result_state"], current["result_state"]),
            "needs_fact": sorted(set(prior["needs_fact"] + current["needs_fact"])),
            "needs_evidence": sorted(set(prior["needs_evidence"] + current["needs_evidence"])),
            "prior_state": prior["result_state"],
            "current_state": current["result_state"],
            "prior_as_of": prior_date,
            "as_of": current_date,
            "evidence_basis": {"prior": prior["evidence_basis"],
                               "current": current["evidence_basis"]},
            "prior": prior,
            "current": current,
        })
    return output
