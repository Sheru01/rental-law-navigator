"""Tests for the extraction pipeline (navigator/extract) and navigator/normalize.

Run from the project root:

    python3 -m unittest navigator.tests.test_extract -v
    python3 -m navigator.tests.test_extract

Everything runs offline. No model is called and no network is used. Model behaviour is
simulated by scripted transports, and the echo transport serves canned fixtures. A pass
here proves the pipeline's own guarantees, not the quality of any real model's output.
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from navigator.engine import lookup, precedence  # noqa: E402
from navigator.extract import acceptance, assemble, pipeline, records, spans, transports  # noqa: E402
from navigator.extract.corpus import Document, collapse, list_documents  # noqa: E402
from navigator.extract.redact import Redactor  # noqa: E402
from navigator.normalize.effective_date import (ENGINE_OWNED_RULE_IDS, RULE_CA_DEFAULT,  # noqa: E402
                                                derive_effective_date)

CANARY = "sk-CANARYCANARYCANARY0123456789abcdef"
BODY = (
    "The City of Testville Code, section 12.04: A landlord shall not charge a security deposit exceeding "
    "one month's rent for any residential dwelling unit.\n"
    "Section 12.05: This chapter takes effect on March 1, 2026.\n"
    "Approved by the Mayor on January 15, 2026.\n"
)
SPAN_MAIN = ("A landlord shall not charge a security deposit exceeding one month's rent for any "
             "residential dwelling unit.")
SPAN_EFF = "Section 12.05: This chapter takes effect on March 1, 2026."


# ------------------------------------------------------------------ helpers

def epi(cat, basis="basis", spans_=("quoted_span",), rule=None):
    return {"category": cat, "basis": basis, "derivation_rule": rule, "span_ids": list(spans_)}


def good_rule(**over):
    """A well-formed model candidate for the BODY document."""
    r = {
        "jurisdiction": "Testville, NJ", "category": "security_deposits", "instrument_type": "ordinance",
        "source_kind": "primary_legal_text", "title": "Testville deposit cap",
        "requirement": "A landlord may not charge a deposit above one month's rent.",
        "key_value": "1 month's rent",
        "coverage_conditions": {"predicates": [{"fact": "jurisdiction", "op": "eq", "value": "Testville, NJ"}],
                                "needs_fact": [], "note": "Residential dwelling units in Testville."},
        "exemptions": None,
        "status": "in_force", "status_establishable": True, "status_basis": "derived",
        "effective_date": "2026-03-01", "citation": "Testville Code sec. 12.04",
        "quoted_span": SPAN_MAIN,
        "supporting_spans": [{"id": "s1", "text": SPAN_EFF, "supports": ["effective_date", "status"]}],
        "epistemics": {
            "jurisdiction": epi("stated"), "category": epi("stated"), "title": epi("inferred"),
            "requirement": epi("derived", rule="PARAPHRASE"), "key_value": epi("stated"),
            "coverage_conditions": epi("stated"), "exemptions": epi("unknown", spans_=()),
            "status": epi("derived", spans_=("s1",), rule="ENACTED_EFFECTIVE_BEFORE_AS_OF"),
            "effective_date": epi("stated", spans_=("s1",)), "citation": epi("stated"),
            "rental_housing_applicability": epi("stated")},
        "relations": [], "confidence": 0.9, "conflict_flag": False, "conflict_note": None,
    }
    r.update(over)
    return r


def reply(rules, kind="primary_legal_text", states=True):
    return {"document": {"source_kind": kind, "states_legal_requirement": states, "notes": ""}, "rules": rules}


def make_corpus(root, docs):
    """docs: {doc_id: (subdir, body)}. Writes SOURCE/RETRIEVED headers."""
    root = Path(root)
    for doc_id, (sub, body) in docs.items():
        d = root / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / ("%s.txt" % doc_id)).write_text(
            "SOURCE: https://example.org/%s\nRETRIEVED: 2026-10-01 00:00 UTC\n\n%s" % (doc_id, body),
            encoding="utf-8")
    return root


class FakeTransport(object):
    """Scripted transport. script(doc_id, step, messages) -> dict | str | Exception."""
    name = "fake"
    model = "fake-model"

    def __init__(self, script):
        self.script = script
        self.calls = []
        self.systems = []
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def complete(self, system, messages, doc_id, step):
        with self._lock:
            self.calls.append((doc_id, step))
            self.systems.append(system)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.02)
            out = self.script(doc_id, step, messages)
        finally:
            with self._lock:
                self.active -= 1
        if isinstance(out, Exception):
            raise out
        return out if isinstance(out, str) else json.dumps(out)


class Run(object):
    pass


def run_pipeline(tmp, script, docs=None, extra=None, env=None, transport=None):
    tmp = Path(tmp)
    corpus = make_corpus(tmp / "corpus", docs or {"X001": ("text", BODY)})
    ft = transport or FakeTransport(script)
    out_dir, cache_dir = tmp / "out", tmp / "cache"
    argv = ["--transport", "openai", "--corpus-dir", str(corpus), "--out-dir", str(out_dir),
            "--cache-dir", str(cache_dir)] + (extra or [])
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(pipeline, "make_transport", lambda *a, **k: ft):
        with mock.patch.dict(os.environ, env or {}, clear=False):
            code = pipeline.run(argv, out=out, err=err)
    r = Run()
    r.code, r.out, r.err, r.transport = code, out.getvalue(), err.getvalue(), ft
    r.out_dir, r.cache_dir = out_dir, cache_dir
    r.rules = json.loads((out_dir / "rules.json").read_text())["rules"]
    r.withheld = json.loads((out_dir / "rules_withheld.json").read_text())["withheld_projections"]
    r.report = json.loads((out_dir / "extract_report.json").read_text())
    r.audit = [json.loads(line) for line in (out_dir / "audit_log.jsonl").read_text().splitlines()]
    return r


def rule_events(run):
    return [e for e in run.audit if e["event"] == "rule"]


def schema_errors(rec):
    full, relaxed = assemble.validators(assemble.load_schema())
    return assemble.validate_record(rec, full, relaxed)


# ----------------------------------------------------------- echo end to end

class TestEchoEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "out"
        buf = io.StringIO()
        cls.code = pipeline.run(["--transport", "echo", "--out-dir", str(cls.out)], out=buf, err=io.StringIO())
        cls.stdout = buf.getvalue()
        cls.rules = json.loads((cls.out / "rules.json").read_text())["rules"]
        cls.withheld = json.loads((cls.out / "rules_withheld.json").read_text())["withheld_projections"]
        cls.report = json.loads((cls.out / "extract_report.json").read_text())
        cls.audit = [json.loads(x) for x in (cls.out / "audit_log.jsonl").read_text().splitlines()]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_exit_zero_and_not_a_real_extraction(self):
        self.assertEqual(self.code, 0, self.stdout)
        self.assertFalse(self.report["is_real_extraction"])
        self.assertIn("NOT a real extraction", self.stdout)

    def test_rules_json_shape_and_schema(self):
        raw = json.loads((self.out / "rules.json").read_text())
        self.assertEqual(list(raw), ["rules"])
        self.assertTrue(self.rules)
        for r in self.rules:
            self.assertEqual(schema_errors(r), [], r["team_rule_id"])
            self.assertIn(r["status"], ("in_force", "not_yet_effective", "pending", "failed"))

    def test_ids_unique_and_match_organizer_ids(self):
        ids = [r["team_rule_id"] for r in self.rules + self.withheld]
        self.assertEqual(len(ids), len(set(ids)))
        for want in ("MA-ALG-P1", "MA-ALG-P2", "NJ-ALG-01", "SD-ALG-01", "SF-ALG-01"):
            self.assertIn(want, [r["team_rule_id"] for r in self.rules])
        self.assertEqual({r["team_rule_id"] for r in self.withheld}, {"BERK-ALG-01", "CA-ALG-01"})

    def test_every_quoted_span_verbatim_against_the_distributed_file(self):
        docs = {d.doc_id: d for d in list_documents(ROOT / "corpus")}
        for r in self.rules + self.withheld:
            d = docs[r["provenance"]["doc_id"]]
            self.assertIn(collapse(r["quoted_span"]), collapse(d.raw_text), r["team_rule_id"])
            self.assertIn(collapse(r["quoted_span"]), d.quotable_norm)
            for s in r["supporting_spans"]:
                self.assertIn(s["text"], d.quotable_norm, (r["team_rule_id"], s["id"]))

    def test_summary_only_documents_are_dropped_by_default(self):
        dropped = {(e["doc_id"], tuple(e["drop_reasons"])) for e in self.audit
                   if e["event"] == "rule" and e["outcome"] == "dropped"}
        docs = {d for d, _ in dropped}
        self.assertTrue({"D034", "D059"} <= docs)
        for d, reasons in dropped:
            if d in ("D034", "D059"):
                self.assertTrue(reasons[0].startswith("quoted_span_refused_summary_block"), reasons)
        self.assertNotIn("HOB-ALG-01", [r["team_rule_id"] for r in self.rules])
        self.assertNotIn("MA-RENT-P1", [r["team_rule_id"] for r in self.rules])

    def test_duplicate_pending_measure_collapsed(self):
        dup = [e for e in self.audit if e["event"] == "rule" and e["doc_id"] == "D047"][0]
        self.assertEqual(dup["outcome"], "dropped")
        self.assertTrue(dup["drop_reasons"][0].startswith("duplicate_of:D046"))

    def test_audit_log_contents(self):
        run = self.audit[0]
        self.assertEqual(run["event"], "run")
        self.assertEqual(run["manifest_hash_scope"], "unknown")
        names = [e["name"] for e in self.audit if e["event"] == "known_gap"]
        self.assertIn("SB 763", names)
        for e in self.audit:
            if e["event"] == "document":
                for k in ("doc_id", "url", "retrieved_at", "text_file_sha256_local", "prompt_version",
                          "raw_model_output", "manifest_hash_scope"):
                    self.assertIn(k, e)
                self.assertNotIn("sha256", e)          # the ambiguous key must not exist
        for e in self.audit:
            if e["event"] == "rule":
                for k in ("doc_id", "url", "retrieved_at", "text_file_sha256_local", "prompt_version",
                          "raw_model_output", "normalizations", "span_verification", "drop_reasons"):
                    self.assertIn(k, e)

    def test_manifest_hash_is_never_presented_as_validating(self):
        for r in self.rules + self.withheld:
            self.assertEqual(r["provenance"]["manifest_hash_scope"], "unknown")
            self.assertIn("text_file_sha256_local", r["provenance"])
            self.assertNotEqual(r["provenance"]["text_file_sha256_local"],
                                r["provenance"]["manifest_sha256_reported_unverified"])

    def test_eight_shells_empty_but_not_model_confirmed_under_echo(self):
        chk = [c for c in self.report["acceptance"]["checks"] if c["name"].startswith("shell_empty:")]
        self.assertEqual(len(chk), 8)
        self.assertTrue(all(c["passed"] for c in chk))
        self.assertFalse(self.report["acceptance"]["model_confirmed"])
        self.assertTrue(all("NOT confirmed by a model" in c["detail"] for c in chk))

    def test_la_zero_algorithmic_and_no_sb763(self):
        self.assertFalse([r for r in self.rules + self.withheld if r["jurisdiction"] == "Los Angeles, CA"])
        names = {c["name"]: c for c in self.report["acceptance"]["checks"]}
        self.assertTrue(names["la_zero_algorithmic_rent_setting"]["passed"])
        self.assertTrue(names["no_sb763_record"]["passed"])
        self.assertFalse([r for r in self.rules + self.withheld if "763" in r["citation"]])

    def test_all_acceptance_checks_pass_on_fixtures(self):
        self.assertTrue(self.report["acceptance"]["all_passed"], self.report["acceptance"]["failed"])

    def test_the_five_over_reading_protections_on_fixtures(self):
        by = {r["team_rule_id"]: r for r in self.rules + self.withheld}
        d022, d001 = by["CA-ALG-01"], by["BERK-ALG-01"]
        # D022: no operative date, no status, applicability inferred, scope not universal
        self.assertIsNone(d022["effective_date"])
        self.assertIsNone(d022["status"])
        self.assertEqual(d022["epistemics"]["rental_housing_applicability"]["category"], "inferred")
        self.assertTrue(d022["coverage_conditions"]["needs_fact"])
        blob = " ".join(s for s in [d022["quoted_span"]] + [x["text"] for x in d022["supporting_spans"]])
        for s in ("CHAPTER 338", "October 06, 2025", "10/06/25 - Chaptered"):
            self.assertIn(s, blob)
        # D001: unresolved, both external dates named as unverified
        self.assertIsNone(d001["effective_date"])
        self.assertTrue(d001["conflict_flag"])
        self.assertIn("2026-03-01", d001["conflict_note"])
        self.assertIn("2026-01", d001["conflict_note"])
        self.assertEqual(len(d001["external_unverified_claims"]), 2)
        self.assertFalse(any(c["verified"] for c in d001["external_unverified_claims"]))
        # D069: exact sentence, limits recorded, enacting not expanded
        nj = by["NJ-ALG-01"]
        spans_blob = collapse(" ".join(x["text"] for x in nj["supporting_spans"]))
        self.assertIn(acceptance.NJ_SENTENCE_1, spans_blob)
        self.assertIn(acceptance.NJ_SENTENCE_2, spans_blob)
        self.assertEqual((nj["status"], nj["effective_date"]), ("not_yet_effective", "2027-07-01"))
        self.assertEqual(nj["epistemics"]["effective_date"]["category"], "derived")
        # D076: not passed, definitions flagged
        sd = by["SD-ALG-01"]
        self.assertEqual(sd["status"], "pending")
        self.assertIsNone(sd["effective_date"])
        self.assertIn("98.0702", json.dumps(sd["coverage_conditions"]))
        # D081: summary marked at the point of use
        sf = by["SF-ALG-01"]
        self.assertIn("not the legal text", sf["title"])
        self.assertIn("not the legal text", sf["requirement"])
        self.assertIn("not the legal text", sf["citation"])
        self.assertLessEqual(sf["confidence"], 0.6)

    def test_withheld_projections_are_not_in_rules_and_fail_the_strict_schema(self):
        full, relaxed = assemble.validators(assemble.load_schema())
        for r in self.withheld:
            self.assertFalse(r["projection_valid"])
            self.assertTrue(r["conflict_flag"])
            self.assertEqual(r["status_basis"], "unknown")
            self.assertFalse(r["status_establishable"])
            self.assertTrue(list(full.iter_errors(r)))        # status null is not schema-valid
            self.assertFalse(list(relaxed.iter_errors(r)))

    def test_engine_accepts_the_output_and_never_sees_a_withheld_rule(self):
        precedence.build_plan(self.rules)
        for st, place in (("NJ", "Newark, NJ"), ("CA", "Berkeley, CA"), ("MA", "Boston, MA")):
            entries = lookup.resolve({"address_id": "a", "state": st, "jurisdiction": place,
                                      "year_built": 1990, "units": 12}, self.rules, "2026-10-01")
            ids = [e["team_rule_id"] for e in entries]
            self.assertNotIn("CA-ALG-01", ids)
            self.assertNotIn("BERK-ALG-01", ids)
        nj = {e["team_rule_id"]: e for e in lookup.resolve(
            {"address_id": "a", "state": "NJ", "jurisdiction": "Newark, NJ", "year_built": 1990, "units": 12},
            self.rules, "2026-10-01")}
        self.assertEqual(nj["NJ-ALG-01"]["result"], "not_yet_effective")


# --------------------------------------------------------------- span checks

class TestSpanVerification(unittest.TestCase):
    def test_whitespace_collapse_only(self):
        with tempfile.TemporaryDirectory() as t:
            make_corpus(t, {"X001": ("text", "Alpha beta\n   gamma   delta epsilon zeta eta theta\niota kappa lambda mu\n")})
            d = list_documents(t)[0]
            self.assertEqual(spans.verify_span("gamma delta\nepsilon zeta eta theta iota", d, 20)["result"], "verified")
            # case, punctuation and quote style are NOT folded
            self.assertEqual(spans.verify_span("GAMMA delta epsilon zeta eta theta iota", d, 20)["result"], "not_found")

    def test_curly_quote_is_not_folded(self):
        with tempfile.TemporaryDirectory() as t:
            make_corpus(t, {"X001": ("text", "The term \u201ccommon pricing algorithm\u201d means any methodology used by two persons.\n")})
            d = list_documents(t)[0]
            self.assertEqual(spans.verify_span("The term \"common pricing algorithm\" means any methodology", d, 20)["result"], "not_found")
            self.assertEqual(spans.verify_span("The term \u201ccommon pricing algorithm\u201d means any methodology", d, 20)["result"], "verified")

    def test_header_is_not_quotable(self):
        with tempfile.TemporaryDirectory() as t:
            make_corpus(t, {"X001": ("text", BODY)})
            d = list_documents(t)[0]
            self.assertEqual(spans.verify_span("SOURCE: https://example.org/X001", d, 20)["result"], "refused_header")

    def test_summary_region_is_refused_and_pre_marker_text_is_quotable(self):
        body = ("Real text before the marker says the landlord shall post a notice.\n\n"
                "[SUMMARY, NOT SOURCE TEXT]\n"
                "\"The landlord must post a notice within ten days of the change.\"\n")
        with tempfile.TemporaryDirectory() as t:
            make_corpus(t, {"X001": ("fetched", body)})
            d = list_documents(t)[0]
            self.assertTrue(d.has_summary_region)
            self.assertEqual(spans.verify_span("the landlord shall post a notice.", d, 20)["result"], "verified")
            self.assertEqual(spans.verify_span("The landlord must post a notice within ten days", d, 20)["result"],
                             "refused_summary_block")

    def test_every_real_fetched_file_has_no_quotable_text(self):
        """Documents the strict policy: WebFetch captures are summary end to end."""
        docs = [d for d in list_documents(ROOT / "corpus") if "/fetched/" in d.rel_path]
        self.assertEqual(len(docs), 20)
        for d in docs:
            self.assertFalse(d.has_quotable_text, d.doc_id)


class TestRetryAndDrop(unittest.TestCase):
    def test_bad_span_is_caught_retried_once_then_dropped(self):
        bad = good_rule(quoted_span="A landlord must never exceed one half month of rent as a deposit amount.")
        def script(doc, step, msgs):
            if step == "initial":
                return reply([bad])
            if step == "repair":
                return {"repairs": [{"record_index": 0, "span_id": "quoted_span",
                                     "text": "A landlord must never exceed one half month of rent as a deposit total."}]}
            raise AssertionError(step)
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, script)
        self.assertEqual([s for _, s in r.transport.calls], ["initial", "repair"])
        self.assertEqual(r.rules, [])
        ev = rule_events(r)[0]
        self.assertEqual(ev["outcome"], "dropped")
        self.assertTrue(ev["drop_reasons"][0].startswith("quoted_span_not_found"))
        attempts = ev["span_verification"][0]["attempts"]
        self.assertEqual([a["attempt"] for a in attempts], [1, 2])
        self.assertEqual([a["result"] for a in attempts], ["not_found", "not_found"])

    def test_retry_that_recopies_correctly_keeps_the_record(self):
        bad = good_rule(quoted_span="A landlord shall not charge a deposit above one month rent for any dwelling.")
        def script(doc, step, msgs):
            if step == "initial":
                return reply([bad])
            return {"repairs": [{"record_index": 0, "span_id": "quoted_span", "text": SPAN_MAIN}]}
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, script)
        self.assertEqual(len(r.rules), 1)
        self.assertEqual(r.rules[0]["quoted_span"], SPAN_MAIN)
        att = rule_events(r)[0]["span_verification"][0]["attempts"]
        self.assertEqual([a["result"] for a in att], ["not_found", "verified"])

    def test_a_good_record_triggers_no_repair_call(self):
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, lambda d, s, m: reply([good_rule()]))
        self.assertEqual([s for _, s in r.transport.calls], ["initial"])
        self.assertEqual(len(r.rules), 1)

    def test_unverified_supporting_span_is_removed_and_stated_field_lowered(self):
        rec = good_rule(supporting_spans=[{"id": "s1", "text": "This chapter takes effect on the first of April 2030.",
                                           "supports": ["effective_date"]}])
        def script(doc, step, msgs):
            return reply([rec]) if step == "initial" else {"repairs": []}
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, script)
        self.assertEqual(len(r.rules) + len(r.withheld), 1)
        rule = (r.rules + r.withheld)[0]
        self.assertEqual(rule["supporting_spans"], [])
        self.assertIsNone(rule["effective_date"])                  # no verified span, so no date
        self.assertEqual(rule["epistemics"]["effective_date"]["category"], "unknown")
        self.assertNotEqual(rule["epistemics"]["status"]["category"], "stated")


class TestSummaryRefusal(unittest.TestCase):
    BODY = ("[SUMMARY, NOT SOURCE TEXT]\n"
            "Hoboken: \"Landlords in the City of Hoboken are prohibited from price fixing using algorithmic pricing.\"\n")

    def rec(self):
        return good_rule(jurisdiction="Hoboken, NJ", category="algorithmic_rent_setting",
                         quoted_span="Landlords in the City of Hoboken are prohibited from price fixing using algorithmic pricing.",
                         supporting_spans=[], effective_date=None, status_basis="inferred",
                         epistemics={}, instrument_type="ordinance", source_kind="secondary_commentary")

    def script(self, doc, step, msgs):
        return reply([self.rec()]) if step == "initial" else {"repairs": []}

    def test_default_policy_drops_a_span_drawn_from_a_summary_block(self):
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, self.script, docs={"X001": ("fetched", self.BODY)})
        self.assertEqual(r.rules, [])
        self.assertEqual(r.withheld, [])
        ev = rule_events(r)[0]
        self.assertEqual(ev["outcome"], "dropped")
        self.assertIn("quoted_span_refused_summary_block", ev["drop_reasons"][0])
        self.assertIn("summary_only_support", ev["drop_reasons"][0])
        self.assertEqual(ev["span_verification"][0]["final_result"], "refused_summary_block")

    def test_flag_policy_keeps_it_as_summary_only_with_low_confidence_and_conflict_flag(self):
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, self.script, docs={"X001": ("fetched", self.BODY)}, extra=["--summary-policy", "flag"])
        self.assertEqual(len(r.rules), 1)
        rule = r.rules[0]
        self.assertEqual(rule["provenance_grade"], "summary_only")
        self.assertEqual(rule["quoted_span_kind"], "summary_not_source_text")
        self.assertTrue(rule["conflict_flag"])
        self.assertLessEqual(rule["confidence"], 0.4)
        self.assertIn("fetch-tool summary", rule["title"])
        self.assertIsNone(rule["effective_date"])
        for f in ("status", "requirement"):
            self.assertNotEqual(rule["epistemics"][f]["category"], "stated")


# ------------------------------------------------------------ zero-rule docs

class TestNoLegalRequirement(unittest.TestCase):
    def test_document_with_no_requirement_yields_zero_records(self):
        nav = "Home | About | Contact | Rent Board forms and publications | Search this site\n" * 3
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, lambda d, s, m: reply([], kind="navigation_or_index", states=False),
                             docs={"X001": ("text", nav)})
        self.assertEqual(r.rules, [])
        self.assertEqual(r.withheld, [])
        self.assertEqual(rule_events(r), [])
        self.assertEqual(r.code, 0)

    def test_a_rule_returned_from_a_navigation_document_is_flagged(self):
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, lambda d, s, m: reply([good_rule()], kind="navigation_or_index", states=False),
                             docs={"X001": ("text", BODY)})
        rule = (r.rules + r.withheld)[0]
        self.assertTrue(rule["conflict_flag"])
        self.assertIn("navigation_or_index", rule["conflict_note"])

    def test_la_motion_cannot_produce_an_algorithmic_rule_even_if_a_model_tries(self):
        rec = good_rule(jurisdiction="Los Angeles, CA", category="algorithmic_rent_setting",
                        title="LA algorithm ban", status="pending", status_basis="derived", effective_date=None)
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, lambda d, s, m: reply([rec]) if s == "initial" else {"repairs": []})
        self.assertEqual(r.rules, [])
        self.assertEqual(r.withheld, [])
        self.assertEqual(r.code, 3)
        self.assertEqual(len(r.report["invariant_violations"]), 1)
        self.assertIn("invariant_violation", rule_events(r)[0]["drop_reasons"][0])

    def test_failed_document_is_an_error_not_an_empty_answer(self):
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, lambda d, s, m: transports.TransportError("boom"))
        self.assertEqual(r.report["documents_errored"], ["X001"])
        self.assertEqual(r.code, 1)

    def test_non_json_output_is_retried_once_then_reported(self):
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, lambda d, s, m: "this is not json")
        self.assertEqual([s for _, s in r.transport.calls], ["initial", "json_retry"])
        self.assertEqual(r.report["documents_errored"], ["X001"])


# ------------------------------------------------------- epistemic categories

class TestFourCategories(unittest.TestCase):
    def run_one(self, docs=None, **over):
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, lambda d, s, m: reply([good_rule(**over)]) if s == "initial" else {"repairs": []},
                             docs=docs)
            # reload from disk: the round trip is the file, not memory
            rules = json.loads((r.out_dir / "rules.json").read_text())["rules"]
            held = json.loads((r.out_dir / "rules_withheld.json").read_text())["withheld_projections"]
        return (rules + held)[0]

    def test_all_four_survive_a_round_trip_through_the_file(self):
        rec = self.run_one()
        cats = {f: e["category"] for f, e in rec["epistemics"].items()}
        self.assertEqual(cats["jurisdiction"], "stated")
        self.assertEqual(cats["requirement"], "derived")
        self.assertEqual(cats["title"], "inferred")
        self.assertEqual(cats["exemptions"], "unknown")
        self.assertEqual({"stated", "derived", "inferred", "unknown"} <= set(cats.values()), True)
        self.assertEqual(rec["epistemics"]["requirement"]["derivation_rule"], "PARAPHRASE")
        self.assertIsNone(rec["epistemics"]["jurisdiction"]["derivation_rule"])
        self.assertEqual(rec["status_basis"], "derived")
        self.assertEqual(rec["epistemics"]["effective_date"]["category"], "stated")

    def test_every_substantive_field_carries_a_category(self):
        rec = self.run_one()
        for f in ("jurisdiction", "level", "category", "status", "title", "requirement", "key_value",
                  "coverage_conditions", "exemptions", "overrides", "interaction", "effective_date",
                  "citation", "rental_housing_applicability"):
            self.assertIn(f, rec["epistemics"])
            self.assertIn(rec["epistemics"][f]["category"], ("stated", "derived", "inferred", "unknown"))

    def test_stated_without_a_verified_span_is_lowered_to_inferred(self):
        ep = good_rule()["epistemics"]
        ep["key_value"] = epi("stated", spans_=())
        rec = self.run_one(epistemics=ep)
        self.assertEqual(rec["epistemics"]["key_value"]["category"], "inferred")

    def test_derived_without_a_named_rule_is_lowered_to_inferred(self):
        ep = good_rule()["epistemics"]
        ep["requirement"] = epi("derived", rule=None)
        rec = self.run_one(epistemics=ep)
        self.assertEqual(rec["epistemics"]["requirement"]["category"], "inferred")

    def test_no_value_is_always_unknown_whatever_the_model_claimed(self):
        ep = good_rule()["epistemics"]
        ep["key_value"] = epi("stated")
        rec = self.run_one(key_value=None, epistemics=ep)
        self.assertEqual(rec["epistemics"]["key_value"]["category"], "unknown")

    def test_missing_epistemics_never_default_to_stated(self):
        rec = self.run_one(epistemics={})
        for f in ("requirement", "title", "coverage_conditions"):
            self.assertNotEqual(rec["epistemics"][f]["category"], "stated")

    def test_inferred_rental_applicability_makes_scope_not_universal(self):
        ep = good_rule()["epistemics"]
        ep["rental_housing_applicability"] = epi("stated")        # model claims stated ...
        general = BODY + "General provision: A person shall not use a common pricing algorithm to restrain trade or commerce.\n"
        rec = self.run_one(docs={"X001": ("text", general)},
                           quoted_span="A person shall not use a common pricing algorithm to restrain trade or commerce.",
                           supporting_spans=[], effective_date=None, status=None,
                           status_establishable=False, status_basis="unknown", epistemics=ep)
        # ... but the span has no rental or housing words, so the pipeline refuses to call it stated
        self.assertEqual(rec["epistemics"]["rental_housing_applicability"]["category"], "inferred")
        self.assertTrue(any(n.startswith("rental_housing_applicability") for n in rec["coverage_conditions"]["needs_fact"]))


class TestCoverageNeverWidens(unittest.TestCase):
    def test_free_text_and_bad_predicates_land_in_needs_fact(self):
        norms = []
        out = records.normalize_coverage("Owners of more than four units only", norms)
        self.assertEqual(out["predicates"], [])
        self.assertTrue(out["needs_fact"][0].startswith("unparsed_coverage_condition"))
        out = records.normalize_coverage({"predicates": [
            {"fact": "owner_type", "op": "eq", "value": "corporate"},
            {"fact": "units", "op": "gte", "value": 5},
            {"fact": "year_built", "op": "lte", "value": "1979-06-13", "basis": "certificate_of_occupancy"},
            {"fact": "units", "op": "weird", "value": 1}], "needs_fact": ["owner_unit_count"], "note": "x"}, norms)
        self.assertEqual(len(out["predicates"]), 2)
        self.assertEqual(out["predicates"][1]["basis"], "certificate_of_occupancy")
        self.assertEqual(sum(n.startswith("unparseable_coverage_predicate") for n in out["needs_fact"]), 2)
        self.assertIn("owner_unit_count", out["needs_fact"])


# ------------------------------------------------------ status never invented

class TestStatusNeverInvented(unittest.TestCase):
    def run_one(self, **over):
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, lambda d, s, m: reply([good_rule(**over)]) if s == "initial" else {"repairs": []})
        return r

    def assert_withheld(self, r):
        self.assertEqual(r.rules, [], "a withheld projection leaked into rules.json")
        self.assertEqual(len(r.withheld), 1)
        w = r.withheld[0]
        self.assertIsNone(w["status"])
        self.assertIsNone(w["effective_date"])
        self.assertTrue(w["conflict_flag"])
        self.assertTrue(w["conflict_note"])
        self.assertFalse(w["status_establishable"])
        self.assertEqual(w["status_basis"], "unknown")
        self.assertEqual(w["epistemics"]["status"]["category"], "unknown")
        self.assertEqual(w["epistemics"]["effective_date"]["category"], "unknown")
        self.assertFalse(w["projection_valid"])
        return w

    def test_model_says_not_establishable_but_supplies_pending(self):
        w = self.assert_withheld(self.run_one(status="pending", status_establishable=False, status_basis="unknown"))
        ev = rule_events(self.run_one(status="pending", status_establishable=False, status_basis="unknown"))[0]
        self.assertTrue(any(n["field"] == "status" and n["before"] == "pending" and n["after"] is None
                            for n in ev["normalizations"]))
        self.assertEqual(w["provenance"]["candidate_index"], 0)

    def test_model_supplies_in_force_with_unknown_basis(self):
        self.assert_withheld(self.run_one(status="in_force", status_establishable=True, status_basis="unknown"))

    def test_model_supplies_null_status(self):
        self.assert_withheld(self.run_one(status=None, status_establishable=False, status_basis="unknown"))

    def test_invented_status_word_is_not_accepted(self):
        self.assert_withheld(self.run_one(status="indeterminate", status_establishable=True, status_basis="inferred"))

    def test_status_resting_on_the_engine_owned_default_rule_is_withheld(self):
        ep = good_rule()["epistemics"]
        ep["status"] = epi("derived", spans_=("s1",), rule="California default effective date: January 1 following enactment")
        w = self.assert_withheld(self.run_one(epistemics=ep))
        self.assertIn("engine", w["projection_withheld_reason"])

    def test_withheld_status_forces_date_null_even_if_model_gave_one(self):
        w = self.assert_withheld(self.run_one(status=None, status_establishable=False, status_basis="unknown",
                                              effective_date="2026-03-01"))
        self.assertIsNone(w["effective_date"])

    def test_empty_conflict_note_is_filled_for_a_withheld_record(self):
        w = self.assert_withheld(self.run_one(status=None, status_establishable=False, status_basis="unknown",
                                              conflict_flag=False, conflict_note=None))
        self.assertIn("not establishable", w["conflict_note"])

    def test_establishable_status_stays_substantive_and_is_not_over_withheld(self):
        r = self.run_one()
        self.assertEqual(len(r.rules), 1)
        self.assertEqual(r.rules[0]["status"], "in_force")
        self.assertTrue(r.rules[0]["status_establishable"])
        self.assertEqual(r.withheld, [])


class TestEffectiveDateGuards(unittest.TestCase):
    def run_one(self, **over):
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, lambda d, s, m: reply([good_rule(**over)]) if s == "initial" else {"repairs": []})
        return (r.rules + r.withheld)[0]

    def test_engine_owned_california_default_is_refused_in_extraction(self):
        ep = good_rule()["epistemics"]
        ep["effective_date"] = epi("derived", spans_=("s1",), rule=RULE_CA_DEFAULT)
        rec = self.run_one(effective_date="2027-01-01", epistemics=ep)
        self.assertIsNone(rec["effective_date"])
        self.assertEqual(rec["epistemics"]["effective_date"]["category"], "unknown")

    def test_description_style_default_rule_also_refused(self):
        ep = good_rule()["epistemics"]
        ep["effective_date"] = epi("derived", spans_=("s1",), rule="CA default operative date for chaptered statutes")
        self.assertIsNone(self.run_one(effective_date="2027-01-01", epistemics=ep)["effective_date"])

    def test_stated_date_must_appear_in_the_cited_span(self):
        rec = self.run_one(effective_date="2026-04-01")
        self.assertIsNone(rec["effective_date"])
        self.assertEqual(rec["epistemics"]["effective_date"]["category"], "unknown")

    def test_inferred_date_is_not_carried(self):
        ep = good_rule()["epistemics"]
        ep["effective_date"] = epi("inferred", spans_=("s1",))
        self.assertIsNone(self.run_one(epistemics=ep)["effective_date"])

    def test_stated_date_in_other_formats_is_recognised(self):
        fulls, months, years = records.dates_in_text("approved 10/06/25; effective Oct. 6, 2025; 6 October 2025; 2026-03-01; June 2025")
        self.assertIn((2025, 10, 6), fulls)
        self.assertIn((2026, 3, 1), fulls)
        self.assertIn((2025, 6), months)
        self.assertTrue(records.date_supported("2025-10-06", "Approved October 06, 2025."))
        self.assertTrue(records.date_supported("2025-06", "effective in June 2025"))
        self.assertFalse(records.date_supported("2025-06-15", "effective in June 2025"))

    def test_bad_calendar_date_is_dropped(self):
        self.assertIsNone(self.run_one(effective_date="2026-02-31")["effective_date"])


class TestSourceKindMarking(unittest.TestCase):
    def test_secondary_commentary_is_marked_and_capped(self):
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, lambda d, s, m: reply([good_rule(source_kind="secondary_commentary", confidence=0.95)])
                             if s == "initial" else {"repairs": []})
        rec = (r.rules + r.withheld)[0]
        self.assertIn("[secondary source, not the legal text]", rec["title"])
        self.assertTrue(rec["requirement"].startswith("Per a secondary source"))
        self.assertLessEqual(rec["confidence"], 0.6)


# ----------------------------------------------------------- relations / ids

class TestAssembly(unittest.TestCase):
    def test_relations_resolve_to_ids_and_withheld_targets_do_not_leak(self):
        a = good_rule(jurisdiction="NJ", category="algorithmic_rent_setting", title="State rule",
                      relations=[{"interaction": "preempts_pending", "target_jurisdiction": "Newark, NJ",
                                  "target_category": "algorithmic_rent_setting", "category": "inferred",
                                  "basis": "b", "span_ids": []}])
        b = good_rule(jurisdiction="Newark, NJ", category="algorithmic_rent_setting", title="City rule")
        c = good_rule(jurisdiction="Hoboken, NJ", category="algorithmic_rent_setting", title="Held",
                      status=None, status_establishable=False, status_basis="unknown")
        a["relations"].append({"interaction": "preempts_pending", "target_jurisdiction": "Hoboken, NJ",
                               "target_category": "algorithmic_rent_setting", "category": "inferred",
                               "basis": "b", "span_ids": []})
        docs = {"X001": ("text", BODY), "X002": ("text", BODY), "X003": ("text", BODY)}
        def script(doc, step, msgs):
            if step != "initial":
                return {"repairs": []}
            return reply([{"X001": a, "X002": b, "X003": c}[doc]])
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, script, docs=docs)
        by = {x["team_rule_id"]: x for x in r.rules}
        self.assertEqual(set(by), {"NJ-ALG-01", "NWK-ALG-01"})
        nj = by["NJ-ALG-01"]
        self.assertEqual((nj["overrides"], nj["interaction"]), (["NWK-ALG-01"], "preempts_pending"))
        self.assertEqual(nj["epistemics"]["overrides"]["category"], "inferred")
        self.assertEqual([u["reason"] for u in nj["relations_unresolved"]], ["target_is_a_withheld_projection"])
        precedence.build_plan(r.rules)                      # engine accepts it, no unknown-id warnings
        self.assertEqual(precedence.build_plan(r.rules).warnings, [])

    def test_bills_use_the_pending_series_and_others_use_nn(self):
        bill = good_rule(jurisdiction="MA", category="algorithmic_rent_setting", instrument_type="bill",
                         status="pending", effective_date=None, citation="H.1", title="Bill")
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, lambda d, s, m: reply([bill, good_rule()]) if s == "initial" else {"repairs": []})
        self.assertEqual({x["team_rule_id"] for x in r.rules}, {"MA-ALG-P1", "T-DEP-01"})


# -------------------------------------------------- transports, cache, secrets

class TestTransportAndSecrets(unittest.TestCase):
    def test_missing_openai_package_prints_exact_pip_line_and_exits_2(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(sys.modules, {"openai": None}):
            code = pipeline.run(["--transport", "openai", "--docs", "D022"], out=out, err=err)
        self.assertEqual(code, 2)
        self.assertIn("python3 -m pip install openai", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue() + out.getvalue())

    def test_missing_openai_package_via_main_has_no_traceback(self):
        err = io.StringIO()
        with mock.patch.dict(sys.modules, {"openai": None}), contextlib.redirect_stderr(err):
            code = pipeline.main(["--transport", "openai", "--docs", "D022"])
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err.getvalue())

    def test_missing_key_exits_2_without_echoing_anything_secret(self):
        fake_openai = mock.MagicMock()
        out, err = io.StringIO(), io.StringIO()
        env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
        with mock.patch.dict(sys.modules, {"openai": fake_openai}), mock.patch.dict(os.environ, env, clear=True):
            code = pipeline.run(["--transport", "openai", "--docs", "D022"], out=out, err=err)
        self.assertEqual(code, 2)
        self.assertIn("OPENAI_API_KEY is not set", err.getvalue())
        self.assertIn("Never write the key to a file", err.getvalue())
        fake_openai.OpenAI.assert_not_called()

    def test_openai_is_the_default_transport(self):
        import argparse
        src = (ROOT / "navigator" / "extract" / "pipeline.py").read_text()
        self.assertIn('default="openai"', src)
        self.assertEqual(transports.DEFAULT_MODELS["openai"], "gpt-5")

    def test_key_is_read_from_the_environment_only(self):
        fake = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"openai": fake}), mock.patch.dict(os.environ, {"OPENAI_API_KEY": CANARY}):
            transports.OpenAITransport()
        args, kwargs = fake.OpenAI.call_args
        self.assertNotIn("api_key", kwargs)
        self.assertNotIn(CANARY, repr(args) + repr(kwargs))

    def test_vendor_error_text_is_scrubbed(self):
        class AuthenticationError(Exception):
            pass
        fake = mock.MagicMock()
        fake.OpenAI.return_value.chat.completions.create.side_effect = AuthenticationError(
            "Incorrect API key provided: %s. You can find your key at ..." % CANARY)
        with mock.patch.dict(sys.modules, {"openai": fake}), mock.patch.dict(os.environ, {"OPENAI_API_KEY": CANARY}):
            tr = transports.OpenAITransport()
            with self.assertRaises(transports.FatalTransportError) as cm:
                tr.complete("sys", [{"role": "user", "content": "x"}], "X001", "initial")
        self.assertNotIn(CANARY, str(cm.exception))
        self.assertIn("[REDACTED]", str(cm.exception))

    def test_rejected_credentials_stop_the_run_and_write_nothing(self):
        def script(doc, step, msgs):
            return transports.FatalTransportError("AuthenticationError: bad key %s" % "[REDACTED]")
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            make_corpus(tmp / "corpus", {"X001": ("text", BODY)})
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(pipeline, "make_transport", lambda *a, **k: FakeTransport(script)):
                code = pipeline.run(["--corpus-dir", str(tmp / "corpus"), "--out-dir", str(tmp / "out"),
                                     "--cache-dir", str(tmp / "cache")], out=out, err=err)
            self.assertEqual(code, 2)
            self.assertFalse((tmp / "out" / "rules.json").exists())

    def test_no_key_is_ever_written_to_any_output_or_cache_file(self):
        def script(doc, step, msgs):
            if doc == "X002":
                return transports.TransportError("RateLimitError: key %s exhausted" % CANARY)
            rec = good_rule(requirement="Echoed credential %s must not survive." % CANARY,
                            conflict_note="token Bearer abcdefghijklmnopqrstuvwxyz0123456789 here")
            return reply([rec]) if step == "initial" else {"repairs": []}
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, script, docs={"X001": ("text", BODY), "X002": ("text", BODY)},
                             env={"OPENAI_API_KEY": CANARY})
            blobs = []
            for base in (r.out_dir, r.cache_dir):
                for p in Path(base).rglob("*"):
                    if p.is_file():
                        blobs.append(p.read_text(errors="replace"))
            blobs.append(r.out)
            blobs.append(r.err)
        joined = "\n".join(blobs)
        self.assertFalse(CANARY in joined, "the key appears in an output or cache file")
        self.assertFalse("CANARYCANARY" in joined)
        self.assertIsNone(re.search(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{16,}", joined))
        self.assertFalse("abcdefghijklmnopqrstuvwxyz0123456789" in joined)
        self.assertGreater(r.report["redactions_applied"], 0)

    def test_redactor_unit(self):
        red = Redactor()
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "plainvaluewithnoprefix123"}):
            s = red.text("a sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV b plainvaluewithnoprefix123 c")
        self.assertNotIn("ABCDEFGH", s)
        self.assertNotIn("plainvaluewithnoprefix123", s)
        self.assertEqual(red.obj({"k": ["sk-ZZZZZZZZZZZZZZZZZZZZ"]}), {"k": ["[REDACTED]"]})


class TestCacheAndConcurrency(unittest.TestCase):
    def test_cache_hit_then_no_cache(self):
        with tempfile.TemporaryDirectory() as t:
            script = lambda d, s, m: reply([good_rule()]) if s == "initial" else {"repairs": []}
            r1 = run_pipeline(t, script)
            n1 = len(r1.transport.calls)
            r2 = run_pipeline(t, script)
            self.assertEqual(len(r2.transport.calls), 0, "second run must be served from the cache")
            self.assertEqual(r2.report["cache_hits"], 1)
            self.assertEqual(r1.rules, r2.rules)
            r3 = run_pipeline(t, script, extra=["--no-cache"])
            self.assertEqual(len(r3.transport.calls), n1)
            self.assertEqual(r3.report["cache_hits"], 0)

    def test_cache_files_hold_model_output_only(self):
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, lambda d, s, m: reply([good_rule()]))
            files = list(Path(r.cache_dir).glob("*.json"))
            self.assertEqual(len(files), 1)
            data = json.loads(files[0].read_text())
            self.assertEqual(set(data), {"key", "format", "steps"})

    def test_echo_transport_never_uses_the_cache(self):
        with tempfile.TemporaryDirectory() as t:
            cache = Path(t) / "cache"
            pipeline.run(["--transport", "echo", "--docs", "D022", "--out-dir", str(Path(t) / "o"),
                          "--cache-dir", str(cache)], out=io.StringIO(), err=io.StringIO())
            self.assertFalse(cache.exists())

    def test_concurrency_is_bounded_and_real(self):
        docs = {"X%03d" % i: ("text", BODY) for i in range(12)}
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, lambda d, s, m: reply([]), docs=docs)
        self.assertLessEqual(r.transport.max_active, 4)
        self.assertGreater(r.transport.max_active, 1)

    def test_concurrency_flag_range_enforced(self):
        err = io.StringIO()
        code = pipeline.run(["--transport", "echo", "--concurrency", "99", "--docs", "D022"],
                            out=io.StringIO(), err=err)
        self.assertEqual(code, 2)


class TestGenericPrompt(unittest.TestCase):
    def test_same_prompt_for_every_document_and_transport(self):
        docs = {"X001": ("text", BODY), "X002": ("text", "Other text entirely, about nothing at all in particular.\n")}
        with tempfile.TemporaryDirectory() as t:
            r = run_pipeline(t, lambda d, s, m: reply([]), docs=docs)
        self.assertEqual(len(set(r.transport.systems)), 1)
        text = (ROOT / "navigator" / "extract" / "prompt.md").read_text()
        self.assertEqual(r.transport.systems[0], text)
        self.assertTrue(text.startswith("PROMPT_VERSION:"))

    def test_prompt_tells_the_model_the_critical_rules(self):
        text = (ROOT / "navigator" / "extract" / "prompt.md").read_text()
        for needle in ("[SUMMARY, NOT SOURCE TEXT]", "\"rules\": []", "status_establishable", "NEVER apply a default effective-date rule",
                       "stated", "derived", "inferred", "unknown", "yields_to", "preempts_pending", "stacks",
                       "rental_housing_applicability"):
            self.assertIn(needle, text, needle)

    def test_prompt_and_package_are_ascii_without_em_dashes(self):
        for p in list((ROOT / "navigator" / "extract").rglob("*")) + list((ROOT / "navigator" / "normalize").rglob("*")):
            if p.is_file() and p.suffix in (".py", ".md", ".json"):
                data = p.read_bytes()
                self.assertTrue(all(b < 128 for b in data), "non-ASCII byte in %s" % p)

    def test_no_per_document_branching_in_the_extraction_code(self):
        """Document ids may appear only in acceptance.py, which holds expectations as data."""
        pat = re.compile(r"\bD\d{3}\b|SUPP_")
        for p in (ROOT / "navigator" / "extract").glob("*.py"):
            if p.name == "acceptance.py":
                continue
            self.assertIsNone(pat.search(p.read_text()), "document id in %s" % p.name)

    def test_no_absolute_machine_paths_in_deliverables(self):
        pat = re.compile(r"(/Users/|/home/|/sessions/|C:\\\\)")
        for p in list((ROOT / "navigator" / "extract").rglob("*")) + list((ROOT / "navigator" / "normalize").rglob("*")):
            if p.is_file() and p.suffix in (".py", ".md", ".json"):
                self.assertIsNone(pat.search(p.read_text()), "machine path in %s" % p)


# --------------------------------------------------- effective_date module

class TestEffectiveDateModule(unittest.TestCase):
    def test_chaptered_2025_with_no_stated_date_gives_jan_1_2026_and_a_derivation_record(self):
        date, rec = derive_effective_date("2025-10-06")
        self.assertEqual(date, "2026-01-01")
        self.assertEqual(rec["rule_id"], RULE_CA_DEFAULT)
        self.assertTrue(rec["applied"] and rec["derived"])
        self.assertEqual(rec["category"], "derived")
        self.assertEqual(rec["output"], "2026-01-01")
        self.assertEqual(rec["inputs"]["enacted_on"], "2025-10-06")
        self.assertIs(rec["corpus_support"], False)
        self.assertIn("Cal. Const. art. IV, sec. 8(c)(1)", rec["legal_basis_cited_unverified"])
        self.assertIn("CONTRACT.md section 6", rec["rule_source"])
        json.dumps(rec)                                          # machine-readable

    def test_accepts_date_objects_and_other_years(self):
        import datetime
        self.assertEqual(derive_effective_date(datetime.date(2024, 12, 31))[0], "2025-01-01")
        self.assertEqual(derive_effective_date("2026-02-03")[0], "2027-01-01")

    def test_a_stated_date_is_returned_unchanged(self):
        for stated in ("2027-07-01", "2026-03", "2026"):
            date, rec = derive_effective_date("2025-10-06", stated_effective_date=stated)
            self.assertEqual(date, stated)
            self.assertEqual(rec["category"], "stated")
            self.assertFalse(rec["derived"])
            self.assertNotEqual(rec["rule_id"], RULE_CA_DEFAULT)

    def test_urgency_clause_is_explicitly_out_of_scope(self):
        date, rec = derive_effective_date("2025-10-06", urgency=True)
        self.assertIsNone(date)
        self.assertFalse(rec["applied"])
        self.assertIn("OUT OF SCOPE", rec["reason"])
        self.assertEqual(rec["category"], "unknown")

    def test_other_cases_are_out_of_scope_not_guessed(self):
        for kw in ({"jurisdiction": "NJ"}, {"instrument": "ordinance"}, {"session": "special"}):
            date, rec = derive_effective_date("2025-10-06", **kw)
            self.assertIsNone(date, kw)
            self.assertFalse(rec["applied"], kw)
        date, rec = derive_effective_date(None)
        self.assertIsNone(date)
        self.assertFalse(rec["applied"])

    def test_bad_inputs_raise(self):
        with self.assertRaises(ValueError):
            derive_effective_date("not a date")
        with self.assertRaises(ValueError):
            derive_effective_date("2025-10-06", stated_effective_date="2026-13-45")

    def test_the_pipeline_does_not_import_or_call_the_derivation(self):
        for p in (ROOT / "navigator" / "extract").glob("*.py"):
            src = p.read_text()
            self.assertIsNone(re.search(r"derive_effective_date\s*\(", src), p.name)
            self.assertIsNone(re.search(r"import\s+[^\n]*derive_effective_date", src), p.name)
        self.assertEqual(ENGINE_OWNED_RULE_IDS, (RULE_CA_DEFAULT,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
