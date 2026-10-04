"""Deterministic T1-T5 and withheld-evidence tests. No network or model calls."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from navigator.changes import diff as generic_diff, run as changes
from navigator.engine import lookup, run as engine_run, withheld as withheld_engine

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "navigator" / "tests" / "fixtures"
TESTS = json.loads((ROOT / "dev" / "change_tests.json").read_text())


def fixtures():
    rules = json.loads((FIX / "rules.json").read_text())
    # The older engine fixture deliberately contains two bad MA rent-cap
    # records for guard tests. They are not part of a clean T1-T5 scenario.
    rules = [r for r in rules if r["team_rule_id"] not in ("BOS-RENT-01", "MA-RENT-X1")]
    next(r for r in rules if r["team_rule_id"] == "MA-RENT-P1")["provenance_grade"] = "source_text"
    juris = engine_run.load_jurisdictions(FIX / "jurisdictions.json")
    sample = engine_run.load_sample_addresses(FIX / "addresses.csv")
    addresses = engine_run.build_addresses(juris, sample)
    addresses.pop("F015")  # missing legal city is tested separately
    addresses.pop("F016")  # missing state is tested separately
    return rules, addresses


def ab325_withheld():
    rules, _ = fixtures()
    record = copy.deepcopy(next(r for r in rules if r["team_rule_id"] == "CA-ALG-01"))
    record.update({
        "status": None,
        "effective_date": None,
        "status_establishable": False,
        "projection_withheld_reason": "D022 states approval but no operative date.",
        "instrument_type": "statute",
        "source_kind": "primary_legal_text",
        "provenance_grade": "source_text",
        "source_doc_id": "D022",
        "supporting_spans": [
            {"text": "CHAPTER 338", "verified": True},
            {"text": "Approved by Governor October 06, 2025.", "verified": True},
        ],
        "coverage_conditions": {
            "predicates": [],
            "needs_fact": ["rental_housing_applicability", "agreement_or_coercion"],
            "note": "D022 does not mention rental housing.",
        },
        "epistemics": {"rental_housing_applicability": {"category": "inferred"}},
    })
    return record


def get_rule(rules, rid):
    return next(r for r in rules if r["team_rule_id"] == rid)


class TestWithheldEngine(unittest.TestCase):
    def setUp(self):
        self.rules, self.addresses = fixtures()
        self.withheld = ab325_withheld()
        self.established = [r for r in self.rules if r["team_rule_id"] != "CA-ALG-01"]

    def test_date_derivation_does_not_turn_d022_into_address_applicability(self):
        before = lookup.resolve(self.addresses["F001"], self.established,
                                "2025-12-31", withheld=[self.withheld])
        after = lookup.resolve(self.addresses["F001"], self.established,
                               "2026-01-02", withheld=[self.withheld])
        b = next(e for e in before if e["team_rule_id"] == "CA-ALG-01")
        a = next(e for e in after if e["team_rule_id"] == "CA-ALG-01")
        self.assertEqual((b["result"], a["result"]), ("unknown", "unknown"))
        self.assertEqual((b["temporal_result"], a["temporal_result"]),
                         ("not_yet_effective", "date_reached"))
        self.assertEqual(a["evidence_status"], "not_established")
        self.assertEqual(a["reason_code"], "projection_withheld")
        self.assertEqual(a["result_state"], "indeterminate")
        self.assertEqual(a["source_doc_id"], "D022")
        self.assertEqual(a["as_of"], "2026-01-02")
        self.assertIn("D022 states approval", a["reason"])
        self.assertEqual(a["date_derivation"]["output"], "2026-01-01")
        self.assertFalse(a["date_derivation"]["corpus_support"])
        self.assertIn("rental_housing_applicability", a["needs_fact"])
        self.assertIn("agreement_or_coercion", a["needs_fact"])
        self.assertIn("does not establish rental-housing applicability", a["explanation"])
        self.assertNotIn("Applies:", a["explanation"])

    def test_unverified_or_incomplete_date_evidence_cannot_derive_date(self):
        variants = []
        unverified = copy.deepcopy(self.withheld)
        unverified["supporting_spans"][1]["verified"] = False
        variants.append(unverified)
        no_chapter = copy.deepcopy(self.withheld)
        no_chapter["supporting_spans"] = no_chapter["supporting_spans"][1:]
        variants.append(no_chapter)
        summary = copy.deepcopy(self.withheld)
        summary["provenance_grade"] = "summary_only"
        variants.append(summary)
        for record in variants:
            entry = next(e for e in lookup.resolve(self.addresses["F001"], [],
                                                   "2026-01-02", withheld=[record])
                         if e["team_rule_id"] == "CA-ALG-01")
            self.assertEqual(entry["result"], "unknown")
            self.assertEqual(entry["temporal_result"], "not_established")
            self.assertIsNone(entry["date_derivation"])

    def test_withheld_scope_false_is_traced_and_not_reported_elsewhere(self):
        entries, omitted = lookup.resolve_with_trace(self.addresses["F012"], [],
                                                      "2026-10-01", withheld=[self.withheld])
        self.assertEqual(entries, [])
        self.assertEqual(omitted[0]["team_rule_id"], "CA-ALG-01")
        self.assertIn("coverage false", omitted[0]["reason"])

    def test_withheld_missing_unit_and_co_date_facts_are_named(self):
        record = copy.deepcopy(self.withheld)
        record["jurisdiction"] = "San Francisco, CA"
        record["level"] = "city"
        record["coverage_conditions"] = {
            "predicates": [{"fact": "year_built", "op": "lte", "value": "1979-06-13",
                            "basis": "certificate_of_occupancy"},
                           {"fact": "units", "op": "gte", "value": 2}],
            "needs_fact": ["owner_type"],
        }
        entry = lookup.resolve(self.addresses["F002"], [], "2026-10-01", withheld=[record])[0]
        self.assertEqual(entry["result"], "unknown")
        self.assertIn("certificate_of_occupancy_date", entry["needs_fact"])
        self.assertIn("owner_type", entry["needs_fact"])
        addr = dict(self.addresses["F002"], units=None)
        entry = lookup.resolve(addr, [], "2026-10-01", withheld=[record])[0]
        self.assertIn("units", entry["needs_fact"])

    def test_null_status_inside_rules_is_not_silently_discarded(self):
        output, stats = engine_run.run_lookups(self.addresses, [self.withheld], "2026-10-01")
        self.assertEqual(stats["withheld_rule_ids"], ["CA-ALG-01"])
        self.assertEqual(output["F001"][0]["result"], "unknown")
        self.assertEqual(output["F001"][0]["evidence_status"], "not_established")

    def test_inferred_in_force_status_without_date_does_not_apply(self):
        record = copy.deepcopy(get_rule(self.rules, "HOB-ALG-01"))
        record["effective_date"] = None
        record["status_basis"] = "inferred"
        entries = lookup.resolve(self.addresses["F011"], [record], "2026-10-01")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["result"], "unknown")
        self.assertIn("no source-established effective date", entries[0]["explanation"])

    def test_withheld_local_rule_is_unknown_in_its_city_and_not_elsewhere(self):
        record = copy.deepcopy(get_rule(self.rules, "HOB-ALG-01"))
        record["status"] = None
        record["effective_date"] = None
        record["projection_withheld_reason"] = "No operative date in verified source text."
        own = lookup.resolve(self.addresses["F011"], [], "2026-10-01", withheld=[record])
        other = lookup.resolve(self.addresses["F010"], [], "2026-10-01", withheld=[record])
        self.assertEqual(own[0]["result"], "unknown")
        self.assertIn("No operative date", own[0]["explanation"])
        self.assertEqual(other, [])

    def test_withheld_state_preemption_does_not_flag_unrelated_city(self):
        record = copy.deepcopy(get_rule(self.rules, "NJ-ALG-01"))
        record["status"] = None
        record["effective_date"] = None
        newark = lookup.resolve(self.addresses["F012"], [], "2026-10-01", withheld=[record])
        self.assertEqual(newark[0]["result"], "unknown")
        self.assertFalse(newark[0]["conflict_flag"])
        self.assertNotIn("Conflict flagged", newark[0]["explanation"])

    def test_d001_style_withheld_record_remains_visible_at_berkeley_address(self):
        record = copy.deepcopy(get_rule(self.rules, "BERK-ALG-01"))
        record["status"] = None
        record["source_doc_id"] = "D001"
        record["projection_withheld_reason"] = "Adoption and operative date are not established."
        entry = lookup.resolve(self.addresses["F008"], [], "2026-10-01",
                               withheld=[record])[0]
        self.assertEqual(entry["result"], "unknown")
        self.assertEqual(entry["result_state"], "indeterminate")
        self.assertEqual(entry["source_doc_id"], "D001")
        self.assertIn("Adoption and operative date", entry["reason"])

    def test_d076_style_pending_record_never_applies_without_enactment(self):
        record = copy.deepcopy(get_rule(self.rules, "HOB-ALG-01"))
        record["team_rule_id"] = "SD-ALG-01"
        record["jurisdiction"] = "San Diego, CA"
        record["coverage_conditions"] = {"predicates": [], "needs_fact": [], "note": "fixture"}
        record["status"] = "pending"
        record["effective_date"] = None
        record["source_doc_id"] = "D076"
        entry = lookup.resolve_detailed(self.addresses["F007"], [record],
                                        "2026-10-01")[0]
        self.assertEqual(entry["result_state"], "pending")
        self.assertEqual(entry["source_doc_id"], "D076")

    def test_duplicate_id_and_substantive_withheld_status_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate team_rule_id"):
            engine_run.run_lookups(self.addresses, self.rules, "2026-10-01", [self.withheld])
        bad = copy.deepcopy(self.withheld)
        bad["status"] = "in_force"
        with self.assertRaisesRegex(ValueError, "substantive status"):
            engine_run.run_lookups(self.addresses, [], "2026-10-01", [bad])

    def test_cli_auto_loads_sibling_withheld_and_requires_explicit_file(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "rules.json").write_text(json.dumps([]))
            (folder / "rules_withheld.json").write_text(json.dumps(
                {"withheld_projections": [self.withheld]}))
            output = folder / "lookups.json"
            base = [sys.executable, str(ROOT / "navigator" / "engine" / "run.py"),
                    "--as-of", "2026-10-01", "--rules", str(folder / "rules.json"),
                    "--jurisdictions", str(FIX / "jurisdictions.json"),
                    "--addresses", str(FIX / "addresses.csv"), "--out", str(output)]
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            done = subprocess.run(base, cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            data = json.loads(output.read_text())
            self.assertEqual(data["withheld_rule_ids"], ["CA-ALG-01"])
            self.assertEqual(data["lookups"]["F001"][0]["result"], "unknown")
            detailed = data["lookup_details"]["F001"][0]
            self.assertEqual(detailed["result_state"], "indeterminate")
            self.assertEqual(detailed["source_doc_id"], "D022")
            self.assertEqual(detailed["as_of"], "2026-10-01")
            missing = subprocess.run(base + ["--withheld", str(folder / "missing.json")],
                                     cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(missing.returncode, 2)
            self.assertIn("missing input", missing.stderr)

    def test_both_clis_require_valid_companion_withheld_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            for scenario in ("missing", "empty", "nonempty", "invalid", "unreadable"):
                folder = Path(td) / scenario
                folder.mkdir()
                (folder / "rules.json").write_text(json.dumps([]))
                withheld_path = folder / "rules_withheld.json"
                if scenario == "empty":
                    withheld_path.write_text(json.dumps({"withheld_projections": []}))
                elif scenario == "nonempty":
                    withheld_path.write_text(json.dumps(
                        {"withheld_projections": [self.withheld]}))
                elif scenario == "invalid":
                    withheld_path.write_text("{invalid json")
                elif scenario == "unreadable":
                    withheld_path.mkdir()
                for module in ("engine", "changes"):
                    with self.subTest(scenario=scenario, module=module):
                        output = folder / (module + ".json")
                        command = [sys.executable, str(ROOT / "navigator" / module / "run.py"),
                                   "--rules", str(folder / "rules.json"),
                                   "--jurisdictions", str(FIX / "jurisdictions.json"),
                                   "--addresses", str(FIX / "addresses.csv"),
                                   "--out", str(output)]
                        if module == "engine":
                            command += ["--as-of", "2026-10-01"]
                        proc = subprocess.run(
                            command, cwd=ROOT,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                            capture_output=True, text=True)
                        if scenario in ("missing", "invalid", "unreadable"):
                            self.assertEqual(proc.returncode, 2, proc.stderr)
                            self.assertFalse(output.exists())
                            message = {"missing": "missing input", "invalid": "not valid JSON",
                                       "unreadable": "could not read input"}[scenario]
                            self.assertIn(message, proc.stderr)
                        else:
                            self.assertEqual(proc.returncode, 0 if module == "engine" else 3,
                                             proc.stderr)
                            self.assertTrue(output.exists())
                            data = json.loads(output.read_text())
                            if module == "engine":
                                self.assertEqual(data["withheld_rule_ids"],
                                                 [] if scenario == "empty" else ["CA-ALG-01"])
                            else:
                                self.assertEqual(data["T1"]["rule_traces"]["CA-ALG-01"]
                                                 ["record_present"], scenario == "nonempty")


class TestChangeScenarios(unittest.TestCase):
    def setUp(self):
        self.rules, self.addresses = fixtures()

    def run_scenarios(self, rules=None, withheld=(), addresses=None):
        return changes.run_changes(addresses or self.addresses,
                                   self.rules if rules is None else rules,
                                   withheld, TESTS)

    def test_five_scenarios_with_complete_fixture_evidence(self):
        output = self.run_scenarios()
        self.assertEqual(set(output), {"T1", "T2", "T3", "T4", "T5"})
        self.assertTrue(all(x["status"] == "confirmed" for x in output.values()), output)
        ca = {aid for aid, a in self.addresses.items() if a["state"] == "CA"}
        nj = {aid for aid, a in self.addresses.items() if a["state"] == "NJ"}
        ma = {aid for aid, a in self.addresses.items() if a["state"] == "MA"}
        self.assertEqual(set(output["T1"]["affected_address_ids"]), ca)
        self.assertEqual(set(output["T2"]["affected_address_ids"]), {"F010", "F011"})
        self.assertEqual(set(output["T3"]["affected_address_ids"]), nj)
        self.assertEqual(set(output["T3"]["conflict_flag_address_ids"]), {"F010", "F011"})
        self.assertEqual(set(output["T4"]["affected_address_ids"]), ma)
        self.assertEqual(output["T5"]["affected_address_ids"], [])
        self.assertEqual(output["T5"]["unresolved_address_ids"], [])
        self.assertEqual(output["T1"]["rule_traces"]["CA-ALG-01"]["by_date"]["2025-12-31"],
                         {"not_yet_effective": len(ca)})

    def test_t1_withheld_ab325_reports_date_boundary_as_possible_only(self):
        rules = [r for r in self.rules if r["team_rule_id"] != "CA-ALG-01"]
        output = self.run_scenarios(rules, [ab325_withheld()])["T1"]
        ca = {aid for aid, a in self.addresses.items() if a["state"] == "CA"}
        self.assertEqual(output["status"], "not_established")
        self.assertEqual(output["affected_address_ids"], [])
        self.assertEqual(set(output["possible_affected_address_ids"]), ca)
        self.assertEqual(set(output["unresolved_address_ids"]), ca)
        self.assertEqual(output["rule_traces"]["CA-ALG-01"]["status"], None)
        self.assertEqual(output["rule_traces"]["CA-ALG-01"]["rental_housing_applicability"],
                         "inferred")

    def test_missing_rule_does_not_become_an_expected_answer(self):
        rules = [r for r in self.rules if r["team_rule_id"] != "CA-ALG-01"]
        output = self.run_scenarios(rules)["T1"]
        self.assertEqual(output["affected_address_ids"], [])
        self.assertEqual(output["possible_affected_address_ids"], [])
        self.assertEqual(output["missing_rule_ids"], ["CA-ALG-01"])
        self.assertEqual(output["status"], "not_established")

    def test_wrong_city_local_rule_is_reported_as_failure(self):
        rules = copy.deepcopy(self.rules)
        bad = get_rule(rules, "HOB-ALG-01")
        bad["jurisdiction"], bad["level"] = "NJ", "state"
        output = self.run_scenarios(rules)["T2"]
        self.assertEqual(output["status"], "failed")
        self.assertIn("F010", output["unexpected_address_ids"])
        self.assertIn("F012", output["unexpected_address_ids"])

    def test_fair_act_early_application_is_reported_as_failure(self):
        rules = copy.deepcopy(self.rules)
        bad = get_rule(rules, "NJ-ALG-01")
        bad["effective_date"] = "2026-01-01"
        output = self.run_scenarios(rules)["T3"]
        self.assertEqual(output["status"], "failed")
        self.assertEqual(set(output["unexpected_address_ids"]), {"F010", "F011", "F012"})

    def test_generic_diff_changes_when_only_effective_date_mutates(self):
        address = self.addresses["F001"]
        original = copy.deepcopy(get_rule(self.rules, "CA-ALG-01"))
        moved = copy.deepcopy(original)
        moved["effective_date"] = "2027-01-01"

        def observed(rule, as_of):
            entry = lookup.resolve(address, [rule], as_of)[0]
            return changes.observe(address, rule, entry)

        early = "2025-12-31"
        late = "2026-01-02"
        self.assertEqual(changes.diff(observed(original, early), observed(original, late)),
                         "newly_applies")
        self.assertEqual(changes.diff(observed(moved, early), observed(moved, late)),
                         "unchanged")
        self.assertEqual(changes.diff(observed(moved, late), observed(moved, "2027-01-02")),
                         "newly_applies")

    def test_missing_local_evidence_does_not_create_conflict_flags(self):
        rules = [r for r in self.rules if r["team_rule_id"] != "JC-ALG-01"]
        output = self.run_scenarios(rules)["T3"]
        self.assertEqual(output["status"], "not_established")
        self.assertIn("JC-ALG-01", output["missing_rule_ids"])
        self.assertNotIn("F010", output["conflict_flag_address_ids"])
        self.assertIn("F010", output["unresolved_address_ids"])
        self.assertNotIn("F012", output["conflict_flag_address_ids"])

    def test_jersey_city_absence_and_hoboken_date_gap_stay_unresolved(self):
        rules = [copy.deepcopy(r) for r in self.rules if r["team_rule_id"] != "JC-ALG-01"]
        hoboken = get_rule(rules, "HOB-ALG-01")
        hoboken["effective_date"] = None
        hoboken["status_basis"] = "inferred"
        result = self.run_scenarios(rules)["T2"]
        self.assertEqual(result["status"], "not_established")
        self.assertIn("JC-ALG-01", result["missing_rule_ids"])
        self.assertEqual(result["affected_address_ids"], [])
        self.assertEqual(set(result["unresolved_address_ids"]), {"F010", "F011"})

    def test_pending_bill_cannot_be_reported_as_in_force(self):
        rules = copy.deepcopy(self.rules)
        bad = get_rule(rules, "MA-ALG-P1")
        bad["status"], bad["effective_date"] = "in_force", "2026-01-01"
        output = self.run_scenarios(rules)["T4"]
        self.assertEqual(output["status"], "failed")
        self.assertEqual(set(output["unexpected_address_ids"]), {"F013", "F014"})

    def test_t5_empty_set_is_not_proof_when_ballot_source_missing(self):
        rules = [r for r in self.rules if r["team_rule_id"] != "MA-RENT-P1"]
        output = self.run_scenarios(rules)["T5"]
        self.assertEqual(output["affected_address_ids"], [])
        self.assertEqual(output["status"], "not_established")
        self.assertEqual(set(output["unresolved_address_ids"]), {"F013", "F014"})

    def test_t5_detects_bad_ma_cap_record_even_if_engine_guard_omits_it(self):
        rules = copy.deepcopy(self.rules)
        bad = copy.deepcopy(get_rule(rules, "MA-RENT-P1"))
        bad["team_rule_id"], bad["status"] = "BOS-RENT-BAD", "in_force"
        rules.append(bad)
        output = self.run_scenarios(rules)["T5"]
        self.assertEqual(output["status"], "failed")
        self.assertEqual(output["unexpected_rule_ids"], ["BOS-RENT-BAD"])
        self.assertEqual(output["affected_address_ids"], [])

    def test_missing_geography_stays_unresolved(self):
        _, all_addresses = fixtures()
        juris = engine_run.load_jurisdictions(FIX / "jurisdictions.json")
        sample = engine_run.load_sample_addresses(FIX / "addresses.csv")
        all_addresses["F015"] = engine_run.build_addresses(juris, sample)["F015"]
        output = self.run_scenarios(addresses=all_addresses)
        self.assertIn("F015", output["T2"]["unresolved_address_ids"])
        self.assertIn("F015", output["T3"]["unresolved_address_ids"])
        self.assertNotIn("F015", output["T2"]["affected_address_ids"])

    def test_output_is_deterministic_and_cli_writes_all_five(self):
        first = json.dumps(self.run_scenarios(), sort_keys=True)
        self.assertEqual(first, json.dumps(self.run_scenarios(), sort_keys=True))
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "rules.json").write_text(json.dumps(self.rules))
            (folder / "rules_withheld.json").write_text(json.dumps(
                {"withheld_projections": []}))
            out = folder / "changes.json"
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            proc = subprocess.run(
                [sys.executable, str(ROOT / "navigator" / "changes" / "run.py"),
                 "--rules", str(folder / "rules.json"),
                 "--jurisdictions", str(FIX / "jurisdictions.json"),
                 "--addresses", str(FIX / "addresses.csv"), "--out", str(out)],
                cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 3)  # F015 has unresolved legal geography
            self.assertTrue(out.exists())
            self.assertEqual(set(json.loads(out.read_text())), {"T1", "T2", "T3", "T4", "T5"})


class TestGenericAddressDiff(unittest.TestCase):
    def setUp(self):
        self.rules, self.addresses = fixtures()
        self.rule = copy.deepcopy(get_rule(self.rules, "CA-ALG-01"))

    def compare(self, address_id="F001", before=None, after=None,
                before_date="2025-12-31", after_date="2026-01-02", **kwargs):
        return generic_diff.compare_address(
            self.addresses[address_id],
            [self.rule] if before is None else before,
            [self.rule] if after is None else after,
            before_date, after_date, **kwargs)

    def test_effective_date_mutation_changes_generic_result(self):
        original = self.compare()[0]
        self.assertEqual(original["result_state"], "became_effective")
        self.assertEqual((original["prior_state"], original["current_state"]),
                         ("not_yet_effective", "applies"))
        self.assertEqual(original["as_of"], "2026-01-02")
        self.assertEqual(original["rule_id"], "CA-ALG-01")
        moved = copy.deepcopy(self.rule)
        moved["effective_date"] = "2027-01-01"
        self.assertEqual(self.compare(before=[moved], after=[moved])[0]["result_state"],
                         "unchanged")
        self.assertEqual(self.compare(before=[moved], after=[moved],
                                      before_date="2026-12-31",
                                      after_date="2027-01-02")[0]["result_state"],
                         "became_effective")

    def test_missing_record_is_unresolved_until_absence_is_documented(self):
        unknown = self.compare(before=[])[0]
        self.assertEqual(unknown["result_state"], "remains_unresolved")
        self.assertEqual(unknown["prior_state"], "could_not_determine")
        added = self.compare(before=[],
                             documented_absent_before={"CA-ALG-01":
                                                        "Verified prior legal inventory."})[0]
        self.assertEqual(added["result_state"], "added")
        self.assertEqual(added["prior_state"], "not_present")
        removed = self.compare(after=[], before_date="2026-01-02",
                               after_date="2027-01-02",
                               documented_absent_after={"CA-ALG-01":
                                                        "Verified repeal record."})[0]
        self.assertEqual(removed["result_state"], "removed")
        with self.assertRaisesRegex(ValueError, "both recorded and documented absent"):
            self.compare(documented_absent_before={"CA-ALG-01": "contradiction"})

    def test_changed_coverage_and_later_date_without_cessation_evidence(self):
        out_of_state = copy.deepcopy(self.rule)
        out_of_state["jurisdiction"] = "NJ"
        changed = self.compare(before=[out_of_state], after=[self.rule],
                               before_date="2026-01-02")[0]
        self.assertEqual(changed["result_state"], "changed_coverage")
        self.assertEqual(changed["prior_state"], "does_not_cover")
        moved = copy.deepcopy(self.rule)
        moved["effective_date"] = "2027-01-01"
        unresolved = self.compare(before=[self.rule], after=[moved],
                                  before_date="2026-01-02",
                                  after_date="2026-01-03")[0]
        self.assertEqual((unresolved["prior_state"], unresolved["current_state"]),
                         ("applies", "not_yet_effective"))
        self.assertEqual(unresolved["result_state"], "remains_unresolved")
        self.assertNotEqual(unresolved["result_state"], "ceased_effective")

    def test_earlier_date_mutation_is_effective_transition_not_addition(self):
        moved = copy.deepcopy(self.rule)
        moved["effective_date"] = "2027-01-01"
        transition = self.compare(before=[moved], after=[self.rule],
                                  before_date="2026-01-02",
                                  after_date="2026-01-03")[0]
        self.assertEqual((transition["prior_state"], transition["current_state"]),
                         ("not_yet_effective", "applies"))
        self.assertEqual(transition["result_state"], "became_effective")
        self.assertNotIn(transition["result_state"], ("added", "removed"))

    def test_pending_to_applicable_is_effective_transition_not_added(self):
        for rid, prior_date, current_date, effective_date in (
                ("CA-ALG-01", "2026-01-02", "2026-01-03", "2026-01-01"),
                ("CA-ALG-MUT", "2028-04-10", "2028-04-11", "2028-04-10")):
            with self.subTest(rule_id=rid, current_date=current_date):
                pending = copy.deepcopy(self.rule)
                pending["team_rule_id"] = rid
                pending["status"] = "pending"
                pending["effective_date"] = None
                operative = copy.deepcopy(pending)
                operative["status"] = "in_force"
                operative["effective_date"] = effective_date
                transition = self.compare(before=[pending], after=[operative],
                                          before_date=prior_date,
                                          after_date=current_date)[0]
                self.assertEqual((transition["prior_state"], transition["current_state"]),
                                 ("pending", "applies"))
                self.assertEqual(transition["result_state"], "became_effective")
                self.assertNotIn(transition["result_state"], ("added", "removed"))

    def test_scenario_adapter_does_not_turn_date_conflict_into_cessation(self):
        before = {"result": "applies", "reason": "fixture applies"}
        after = {"result": "not_yet_effective", "reason": "date moved later"}
        self.assertEqual(changes.diff(before, after), "could_not_determine")

    def test_documented_removal_and_declared_supersession_are_not_cessation(self):
        removed = self.compare(after=[], before_date="2026-01-02",
                               after_date="2026-01-03",
                               documented_absent_after={"CA-ALG-01":
                                                        "Verified repeal record."})[0]
        self.assertEqual(removed["result_state"], "removed")
        statewide = copy.deepcopy(get_rule(self.rules, "CA-RENT-01"))
        local = copy.deepcopy(get_rule(self.rules, "SF-RENT-01"))
        compared = generic_diff.compare_address(
            self.addresses["F001"], [statewide], [statewide, local],
            "2026-01-02", "2026-01-03")
        state_change = next(r for r in compared if r["rule_id"] == "CA-RENT-01")
        self.assertEqual((state_change["prior_state"], state_change["current_state"]),
                         ("applies", "superseded"))
        self.assertEqual(state_change["result_state"], "changed_precedence")
        self.assertNotIn("ceased_effective", {removed["result_state"],
                                               state_change["result_state"]})

    def test_withheld_and_missing_property_fact_have_different_states(self):
        withheld = ab325_withheld()
        legal = self.compare(before=[], after=[],
                             prior_withheld=[withheld], current_withheld=[withheld])[0]
        self.assertEqual(legal["result_state"], "remains_unresolved")
        self.assertEqual(legal["prior_state"], "indeterminate")
        self.assertEqual(legal["source_doc_id"], "D022")
        self.assertIn("rental_housing_applicability", legal["needs_fact"])
        sf = copy.deepcopy(get_rule(self.rules, "SF-RENT-01"))
        address = self.addresses["F003"]
        property_gap = generic_diff.compare_address(address, [sf], [sf],
                                                    "2026-01-01", "2026-10-01")[0]
        self.assertEqual(property_gap["prior_state"], "unknown")
        self.assertEqual(property_gap["result_state"], "remains_unresolved")
        self.assertIn("year_built", property_gap["needs_fact"])

    def test_point_lookup_details_distinguish_legal_and_property_gaps(self):
        legal = lookup.resolve_detailed(self.addresses["F001"], [], "2026-10-01",
                                        withheld=[ab325_withheld()])[0]
        self.assertEqual(legal["result_state"], "indeterminate")
        self.assertIn("operative_status_or_date", legal["needs_evidence"])
        sf = copy.deepcopy(get_rule(self.rules, "SF-RENT-01"))
        property_gap = lookup.resolve_detailed(self.addresses["F003"], [sf],
                                               "2026-10-01")[0]
        self.assertEqual(property_gap["result_state"], "unknown")
        self.assertIn("year_built", property_gap["needs_fact"])
        self.assertEqual(property_gap["needs_evidence"], [])

    def test_failed_and_ma_guard_cannot_be_coverage_changes(self):
        failed = copy.deepcopy(self.rule)
        failed["status"] = "failed"
        result = self.compare(before=[failed], after=[self.rule],
                              before_date="2026-01-02")[0]
        self.assertEqual(result["result_state"], "remains_unresolved")
        ma_rule = copy.deepcopy(get_rule(json.loads((FIX / "rules.json").read_text()),
                                         "MA-RENT-P1"))
        ma_rule["status"] = "in_force"
        ma_rule["effective_date"] = "2020-01-01"
        output = generic_diff.compare_address(self.addresses["F013"], [ma_rule], [ma_rule],
                                              "2026-01-01", "2026-10-01")[0]
        self.assertEqual(output["prior_state"], "does_not_cover")
        self.assertEqual(output["result_state"], "unchanged")

    def test_dates_are_required_and_chronological(self):
        with self.assertRaises(TypeError):
            self.compare(before_date=None)
        with self.assertRaisesRegex(ValueError, "prior_as_of"):
            self.compare(before_date="2027-01-01", after_date="2026-01-01")


if __name__ == "__main__":
    unittest.main(verbosity=2)
