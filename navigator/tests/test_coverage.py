"""Tests for the deterministic rule engine (navigator/engine).

Run from the project root:

    python3 -m unittest navigator.tests.test_coverage -v
    python3 -m navigator.tests.test_coverage

Everything here runs against hand-written fixtures in navigator/tests/fixtures/
and has no dependency on the extraction or geocoding workers. The last class
checks the real output files if they exist and is skipped otherwise.
"""
import contextlib
import copy
import csv
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from navigator.engine import coverage, lookup, precedence, run  # noqa: E402
from navigator.engine.coverage import FALSE, TRUE, UNKNOWN  # noqa: E402

FIX = ROOT / "navigator" / "tests" / "fixtures"
OUT = ROOT / "navigator" / "out"
AS_OF = "2026-10-01"
CO = "certificate_of_occupancy"


def load_rules():
    with open(FIX / "rules.json", encoding="utf-8") as fh:
        return json.load(fh)


def load_addresses():
    juris = run.load_jurisdictions(FIX / "jurisdictions.json")
    sample = run.load_sample_addresses(FIX / "addresses.csv")
    return run.build_addresses(juris, sample)


def by_id(entries):
    return {e["team_rule_id"]: e for e in entries}


class EngineCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_rules()
        cls.addrs = load_addresses()

    def look(self, aid, as_of=AS_OF, rules=None):
        return by_id(lookup.resolve(self.addrs[aid], rules or self.rules, as_of))

    def result(self, aid, rid, as_of=AS_OF):
        e = self.look(aid, as_of).get(rid)
        return None if e is None else e["result"]


# ====================================================== the 14 required cases

class TestRequiredCases(EngineCase):

    def test_01_missing_year_built_is_unknown_not_false(self):
        pred = {"fact": "year_built", "op": "lte", "value": 2011}
        for missing in (None, "", float("nan")):
            self.assertEqual(coverage.evaluate(pred, {"year_built": missing}), UNKNOWN)
        self.assertEqual(coverage.evaluate(pred, {}), UNKNOWN)
        # through the whole engine: San Diego has no year built
        e = self.look("F007")["CA-RENT-01"]
        self.assertEqual(e["result"], "unknown")
        self.assertIn("year built", e["explanation"])
        self.assertIn("not in the assessor record", e["explanation"])

    def test_02_missing_units_is_unknown(self):
        pred = {"fact": "units", "op": "gte", "value": 2}
        self.assertEqual(coverage.evaluate(pred, {"units": None}), UNKNOWN)
        self.assertEqual(coverage.evaluate(pred, {"units": ""}), UNKNOWN)
        e = self.look("F008")["BERK-RENT-01"]
        self.assertEqual(e["result"], "unknown")
        self.assertIn("unit count", e["explanation"])
        self.assertTrue(e["explanation"].startswith("Unknown:"))
        self.assertNotIn("Applies", e["explanation"])

    def test_03_year_built_equals_co_cutoff_year_is_unknown(self):
        pred = {"fact": "year_built", "op": "lte", "value": "1979-06-13", "basis": CO}
        self.assertEqual(coverage.evaluate(pred, {"year_built": 1979}), UNKNOWN)
        self.assertEqual(coverage.evaluate(pred, {"year_built": "1979"}), UNKNOWN)
        # the same predicate without the CO basis is a plain year comparison
        plain = {"fact": "year_built", "op": "lte", "value": "1979-06-13"}
        self.assertEqual(coverage.evaluate(plain, {"year_built": 1979}), TRUE)
        e = self.look("F002")["SF-RENT-01"]          # SF, year_built 1979
        self.assertEqual(e["result"], "unknown")
        self.assertIn("1979", e["explanation"])
        self.assertIn("certificate-of-occupancy", e["explanation"])
        # LA cutoff year is 1978
        self.assertEqual(self.result("F006", "LA-RENT-01"), "unknown")

    def test_04_year_1960_vs_1979_co_cutoff_is_true(self):
        pred = {"fact": "year_built", "op": "lte", "value": "1979-06-13", "basis": CO}
        self.assertEqual(coverage.evaluate(pred, {"year_built": 1960}), TRUE)
        self.assertEqual(coverage.evaluate(pred, {"year_built": 1980}), FALSE)
        self.assertEqual(coverage.evaluate(pred, {"year_built": 1978}), TRUE)
        self.assertEqual(self.result("F001", "SF-RENT-01"), "applies")
        self.assertIsNone(self.result("F004", "SF-RENT-01"))      # 1990: omitted

    def test_05_needs_fact_makes_unknown_even_when_all_predicates_true(self):
        rule = copy.deepcopy(next(r for r in self.rules if r["team_rule_id"] == "CA-DEP-01"))
        rule["coverage_conditions"]["predicates"] = [
            {"fact": "year_built", "op": "lte", "value": 2000}]
        addr = {"address_id": "X", "state": "CA", "jurisdiction": "Oakland, CA",
                "year_built": 1960, "units": 30}
        cov = coverage.evaluate_rule(rule, addr)
        self.assertEqual(cov.value, UNKNOWN)
        self.assertEqual(cov.unmet, [])                      # nothing was false
        e = by_id(lookup.resolve(addr, [rule], AS_OF))["CA-DEP-01"]
        self.assertEqual(e["result"], "unknown")
        self.assertIn("owner type", e["explanation"])
        # the same rule with needs_fact emptied applies
        rule["coverage_conditions"]["needs_fact"] = []
        self.assertEqual(by_id(lookup.resolve(addr, [rule], AS_OF))["CA-DEP-01"]["result"], "applies")

    def test_06_false_predicate_beats_unknown_and_rule_is_omitted(self):
        preds = [{"fact": "year_built", "op": "lte", "value": 1979},    # unknown (missing)
                 {"fact": "units", "op": "lt", "value": 5}]             # false (units=40)
        addr = {"address_id": "X", "state": "CA", "jurisdiction": "Oakland, CA",
                "year_built": None, "units": 40}
        self.assertEqual([coverage.evaluate(p, addr) for p in preds], [UNKNOWN, FALSE])
        self.assertEqual(coverage.combine([UNKNOWN, FALSE, TRUE]), FALSE)
        self.assertEqual(coverage.combine([TRUE, UNKNOWN]), UNKNOWN)
        self.assertEqual(coverage.combine([TRUE, TRUE]), TRUE)
        rule = copy.deepcopy(self.rules[0])
        rule["overrides"], rule["interaction"] = [], None
        rule["coverage_conditions"]["predicates"] = preds
        self.assertEqual(coverage.evaluate_rule(rule, addr).value, FALSE)
        self.assertEqual(lookup.resolve(addr, [rule], AS_OF), [])

    def test_07_pending_rule_is_pending_never_applies(self):
        for aid in ("F013", "F014"):
            for rid in ("MA-ALG-P1", "MA-ALG-P2"):
                e = self.look(aid)[rid]
                self.assertEqual(e["result"], "pending")
                self.assertIn("not law", e["explanation"])
        # not even with an effective date in the past
        rule = copy.deepcopy(next(r for r in self.rules if r["team_rule_id"] == "MA-ALG-P1"))
        rule["effective_date"] = "2020-01-01"
        e = by_id(lookup.resolve(self.addrs["F013"], [rule], "2030-01-01"))["MA-ALG-P1"]
        self.assertEqual(e["result"], "pending")
        # and never for a non-MA address
        self.assertIsNone(self.result("F001", "MA-ALG-P1"))

    def test_08_failed_rule_is_absent_entirely(self):
        failed = {r["team_rule_id"] for r in self.rules if r["status"] == "failed"}
        self.assertTrue({"NJ-FAIL-01", "MA-RENT-P1"} <= failed)
        for aid in self.addrs:
            for as_of in (AS_OF, "2025-12-31", "2030-01-01"):
                ids = {e["team_rule_id"] for e in lookup.resolve(self.addrs[aid], self.rules, as_of)}
                self.assertFalse(ids & failed, "failed rule leaked at %s %s" % (aid, as_of))

    def test_09_as_of_before_effective_date_is_not_yet_effective_after_applies(self):
        for aid in ("F001", "F009", "F007"):
            self.assertEqual(self.result(aid, "CA-ALG-01", "2025-12-31"), "not_yet_effective")
            self.assertEqual(self.result(aid, "CA-ALG-01", "2026-01-02"), "applies")
        self.assertEqual(self.result("F001", "CA-ALG-01", "2026-01-01"), "applies")  # boundary day
        # status field says not_yet_effective (stamped at 2026-10-01); the date decides at other as_of
        self.assertEqual(self.result("F012", "NJ-ALG-01", "2026-10-01"), "not_yet_effective")
        self.assertEqual(self.result("F012", "NJ-ALG-01", "2027-06-30"), "not_yet_effective")
        self.assertEqual(self.result("F012", "NJ-ALG-01", "2027-07-01"), "applies")
        self.assertEqual(self.result("F012", "NJ-ALG-01", "2027-07-02"), "applies")

    def test_10_yields_to_statewide_cap_superseded_where_local_applies(self):
        self.assertEqual(self.result("F001", "SF-RENT-01"), "applies")
        e = self.look("F001")["CA-RENT-01"]
        self.assertEqual(e["result"], "superseded")
        self.assertIn("SF-RENT-01", e["explanation"])
        self.assertEqual(self.result("F005", "LA-RENT-01"), "applies")
        self.assertEqual(self.result("F005", "CA-RENT-01"), "superseded")
        # stays applies where the local rule does not apply (omitted or never existed)
        self.assertIsNone(self.result("F004", "SF-RENT-01"))
        self.assertEqual(self.result("F004", "CA-RENT-01"), "applies")
        self.assertEqual(self.result("F009", "CA-RENT-01"), "applies")   # Oakland: no local rule
        # local rule unknown (CO year): the cap stays applies, with an honest caveat
        e = self.look("F002")["CA-RENT-01"]
        self.assertEqual(e["result"], "applies")
        self.assertIn("unknown whether", e["explanation"])
        self.assertIn("SF-RENT-01", e["explanation"])

    def test_11_stacks_both_state_and_local_algorithmic_rules_apply(self):
        look = self.look("F001")
        self.assertEqual(look["CA-ALG-01"]["result"], "applies")
        self.assertEqual(look["SF-ALG-01"]["result"], "applies")
        self.assertIn("Stacks with", look["CA-ALG-01"]["explanation"])
        look = self.look("F008")                                # Berkeley
        self.assertEqual(look["CA-ALG-01"]["result"], "applies")
        self.assertEqual(look["BERK-ALG-01"]["result"], "applies")

    def test_12_preempts_pending_flags_both_and_keeps_local_rule(self):
        for aid, local in (("F010", "JC-ALG-01"), ("F011", "HOB-ALG-01")):
            look = self.look(aid)
            self.assertIn(local, look, "local rule must still be present")
            self.assertEqual(look[local]["result"], "applies")
            self.assertTrue(look[local]["conflict_flag"])
            self.assertTrue(look["NJ-ALG-01"]["conflict_flag"])
            self.assertEqual(look["NJ-ALG-01"]["result"], "not_yet_effective")
            self.assertIn("preempt", look[local]["explanation"])
            self.assertIn("neither is removed", look[local]["explanation"])
        # T2 boundary: neither local ban in Newark, no conflict there
        newark = self.look("F012")
        self.assertNotIn("HOB-ALG-01", newark)
        self.assertNotIn("JC-ALG-01", newark)
        self.assertFalse(newark["NJ-ALG-01"]["conflict_flag"])
        self.assertNotIn("HOB-ALG-01", self.look("F010"))
        self.assertNotIn("JC-ALG-01", self.look("F011"))
        # after the FAIR Act takes effect the flag is still raised, nothing is dropped
        later = self.look("F011", "2027-07-02")
        self.assertEqual(later["NJ-ALG-01"]["result"], "applies")
        self.assertIn("HOB-ALG-01", later)
        self.assertTrue(later["HOB-ALG-01"]["conflict_flag"])

    def test_13_ma_rent_cap_guard_no_applies_no_unknown(self):
        for aid in ("F013", "F014", "F016"):
            for as_of in (AS_OF, "2020-06-01", "2030-01-01"):
                for e in lookup.resolve(self.addrs[aid], self.rules, as_of):
                    rule = next(r for r in self.rules if r["team_rule_id"] == e["team_rule_id"])
                    if rule["category"] != "rent_increase_limits":
                        continue
                    if aid == "F016":
                        # no state at all: a CA cap may be unknown, an MA cap must still never show
                        self.assertFalse(rule["jurisdiction"].endswith("MA"), (aid, e))
                    else:
                        self.fail("rent cap reported for %s: %s" % (aid, e))
        # the guard is a named function with a named reason
        boston = self.addrs["F013"]
        bad = next(r for r in self.rules if r["team_rule_id"] == "BOS-RENT-01")
        reason = lookup.guard_ma_rent_control(bad, boston)
        self.assertIn("c.40P", reason)
        entries, omitted = lookup.resolve_with_trace(boston, self.rules, AS_OF)
        why = {o["team_rule_id"]: o["reason"] for o in omitted}
        self.assertEqual(why["BOS-RENT-01"], lookup.MA_RENT_CONTROL_GUARD)
        self.assertEqual(why["MA-RENT-X1"], lookup.MA_RENT_CONTROL_GUARD)
        # the invariant check itself refuses a violating entry
        plan = precedence.build_plan(self.rules)
        forged = [{"team_rule_id": "BOS-RENT-01", "result": "applies",
                   "explanation": "forged", "conflict_flag": False}]
        with self.assertRaises(lookup.InvariantError):
            lookup.check_invariants(lookup.normalize_address(boston), plan, forged)
        forged[0]["result"] = "unknown"
        with self.assertRaises(lookup.InvariantError):
            lookup.check_invariants(lookup.normalize_address(boston), plan, forged)
        # CA rent caps are untouched by the guard
        self.assertEqual(self.result("F004", "CA-RENT-01"), "applies")

    def test_14_cycle_in_overrides_raises_clear_error_instead_of_hanging(self):
        def r(rid, target, inter="yields_to"):
            x = copy.deepcopy(self.rules[0])
            x["team_rule_id"], x["overrides"], x["interaction"] = rid, [target], inter
            return x
        cases = {
            "two-cycle": [r("A", "B"), r("B", "A")],
            "three-cycle": [r("A", "B"), r("B", "C"), r("C", "A")],
            "self-cycle": [r("A", "A")],
            "mixed-interactions": [r("A", "B", "preempts_pending"), r("B", "A", "yields_to")],
        }

        def boom(signum, frame):
            raise AssertionError("engine hung on a cycle")
        old = signal.signal(signal.SIGALRM, boom)
        signal.alarm(10)
        try:
            for name, rs in cases.items():
                with self.assertRaises(precedence.PrecedenceCycleError, msg=name) as cm:
                    precedence.build_plan(rs)
                self.assertIn("cycle", str(cm.exception))
                with self.assertRaises(precedence.PrecedenceCycleError, msg=name):
                    lookup.resolve(self.addrs["F001"], rs, AS_OF)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)
        self.assertIn("A", str(cm.exception))
        # run.py turns it into a clean message and exit code, not a traceback
        with tempfile.TemporaryDirectory() as td:
            rp = Path(td) / "rules.json"
            rp.write_text(json.dumps(cases["two-cycle"]))
            (Path(td) / "rules_withheld.json").write_text(json.dumps(
                {"withheld_projections": []}))
            proc = run_cli("--as-of", AS_OF, "--rules", str(rp),
                           "--jurisdictions", str(FIX / "jurisdictions.json"),
                           "--addresses", str(FIX / "addresses.csv"),
                           "--out", str(Path(td) / "lookups.json"))
            self.assertEqual(proc.returncode, 2)
            self.assertIn("cycle", proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)
            self.assertFalse((Path(td) / "lookups.json").exists())


# ================================================================ invariants

class TestInvariants(EngineCase):

    def test_as_of_is_required_everywhere(self):
        addr = self.addrs["F001"]
        with self.assertRaises(TypeError):
            lookup.resolve(addr, self.rules)                       # noqa
        with self.assertRaises(TypeError):
            lookup.resolve_with_trace(addr, self.rules)            # noqa
        with self.assertRaises(TypeError):
            coverage.effective_state(self.rules[0])                # noqa
        with self.assertRaises(TypeError):
            run.run_lookups(self.addrs, self.rules)                # noqa
        with self.assertRaises(TypeError):
            coverage.parse_as_of(None)
        with self.assertRaises(ValueError):
            coverage.parse_as_of("not-a-date")
        proc = run_cli("--rules", str(FIX / "rules.json"))
        self.assertNotEqual(proc.returncode, 0)                    # argparse: --as-of required
        self.assertIn("--as-of", proc.stderr)

    def test_no_function_defaults_to_today(self):
        for mod in (coverage, lookup, precedence, run):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            for token in ("date.today", "datetime.now", "datetime.today", "time.time(", "utcnow"):
                self.assertNotIn(token, src, "%s reads the clock in %s" % (token, mod.__name__))

    def test_every_entry_shape_and_invariants_across_fixtures(self):
        for as_of in ("2025-12-31", "2026-01-02", AS_OF, "2027-07-02"):
            for aid, addr in self.addrs.items():
                entries = lookup.resolve(addr, self.rules, as_of)
                seen = set()
                for e in entries:
                    self.assertEqual(list(e), ["team_rule_id", "result", "explanation", "conflict_flag"])
                    self.assertIn(e["result"], lookup.RESULTS)
                    self.assertIsInstance(e["conflict_flag"], bool)
                    self.assertTrue(e["explanation"].strip())
                    self.assertNotIn(e["team_rule_id"], seen)
                    seen.add(e["team_rule_id"])
                    self.assertNotIn("does not apply", e["explanation"].lower())

    def test_explanations_never_overclaim_when_unknown(self):
        for as_of in (AS_OF, "2027-07-02"):
            for aid, addr in self.addrs.items():
                for e in lookup.resolve(addr, self.rules, as_of):
                    text = e["explanation"]
                    if e["result"] == "unknown":
                        self.assertTrue(text.startswith("Unknown:"), text)
                    if e["result"] == "applies":
                        self.assertTrue(text.startswith("Applies:"), text)
                    if e["result"] == "pending":
                        self.assertTrue(text.startswith("Pending:"), text)

    def test_unknown_explanation_names_the_deciding_fact_and_value(self):
        e = self.look("F002")["SF-RENT-01"]
        self.assertIn("year built, which is 1979", e["explanation"])
        e = self.look("F003")["SF-RENT-01"]
        self.assertIn("year built, which is not in the assessor record", e["explanation"])
        e = self.look("F008")["BERK-RENT-01"]
        self.assertEqual(
            e["explanation"],
            "Unknown: coverage of Berkeley rent ordinance (fixture) depends on unit count, which is "
            "not in the assessor record for this address. Checked: state is CA; "
            "legal jurisdiction is Berkeley city, CA.")

    def test_conflict_flag_comes_through_from_the_rule_record(self):
        e = self.look("F008")["BERK-ALG-01"]
        self.assertTrue(e["conflict_flag"])
        self.assertIn("2026-03-01", e["explanation"])
        self.assertIn("2026-01", e["explanation"])
        self.assertIn("No effective date is recorded", e["explanation"])

    def test_unresolved_jurisdiction_is_unknown_for_city_rules_not_false(self):
        look = self.look("F015")                                   # NJ, jurisdiction null
        self.assertEqual(look["HOB-ALG-01"]["result"], "unknown")
        self.assertEqual(look["JC-ALG-01"]["result"], "unknown")
        self.assertIn("could not be resolved", look["HOB-ALG-01"]["explanation"])
        # a different state is a definite false, which beats the unknown jurisdiction
        ca_unresolved = {"address_id": "X", "state": "CA", "jurisdiction": None,
                         "year_built": 1960, "units": 9}
        ids = {e["team_rule_id"] for e in lookup.resolve(ca_unresolved, self.rules, AS_OF)}
        self.assertNotIn("HOB-ALG-01", ids)
        self.assertIn("SF-ALG-01", ids)                            # unknown, still listed
        self.assertEqual(by_id(lookup.resolve(ca_unresolved, self.rules, AS_OF))["SF-ALG-01"]["result"], "unknown")

    def test_census_place_suffix_is_tolerated_on_address_side_only(self):
        self.assertEqual(self.result("F008", "BERK-ALG-01"), "applies")      # "Berkeley city, CA"
        pred = {"fact": "jurisdiction", "op": "eq", "value": "Jersey City, NJ"}
        self.assertEqual(coverage.evaluate(pred, {"jurisdiction": "Jersey City, NJ"}), TRUE)
        self.assertEqual(coverage.evaluate(pred, {"jurisdiction": "Jersey City city, NJ"}), TRUE)
        self.assertEqual(coverage.evaluate(pred, {"jurisdiction": "Newark city, NJ"}), FALSE)
        self.assertEqual(coverage.evaluate(pred, {"jurisdiction": "Jersey, NJ"}), FALSE)

    def test_partial_effective_date_is_unknown_inside_the_period(self):
        self.assertEqual(self.result("F009", "CA-SCRN-01", "2026-09-30"), "not_yet_effective")
        e = self.look("F009", "2026-10-01")["CA-SCRN-01"]
        self.assertEqual(e["result"], "unknown")
        self.assertIn("2026-10", e["explanation"])
        self.assertEqual(self.result("F009", "CA-SCRN-01", "2026-11-01"), "applies")

    def test_not_yet_effective_without_date_stays_not_yet_effective(self):
        e = self.look("F012")["NJ-NOD-01"]
        self.assertEqual(e["result"], "not_yet_effective")
        self.assertIn("no effective date is recorded", e["explanation"])

    def test_unknown_rule_under_applying_dominant_is_noted_not_hidden(self):
        rules = copy.deepcopy(self.rules)
        cap = next(r for r in rules if r["team_rule_id"] == "CA-RENT-01")
        cap["coverage_conditions"]["predicates"] = [{"fact": "units", "op": "gte", "value": 2}]
        addr = {"address_id": "X", "state": "CA", "jurisdiction": "San Francisco, CA",
                "year_built": 1950, "units": None}
        got = by_id(lookup.resolve(addr, rules, AS_OF))
        self.assertEqual(got["SF-RENT-01"]["result"], "applies")
        self.assertEqual(got["CA-RENT-01"]["result"], "unknown")   # spec: coverage unknown -> unknown
        self.assertIn("SF-RENT-01", got["CA-RENT-01"]["explanation"])
        self.assertIn("would be superseded", got["CA-RENT-01"]["explanation"])

    def test_precedence_warnings_for_bad_interaction_and_missing_target(self):
        r1 = copy.deepcopy(self.rules[0])
        r1["overrides"], r1["interaction"] = ["NOPE"], "yields_to"
        r2 = copy.deepcopy(self.rules[1])
        r2["overrides"], r2["interaction"] = ["CA-RENT-01"], "supersedes"
        plan = precedence.build_plan([r1, r2])
        self.assertEqual(len(plan.warnings), 2)
        self.assertEqual(plan.yields_to, {})

    def test_bad_rule_records_raise_clear_errors(self):
        bad = copy.deepcopy(self.rules[0])
        bad["status"] = "weird"
        with self.assertRaises(coverage.RuleInputError):
            lookup.resolve(self.addrs["F001"], [bad], AS_OF)
        dup = [copy.deepcopy(self.rules[0]), copy.deepcopy(self.rules[0])]
        with self.assertRaises(coverage.RuleInputError):
            precedence.build_plan(dup)

    def test_deterministic_output(self):
        a = json.dumps(run.run_lookups(self.addrs, self.rules, AS_OF)[0], sort_keys=True)
        b = json.dumps(run.run_lookups(self.addrs, self.rules, AS_OF)[0], sort_keys=True)
        self.assertEqual(a, b)

    def test_t_cases_shapes_on_fixtures(self):
        for aid, addr in self.addrs.items():
            st = addr["state"]
            before = by_id(lookup.resolve(addr, self.rules, "2026-10-01"))
            after = by_id(lookup.resolve(addr, self.rules, "2027-07-02"))
            if st == "NJ":                                          # T3
                self.assertEqual(before["NJ-ALG-01"]["result"], "not_yet_effective")
                self.assertEqual(after["NJ-ALG-01"]["result"], "applies")
            if st == "MA":                                          # T4, T5
                self.assertEqual(before["MA-ALG-P1"]["result"], "pending")
                self.assertEqual(before["MA-ALG-P2"]["result"], "pending")
                self.assertNotIn("MA-RENT-P1", before)


# ============================================================ fixtures + CLI

def run_cli(*args):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([sys.executable, str(ROOT / "navigator" / "engine" / "run.py")] + list(args),
                          cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=120)


class TestFixturesAndCli(unittest.TestCase):

    def test_fixture_rules_validate_against_the_schema(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        with open(ROOT / "schema" / "rule_record.schema.json", encoding="utf-8") as fh:
            schema = json.load(fh)
        validator = jsonschema.Draft7Validator(schema)
        for r in load_rules():
            errs = sorted(validator.iter_errors(r), key=lambda e: list(e.path))
            self.assertEqual(errs, [], "%s: %s" % (r["team_rule_id"], [e.message for e in errs]))

    def test_cli_end_to_end_on_fixtures(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "lookups.json"
            withheld = Path(td) / "rules_withheld.json"
            withheld.write_text(json.dumps({"withheld_projections": []}))
            proc = run_cli("--as-of", AS_OF, "--rules", str(FIX / "rules.json"),
                           "--withheld", str(withheld),
                           "--jurisdictions", str(FIX / "jurisdictions.json"),
                           "--addresses", str(FIX / "addresses.csv"), "--out", str(out))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)
            data = json.loads(out.read_text())
            self.assertEqual(data["as_of"], AS_OF)
            self.assertEqual(sorted(data["lookups"]), sorted(load_addresses()))
            self.assertEqual(len(data["lookups"]), 16)
            self.assertTrue(out.read_text().isascii())
            self.assertIn("as_of 2026-10-01", proc.stdout)

    def test_cli_missing_inputs_give_a_clear_message_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as td:
            missing = str(Path(td) / "nope.json")
            for args in (["--rules", missing,
                          "--jurisdictions", str(FIX / "jurisdictions.json")],
                         ["--rules", str(FIX / "rules.json"), "--jurisdictions", missing]):
                proc = run_cli("--as-of", AS_OF, "--addresses", str(FIX / "addresses.csv"),
                               "--out", str(Path(td) / "o.json"), *args)
                self.assertEqual(proc.returncode, 2)
                self.assertIn("missing input", proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
                self.assertFalse((Path(td) / "o.json").exists())
            bad = Path(td) / "bad.json"
            bad.write_text("{not json")
            proc = run_cli("--as-of", AS_OF, "--rules", str(bad),
                           "--jurisdictions", str(FIX / "jurisdictions.json"))
            self.assertEqual(proc.returncode, 2)
            self.assertIn("not valid JSON", proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)
            proc = run_cli("--as-of", "10/01/2026", "--rules", str(FIX / "rules.json"),
                           "--jurisdictions", str(FIX / "jurisdictions.json"))
            self.assertEqual(proc.returncode, 2)
            self.assertNotIn("Traceback", proc.stderr)

    def test_default_command_with_real_inputs_absent_fails_cleanly(self):
        if (OUT / "rules.json").exists() and (OUT / "jurisdictions.json").exists():
            self.skipTest("real inputs are present")
        proc = run_cli("--as-of", AS_OF)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("missing input", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)


# ======================================================= real output (if any)

@unittest.skipUnless((OUT / "lookups.json").exists() and (OUT / "rules.json").exists(),
                     "navigator/out/lookups.json not produced yet")
class TestRealOutput(unittest.TestCase):
    """CONTRACT section 10: 500 ids, no MA rent cap. Skipped until the real files exist."""

    @classmethod
    def setUpClass(cls):
        cls.lookups = json.loads((OUT / "lookups.json").read_text())
        cls.rules = {r["team_rule_id"]: r for r in json.loads((OUT / "rules.json").read_text())
                     if isinstance(r, dict)}
        with open(ROOT / "data" / "sample_addresses.csv", newline="", encoding="utf-8") as fh:
            cls.sample = {r["address_id"]: r for r in csv.DictReader(fh)}

    def test_exactly_500_address_ids(self):
        self.assertEqual(len(self.lookups["lookups"]), 500)
        self.assertEqual(set(self.lookups["lookups"]), set(self.sample))

    def test_no_ma_rent_cap_and_no_failed_rule(self):
        for aid, entries in self.lookups["lookups"].items():
            for e in entries:
                rule = self.rules[e["team_rule_id"]]
                self.assertNotEqual(rule["status"], "failed", (aid, e))
                if self.sample[aid]["state"] == "MA":
                    self.assertNotEqual(rule["category"], "rent_increase_limits", (aid, e))


if __name__ == "__main__":
    unittest.main(verbosity=2)
