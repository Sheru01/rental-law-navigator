"""Run T1-T5 from dev/change_tests.json against the actual engine outputs.

Confirmed affected sets contain only source-supported, determinate outcomes.
Unknown and absent records are reported separately, never filled from a test's
expected_behavior prose.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path[0] = str(ROOT)

from navigator.changes import diff as generic_diff  # noqa: E402
from navigator.engine import coverage, lookup, run as engine_run, withheld as withheld_engine  # noqa: E402
from navigator.extract.assemble import CITY_CODES  # noqa: E402

DEFAULT_TESTS = ROOT / "dev" / "change_tests.json"
DEFAULT_OUT = ROOT / "navigator" / "out" / "changes.json"


def _entry(snapshot, aid, rid):
    return next((e for e in snapshot.get(aid, []) if e["team_rule_id"] == rid), None)


def _city(address, expected):
    juris = address.get("jurisdiction")
    return bool(juris and coverage._norm(expected) in coverage.place_keys(juris))


def _scope(addresses, states):
    return [aid for aid, a in sorted(addresses.items()) if a.get("state") in states]


def observe(address, rule, entry):
    """One rule at one address: distinguish false coverage from missing evidence."""
    if rule is None:
        return {"result": "could_not_determine", "reason": "rule record is missing"}
    if entry is not None:
        return {"result": entry["result"], "reason": entry["explanation"],
                "temporal_result": entry.get("temporal_result")}
    if rule.get("status") == "failed":
        return {"result": "does_not_cover", "reason": "failed proposal never operates",
                "noncoverage_basis": "failed_proposal"}
    if lookup.guard_ma_rent_control(rule, address):
        return {"result": "does_not_cover", "reason": lookup.MA_RENT_CONTROL_GUARD,
                "noncoverage_basis": "ma_rent_control_guard"}
    cov = coverage.evaluate_rule(rule, lookup.normalize_address(address))
    if cov.value == coverage.FALSE:
        return {"result": "does_not_cover", "reason": "; ".join(cov.unmet),
                "noncoverage_basis": "coverage_false"}
    return {"result": "could_not_determine",
            "reason": "the rule was not returned despite non-false coverage; inspect engine output"}


def diff(before, after):
    """Translate the generic classifier into the legacy scenario labels."""
    b, a = before["result"], after["result"]
    if b == "unknown" and a == "unknown" and \
            before.get("temporal_result") == "not_yet_effective" and \
            after.get("temporal_result") == "date_reached":
        return "date_boundary_only"
    change = generic_diff.classify(dict(before, result_state=b), dict(after, result_state=a))
    if change == "remains_unresolved":
        return "could_not_determine"
    if change == "unchanged":
        return "unchanged"
    if a == "applies" and change in ("became_effective", "added", "changed_coverage"):
        return "newly_applies"
    if b == "applies" and change in ("removed", "changed_coverage",
                                     "changed_precedence"):
        return "stops_applying"
    return "changed_without_application"


def _trace(rid, inventory, snapshots):
    rule = inventory.get(rid)
    if rule is None:
        return {"record_present": False, "source_doc_id": None,
                "reason": "No extracted or withheld record supplies this rule id."}
    return {
        "record_present": True,
        "source_doc_id": rule.get("source_doc_id"),
        "source_url": rule.get("source_url"),
        "status": rule.get("status"),
        "effective_date": rule.get("effective_date"),
        "status_basis": rule.get("status_basis"),
        "rental_housing_applicability": (rule.get("epistemics") or {}).get(
            "rental_housing_applicability", {}).get("category"),
        "projection_withheld_reason": rule.get("projection_withheld_reason"),
        "provenance_grade": rule.get("provenance_grade"),
        "by_date": {date: dict(Counter(
            e["result"] for entries in snap.values() for e in entries
            if e["team_rule_id"] == rid)) for date, snap in sorted(snapshots.items())},
    }


def _base(test, inventory, snapshots):
    ids = list(dict.fromkeys(test["rule_ids"] + test.get("conflict_with", [])))
    return {
        "affected_address_ids": [],
        "conflict_flag_address_ids": [],
        "possible_affected_address_ids": [],
        "unresolved_address_ids": [],
        "unexpected_address_ids": [],
        "unexpected_rule_ids": [],
        "status": "not_established",
        "notes": "",
        "missing_rule_ids": [rid for rid in ids if rid not in inventory],
        "rule_traces": {rid: _trace(rid, inventory, snapshots) for rid in ids},
    }


def _finish(result, notes):
    for key in ("affected_address_ids", "conflict_flag_address_ids",
                "possible_affected_address_ids", "unresolved_address_ids", "unexpected_address_ids"):
        result[key] = sorted(set(result[key]))
    result["notes"] = notes
    if result["unexpected_address_ids"] or result["unexpected_rule_ids"]:
        result["status"] = "failed"
    elif not result["missing_rule_ids"] and not result["unresolved_address_ids"]:
        result["status"] = "confirmed"
    return result


def _as_of_change(test, addresses, inventory, snapshots):
    before_date, after_date = test["as_of_before"], test["as_of_after"]
    before, after = snapshots[before_date], snapshots[after_date]
    result = _base(test, inventory, snapshots)
    for aid in _scope(addresses, test["states"]):
        observations = []
        for rid in test["rule_ids"]:
            rule = inventory.get(rid)
            b = observe(addresses[aid], rule, _entry(before, aid, rid))
            a = observe(addresses[aid], rule, _entry(after, aid, rid))
            observations.append((b, a, diff(b, a)))
        if any(change == "newly_applies" for _, _, change in observations):
            result["affected_address_ids"].append(aid)
        elif any(change == "date_boundary_only" for _, _, change in observations):
            result["possible_affected_address_ids"].append(aid)
            result["unresolved_address_ids"].append(aid)
        elif any(b["result"] == "applies" for b, _, _ in observations):
            result["unexpected_address_ids"].append(aid)
        else:
            result["unresolved_address_ids"].append(aid)
        if test.get("conflict_with"):
            if addresses[aid].get("jurisdiction") is None:
                result["unresolved_address_ids"].append(aid)
            state_entry = _entry(before, aid, test["rule_ids"][0])
            in_conflict_city = False
            for local_id in test["conflict_with"]:
                city = _expected_city(local_id, inventory, addresses)
                if city and _city(addresses[aid], city):
                    in_conflict_city = True
                    local_entry = _entry(before, aid, local_id)
                    if state_entry and local_entry and state_entry["conflict_flag"] and \
                            local_entry["conflict_flag"]:
                        result["conflict_flag_address_ids"].append(aid)
                    else:
                        result["unresolved_address_ids"].append(aid)
            if not in_conflict_city and state_entry and state_entry["conflict_flag"]:
                result["unexpected_address_ids"].append(aid)
    note = ("The same deterministic observation diff is used for every as-of scenario. "
            "Confirmed affected addresses require a transition into applies. A date "
            "derived for a withheld rule identifies only a possible transition; it does "
            "not establish rental applicability. Conflict flags require the local record "
            "at the same address. A test title supplies no missing source text.")
    return _finish(result, note)


def _expected_city(rid, inventory, addresses):
    """Check a city-rule id against the project's generic jurisdiction code map."""
    prefix = rid.split("-", 1)[0]
    city = next((name for name, code in CITY_CODES.items() if code == prefix), None)
    rule = inventory.get(rid)
    if city is None and rule and rule.get("level") == "city":
        city = str(rule.get("jurisdiction", "")).rsplit(",", 1)[0].strip()
    if not city:
        return None
    jurisdiction = str(rule.get("jurisdiction", "")) if rule else ""
    if "," in jurisdiction:
        state = jurisdiction.rsplit(",", 1)[1].strip()
    elif len(jurisdiction) == 2:
        state = jurisdiction
    else:
        seen_states = {a.get("state") for a in addresses.values()
                       if a.get("jurisdiction") and
                       str(a["jurisdiction"]).rsplit(",", 1)[0].casefold() == city.casefold()}
        state = next(iter(seen_states)) if len(seen_states) == 1 else None
    return "%s, %s" % (city.title(), state) if state else None


def _boundary(test, addresses, inventory, snapshots):
    snap = snapshots[test["as_of"]]
    result = _base(test, inventory, snapshots)
    target = {rid: _expected_city(rid, inventory, addresses) for rid in test["rule_ids"]}
    for rid, city in target.items():
        rule = inventory.get(rid)
        if rule and (rule.get("level") != "city" or rule.get("jurisdiction") != city):
            result["unexpected_rule_ids"].append(rid)
    states = {city.rsplit(",", 1)[1].strip() for city in target.values() if city}
    for aid in _scope(addresses, states):
        address = addresses[aid]
        if address.get("jurisdiction") is None:
            result["unresolved_address_ids"].append(aid)
            continue
        for rid, city in target.items():
            e = _entry(snap, aid, rid)
            if city and _city(address, city):
                if e and e["result"] == "applies":
                    result["affected_address_ids"].append(aid)
                else:
                    result["unresolved_address_ids"].append(aid)
            elif e and e["result"] == "applies":
                result["unexpected_address_ids"].append(aid)
    return _finish(result, "Only a local rule applying in its own legal city is confirmed. "
                   "Missing ordinance text or unresolved legal geography stays unresolved; "
                   "postal city is never substituted.")


def _pending(test, addresses, inventory, snapshots):
    snap = snapshots[test["as_of"]]
    result = _base(test, inventory, snapshots)
    for aid in _scope(addresses, test["states"]):
        entries = [_entry(snap, aid, rid) for rid in test["rule_ids"]]
        if all(e and e["result"] == "pending" for e in entries):
            result["affected_address_ids"].append(aid)
        elif any(e and e["result"] == "applies" for e in entries):
            result["unexpected_address_ids"].append(aid)
        else:
            result["unresolved_address_ids"].append(aid)
    return _finish(result, "Affected means potentially covered if enacted as written; "
                   "each current result remains pending, never in force.")


def _negative(test, addresses, inventory, snapshots):
    snap = snapshots[test["as_of"]]
    result = _base(test, inventory, snapshots)
    for aid in _scope(addresses, test["states"]):
        if any(e["team_rule_id"] in test["rule_ids"] or
               (e["team_rule_id"] in inventory and
                inventory[e["team_rule_id"]].get("category") == "rent_increase_limits")
               for e in snap[aid]):
            result["unexpected_address_ids"].append(aid)
    result["unexpected_rule_ids"] = sorted(
        rid for rid, rule in inventory.items()
        if rule.get("category") == "rent_increase_limits"
        and rule.get("status") in ("in_force", "not_yet_effective")
        and (rule.get("jurisdiction") == "MA" or
             str(rule.get("jurisdiction", "")).endswith(", MA")))
    supported = all(inventory.get(rid, {}).get("status") == "failed" and
                    inventory[rid].get("provenance_grade") == "source_text"
                    for rid in test["rule_ids"])
    if not supported:
        result["unresolved_address_ids"] = _scope(addresses, test["states"])
    return _finish(result, "The affected set is empty only as an observed engine result. "
                   "A confirmed failed-measure conclusion additionally requires a failed "
                   "record backed by source text; the Massachusetts rent-control guard is separate.")


HANDLERS = {"as_of": _as_of_change, "boundary": _boundary,
            "pending": _pending, "negative": _negative}


def run_changes(addresses, rules, withheld, tests):
    """Return the five change records. Raises on malformed scenario definitions."""
    established, uncertain = withheld_engine.partition(rules, withheld)
    inventory = {r["team_rule_id"]: r for r in established + uncertain}
    ids = [t.get("test_id") for t in tests]
    if len(ids) != len(set(ids)) or set(ids) != {"T1", "T2", "T3", "T4", "T5"}:
        raise ValueError("change tests must contain T1-T5 exactly once")
    if any(t.get("type") not in HANDLERS for t in tests):
        raise ValueError("change test has an unsupported type")
    dates = sorted({t[key] for t in tests for key in ("as_of", "as_of_before", "as_of_after") if key in t})
    snapshots = {date: engine_run.run_lookups(addresses, established, date, uncertain)[0]
                 for date in dates}
    return {t["test_id"]: HANDLERS[t["type"]](t, addresses, inventory, snapshots) for t in tests}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Deterministic T1-T5 change tracking")
    ap.add_argument("--rules", default=str(engine_run.DEFAULT_RULES))
    ap.add_argument("--withheld", default=None)
    ap.add_argument("--jurisdictions", default=str(engine_run.DEFAULT_JURIS))
    ap.add_argument("--addresses", default=str(engine_run.DEFAULT_ADDRESSES))
    ap.add_argument("--tests", default=str(DEFAULT_TESTS))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    try:
        rules = engine_run.load_rules(args.rules)
        withheld_path = Path(args.withheld) if args.withheld else Path(args.rules).with_name("rules_withheld.json")
        withheld = engine_run.load_withheld(withheld_path)
        juris = engine_run.load_jurisdictions(args.jurisdictions)
        sample = engine_run.load_sample_addresses(args.addresses)
        if sample is None:
            raise engine_run.InputProblem("missing input %s" % args.addresses)
        addresses = engine_run.build_addresses(juris, sample)
        tests = engine_run._read_json(args.tests, "dev/change_tests.json")
        if not isinstance(tests, list) or not all(isinstance(t, dict) for t in tests):
            raise ValueError("change tests must be a list of objects")
        output = run_changes(addresses, rules, withheld, tests)
    except (engine_run.InputProblem, coverage.RuleInputError, ValueError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    engine_run.write_json_atomic(args.out, output)
    for test_id, rec in output.items():
        print("%s: %s; affected=%d possible=%d unresolved=%d unexpected=%d" % (
            test_id, rec["status"], len(rec["affected_address_ids"]),
            len(rec["possible_affected_address_ids"]), len(rec["unresolved_address_ids"]),
            len(rec["unexpected_address_ids"])))
    return 0 if all(r["status"] == "confirmed" for r in output.values()) else 3


if __name__ == "__main__":
    sys.exit(main())
