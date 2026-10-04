"""Run the deterministic rule engine over every address.

    python3 navigator/engine/run.py --as-of 2026-10-01

Reads  navigator/out/rules.json and navigator/out/jurisdictions.json
       (plus data/sample_addresses.csv for year_built and units)
Writes navigator/out/lookups.json

Input contracts (what this script expects from the other workers):

rules.json
    A list of rule records (schema/rule_record.schema.json), or {"rules": [...]}.

jurisdictions.json
    One row per address, as a list, or {"addresses": [...]}, or an object keyed
    by address_id. Each row: {"address_id", "state", "jurisdiction"} where
    jurisdiction is the legal incorporated place written exactly like a rule
    jurisdiction, 'City, ST' (e.g. 'Jersey City, NJ'), or null when the geocoder
    could not resolve it. year_built and units may also be present; if absent
    they come from the sample addresses CSV.
"""
import argparse
import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):          # started as a script: make `navigator` importable
    sys.path[0] = str(ROOT)

from navigator.engine import coverage, lookup, precedence, withheld as withheld_engine  # noqa: E402

DEFAULT_RULES = ROOT / "navigator" / "out" / "rules.json"
DEFAULT_WITHHELD = ROOT / "navigator" / "out" / "rules_withheld.json"
DEFAULT_JURIS = ROOT / "navigator" / "out" / "jurisdictions.json"
DEFAULT_ADDRESSES = ROOT / "data" / "sample_addresses.csv"
DEFAULT_OUT = ROOT / "navigator" / "out" / "lookups.json"
EXPECTED_ADDRESS_COUNT = 500


class InputProblem(Exception):
    """A missing or unreadable input. Reported plainly, never as a traceback."""


def _show(path):
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _read_json(path, producer):
    path = Path(path)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise InputProblem("missing input %s (produced by %s). Generate it first, then re-run."
                           % (_show(path), producer))
    except (OSError, UnicodeError) as exc:
        raise InputProblem("could not read input %s: %s" % (_show(path), exc))
    except ValueError as exc:
        raise InputProblem("%s is not valid JSON: %s" % (_show(path), exc))


def load_rules(path):
    data = _read_json(path, "the extraction worker, navigator/extract")
    if isinstance(data, dict) and "rules" in data:
        data = data["rules"]
    if not isinstance(data, list):
        raise InputProblem("%s must be a list of rule records or {\"rules\": [...]}" % _show(path))
    if not all(isinstance(r, dict) for r in data):
        raise InputProblem("%s contains an entry that is not a rule object" % _show(path))
    return data


def load_withheld(path):
    data = _read_json(path, "the extraction worker, navigator/extract")
    if isinstance(data, dict) and "withheld_projections" in data:
        data = data["withheld_projections"]
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise InputProblem("%s must contain a list of withheld projections" % _show(path))
    if any(r.get("status") is not None for r in data):
        raise InputProblem("%s contains a projection with a substantive status" % _show(path))
    return data


def _juris_rows(data, where):
    if isinstance(data, dict):
        for key in ("addresses", "jurisdictions", "results"):
            if key in data:
                data = data[key]
                break
    if isinstance(data, dict):
        rows = []
        for aid, row in data.items():
            if not isinstance(row, dict):
                raise InputProblem("%s: entry for %s is not an object" % (where, aid))
            row = dict(row)
            row.setdefault("address_id", aid)
            rows.append(row)
        return rows
    if isinstance(data, list) and all(isinstance(r, dict) for r in data):
        return data
    raise InputProblem("%s must be a list of rows, {\"addresses\": [...]} or an object keyed by address_id"
                       % where)


def load_jurisdictions(path):
    data = _read_json(path, "the geocoding worker, navigator/geocode")
    rows = _juris_rows(data, _show(path))
    out = {}
    for row in rows:
        aid = row.get("address_id")
        if not aid:
            raise InputProblem("%s has a row with no address_id" % _show(path))
        out[str(aid)] = row
    return out


def load_sample_addresses(path):
    path = Path(path)
    if not path.is_file():
        return None
    with open(path, newline="", encoding="utf-8") as fh:
        return {row["address_id"]: row for row in csv.DictReader(fh)}


def build_addresses(juris, sample):
    """Merge jurisdiction rows over the assessor rows. Returns {address_id: address dict}."""
    sample = sample or {}
    addresses = {}
    for aid in sorted(set(juris) | set(sample)):
        base = sample.get(aid, {})
        j = juris.get(aid, {})
        row = {"address_id": aid}
        for fact in ("state", "year_built", "units"):
            v = j.get(fact)
            row[fact] = v if v not in (None, "") else base.get(fact)
        jur = j.get("jurisdiction")
        row["jurisdiction"] = jur if jur not in (None, "") else None
        addresses[aid] = row
    return addresses


def run_lookups(addresses, rules, as_of, withheld=()):
    """Return (lookups, stats). as_of is required."""
    as_of_d = coverage.parse_as_of(as_of)
    established, withheld_rules = withheld_engine.partition(rules, withheld)
    plan = precedence.build_plan(established)
    lookups = {}
    results = Counter()
    omissions = Counter()
    flagged = 0
    for aid in sorted(addresses):
        entries, omitted = lookup.resolve_with_trace(addresses[aid], established, as_of_d,
                                                     plan, withheld_rules)
        lookups[aid] = entries
        for e in entries:
            results[e["result"]] += 1
            flagged += 1 if e["conflict_flag"] else 0
        for o in omitted:
            omissions[o["reason"].split(":", 1)[0]] += 1
    return lookups, {"results": results, "omissions": omissions, "flagged_entries": flagged,
                     "withheld_rule_ids": [r["team_rule_id"] for r in withheld_rules],
                     "warnings": list(plan.warnings)}


def write_json_atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=False, ensure_ascii=True)
        fh.write("\n")
    os.replace(str(tmp), str(path))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Deterministic rule engine: writes lookups.json")
    ap.add_argument("--as-of", required=True, help="query date, YYYY-MM-DD (required, no default)")
    ap.add_argument("--rules", default=str(DEFAULT_RULES))
    ap.add_argument("--withheld", default=None,
                    help="required withheld projections; defaults to rules_withheld.json beside --rules")
    ap.add_argument("--jurisdictions", default=str(DEFAULT_JURIS))
    ap.add_argument("--addresses", default=str(DEFAULT_ADDRESSES),
                    help="sample addresses CSV, source of year_built and units")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)

    try:
        as_of = coverage.parse_as_of(args.as_of)
        rules = load_rules(args.rules)
        withheld_path = Path(args.withheld) if args.withheld else Path(args.rules).with_name("rules_withheld.json")
        withheld_rules = load_withheld(withheld_path)
        juris = load_jurisdictions(args.jurisdictions)
        sample = load_sample_addresses(args.addresses)
        if sample is None:
            print("WARNING: %s not found; year_built and units will be unknown unless the "
                  "jurisdictions file carries them" % _show(args.addresses), file=sys.stderr)
        addresses = build_addresses(juris, sample)
        lookups, stats = run_lookups(addresses, rules, as_of, withheld_rules)
    except InputProblem as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    except (coverage.RuleInputError, precedence.PrecedenceCycleError, ValueError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    except lookup.InvariantError as exc:
        print("INVARIANT VIOLATION (engine bug, nothing written): %s" % exc, file=sys.stderr)
        return 3

    inventory = {r["team_rule_id"]: r for r in rules + withheld_rules}
    lookup_details = {
        aid: [lookup.detail(addresses[aid], inventory[e["team_rule_id"]], e, as_of)
              for e in entries]
        for aid, entries in lookups.items()
    }
    write_json_atomic(args.out, {"as_of": as_of.isoformat(), "lookups": lookups,
                                 "lookup_details": lookup_details,
                                 "withheld_rule_ids": stats["withheld_rule_ids"]})

    unresolved = sorted(a for a, r in addresses.items() if r["jurisdiction"] is None)
    known_juris = {r.get("jurisdiction") for r in rules if r.get("level") == "city"}
    known_norm = {coverage._norm(j) for j in known_juris}
    seen = Counter(r["jurisdiction"] for r in addresses.values() if r["jurisdiction"])
    unmatched = {j: n for j, n in seen.items()
                 if not (coverage.place_keys(j) & known_norm)}

    print("as_of %s: wrote %s with %d address ids and %d entries"
          % (as_of.isoformat(), _show(args.out), len(lookups), sum(stats["results"].values())))
    print("results: " + ", ".join("%s=%d" % kv for kv in sorted(stats["results"].items())))
    print("omitted (rule did not apply, failed, or guarded): "
          + ", ".join("%s=%d" % kv for kv in sorted(stats["omissions"].items())))
    print("entries with conflict_flag true: %d" % stats["flagged_entries"])
    print("withheld projections included: %d" % len(stats["withheld_rule_ids"]))
    if unresolved:
        print("WARNING: %d address(es) have no resolved jurisdiction: city rules in their state "
              "come out unknown. First few: %s" % (len(unresolved), ", ".join(unresolved[:5])),
              file=sys.stderr)
    if unmatched:
        print("NOTE: resolved jurisdictions with no city rule (check the 'City, ST' spelling "
              "matches the rules if this is unexpected): "
              + ", ".join("%s (%d)" % kv for kv in sorted(unmatched.items())), file=sys.stderr)
    for w in stats["warnings"]:
        print("WARNING: " + w, file=sys.stderr)
    if len(lookups) != EXPECTED_ADDRESS_COUNT:
        print("WARNING: lookups cover %d address ids, the submission needs exactly %d"
              % (len(lookups), EXPECTED_ADDRESS_COUNT), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
