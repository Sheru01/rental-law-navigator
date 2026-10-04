"""Tests for the geocode validator (navigator/geocode/validate.py).

Run from the project root:

    python3 -m unittest navigator.tests.test_geocode -v
    python3 -m navigator.tests.test_geocode

The validator is exercised against fixtures authored in
navigator/tests/fixtures/geocode/ (see make_fixtures.py there): one clean file
that must pass, and at least one deliberately broken file per hard-failure
class. Each broken file must fail with exactly its own class and nothing else,
so a defect cannot hide behind an unrelated failure. The last class checks the
real navigator/out/jurisdictions.json if it exists and is skipped otherwise.

Nothing here touches navigator/out/: CLI runs write to a temp directory.
"""
import csv
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXDIR = ROOT / "navigator" / "tests" / "fixtures" / "geocode"
VALIDATE = ROOT / "navigator" / "geocode" / "validate.py"
MAKE = FIXDIR / "make_fixtures.py"
CSV_PATH = ROOT / "data" / "sample_addresses.csv"
REAL_OUTPUT = ROOT / "navigator" / "out" / "jurisdictions.json"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


V = load_module("geocode_validate_under_test", VALIDATE)
MK = load_module("geocode_make_fixtures_under_test", MAKE)

CSV_ROWS = V.load_csv_rows(CSV_PATH)


def validate_file(path):
    """In-process validation of one jurisdictions file."""
    records, _sha, load_failures = V.load_json_strict(path)
    return V.validate(records, CSV_ROWS, load_failures)


def run_cli(jur_path, tmp, batch=True):
    report = Path(tmp) / "report.md"
    cmd = [sys.executable, str(VALIDATE), "--jurisdictions", str(jur_path),
           "--report", str(report)]
    if batch:
        cmd += ["--batch-input", str(Path(tmp) / "batch.csv")]
    else:
        cmd += ["--no-batch-input"]
    proc = subprocess.run(cmd, cwd=str(ROOT), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True, timeout=120)
    text = report.read_text(encoding="ascii") if report.exists() else None
    return proc.returncode, proc.stdout, text


# fixture file -> (the only hard-failure class it may trigger, substrings that
# must appear in that class's messages)
BROKEN = OrderedDict([
    ("broken_c1_count_499.json", ("C1", ["found 499 address_ids", "missing from the output"])),
    ("broken_c1_extra_id_501.json", ("C1", ["found 501 address_ids", "is not in sample_addresses.csv"])),
    ("broken_c1_foreign_id.json", ("C1", ["not in sample_addresses.csv", "missing from the output"])),
    ("broken_c1_duplicate_key.json", ("C1", ["duplicate JSON key"])),
    ("broken_c1_malformed_json.json", ("C1", ["not valid JSON"])),
    ("broken_c1_top_level_list.json", ("C1", ["top level must be an object"])),
    ("broken_c2_missing_keys.json", ("C2", ["missing keys: county", "missing keys: flags",
                                           "missing keys: year_built", "missing keys: lat"])),
    ("broken_c3_unresolved_with_legal_city.json", ("C3", ["silent fallback"])),
    ("broken_c3_mailing_name_as_legal_city.json", ("C3", ["Dorchester, MA", "Roxbury, MA", "Allston, MA",
                                                         "Brighton, MA", "East Boston, MA", "South Boston, MA",
                                                         "Hyde Park, MA", "Jamaica Plain, MA", "Mattapan, MA",
                                                         "San Ysidro, CA"])),
    ("broken_c4_census_suffix.json", ("C4", ["Los Angeles city, CA", "Hoboken city, NJ", "Jersey City city, NJ"])),
    ("broken_c4_bad_form.json", ("C4", ["'Berkeley'", "Berkeley, California", "BERKELEY CA",
                                       "disagrees with state_code", "not a string"])),
    ("broken_c5_year_changed.json", ("C5", ["year_built differs"])),
    ("broken_c5_units_invented.json", ("C5", ["units invented"])),
    ("broken_c5_year_invented.json", ("C5", ["year_built invented"])),
    ("broken_c5_year_dropped.json", ("C5", ["year_built dropped"])),
    ("broken_c5_year_float.json", ("C5", ["must be int or null"])),
    ("broken_c6_out_of_range.json", ("C6", ["outside the plausible range for NJ", "outside the plausible range for CA"])),
    ("broken_c6_lat_lon_swapped.json", ("C6", ["outside the plausible range for CA"])),
    ("broken_c6_resolved_null_coords.json", ("C6", ["null coordinates"])),
    ("broken_c7_zip_float.json", ("C7", ["zip is float", "float coercion"])),
    ("broken_c7_zip_int_lost_zero.json", ("C7", ["zip is int"])),
    ("broken_c7_zip_short_string.json", ("C7", ["not a 5 digit string"])),
    ("broken_c7_bad_method.json", ("C7", ["is not one of"])),
    ("broken_c7_passthrough_changed.json", ("C7", ["postal_city 'Elsewhere' differs", "street_address"])),
    ("broken_c7_flags_not_list.json", ("C7", ["flags must be a list"])),
])


class TestCleanFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.code, cls.stdout, cls.report = run_cli(FIXDIR / "clean.json", cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_exit_zero_and_pass(self):
        self.assertEqual(self.code, 0, self.stdout)
        self.assertIn("Status: **PASS**", self.report)

    def test_no_hard_failures_in_process(self):
        res = validate_file(FIXDIR / "clean.json")
        self.assertFalse(res.failed, dict((c, v[:3]) for c, v in res.failures.items() if v))

    def test_legitimate_unresolved_rows_pass(self):
        # clean.json has 3 unresolved rows with legal_city null and flags set
        self.assertIn("Unresolved: 3", self.report)
        self.assertIn("unresolved_no_census_match", self.report)

    def test_unincorporated_is_a_warning_not_a_failure(self):
        self.assertIn("resolved_no_place", self.report)

    def test_report_sections_present(self):
        for heading in ("## Hard failures", "## Warnings",
                        "## Counts by legal_city versus the README expectation",
                        "## Addresses where legal_city differs from postal_city",
                        "## Unresolved addresses",
                        "## NJ rows whose supplied zip is outside the NJ range",
                        "## Missing year_built and units by legal_city",
                        "## census_batch_input.csv cross-check"):
            self.assertIn(heading, self.report)

    def test_count_table_flags_deviations(self):
        # the clean fixture has 3 unresolved rows removed from LA/SF/Newark
        self.assertIn("| Los Angeles, CA | 80 | 79 | -1 | DEVIATION |", self.report)
        self.assertIn("| San Diego, CA | 50 | 50 | +0 | ok |", self.report)
        self.assertIn("| Boston, MA | 60 | 60 | +0 | ok |", self.report)

    def test_neighborhoods_reported_as_differences(self):
        self.assertIn("| Dorchester | MA | Boston, MA | 13 |", self.report)
        self.assertIn("| San Ysidro | CA | San Diego, CA | 1 |", self.report)

    def test_nj_out_of_range_zip_reporting(self):
        self.assertIn("NJ rows with out-of-range zip: 27. flagged: 26. not flagged: 1.", self.report)
        self.assertIn("NOT FLAGGED", self.report)

    def test_missing_fact_totals_match_csv(self):
        want_y = sum(1 for r in CSV_ROWS if not r["year_built"].strip())
        want_u = sum(1 for r in CSV_ROWS if not r["units"].strip())
        self.assertIn("| TOTAL | 500 | %d | %d |" % (want_y, want_u), self.report)

    def test_report_is_plain_ascii_without_absolute_paths(self):
        self.assertTrue(all(ord(ch) < 128 for ch in self.report))
        self.assertNotIn(chr(0x2014), self.report)
        self.assertNotIn(str(ROOT), self.report)
        self.assertNotIn(os.path.expanduser("~"), self.report)


class TestBrokenFixtures(unittest.TestCase):
    """One generated test method per broken fixture (added below)."""


def _make_test(name, cls, needles):
    def test(self):
        path = FIXDIR / name
        self.assertTrue(path.exists(), "missing fixture %s" % name)
        res = validate_file(path)
        fired = set(c for c, v in res.failures.items() if v)
        self.assertEqual(fired, {cls}, "expected only %s, got %s" % (cls, sorted(fired)))
        blob = "\n".join(m for _, m in res.failures[cls])
        for needle in needles:
            self.assertIn(needle, blob)
        with tempfile.TemporaryDirectory() as tmp:
            code, _out, report = run_cli(path, tmp, batch=False)
        self.assertEqual(code, 1, "validator must exit non-zero for %s" % name)
        self.assertIn("Status: **FAIL**", report)
        self.assertTrue(all(ord(ch) < 128 for ch in report))
    return test


for _name, (_cls, _needles) in BROKEN.items():
    setattr(TestBrokenFixtures, "test_%s" % _name[:-5], _make_test(_name, _cls, _needles))


class TestEveryClassIsProven(unittest.TestCase):
    def test_each_hard_failure_class_has_a_fixture(self):
        covered = set(cls for cls, _ in BROKEN.values())
        self.assertEqual(covered, set(V.CLASSES))

    def test_no_fixture_files_are_untested(self):
        on_disk = set(p.name for p in FIXDIR.glob("broken_*.json"))
        self.assertEqual(on_disk, set(BROKEN))


class TestPostalCityFallback(unittest.TestCase):
    """The important rule, called out on its own."""

    def test_unresolved_must_have_null_legal_city(self):
        res = validate_file(FIXDIR / "broken_c3_unresolved_with_legal_city.json")
        self.assertEqual(len(res.failures["C3"]), 1)
        self.assertEqual(res.failures["C3"][0][0], "A0001")

    def test_every_listed_mailing_name_is_caught(self):
        res = validate_file(FIXDIR / "broken_c3_mailing_name_as_legal_city.json")
        self.assertEqual(len(res.failures["C3"]), 10)

    def test_boston_and_san_diego_are_legitimate_legal_cities(self):
        recs = MK.clean_records(CSV_ROWS)
        res = V.validate(recs, CSV_ROWS)
        self.assertFalse(res.failed)
        legal = set(r["legal_city"] for r in recs.values())
        self.assertIn("Boston, MA", legal)
        self.assertIn("San Diego, CA", legal)

    def test_empty_string_legal_city_on_unresolved_still_fails(self):
        recs = MK.clean_records(CSV_ROWS)
        aid = [a for a, r in recs.items() if r["method"] == "unresolved"][0]
        recs[aid]["legal_city"] = ""
        res = V.validate(recs, CSV_ROWS)
        self.assertTrue(res.failures["C3"])


class TestHelpers(unittest.TestCase):
    def test_census_suffix_detection_keeps_real_city_names(self):
        name, st = V.split_legal("Jersey City, NJ")
        self.assertEqual((name, st), ("Jersey City", "NJ"))
        self.assertIsNone(V.CENSUS_SUFFIX.search("Jersey City"))
        self.assertIsNotNone(V.CENSUS_SUFFIX.search("Jersey City city"))
        self.assertIsNotNone(V.CENSUS_SUFFIX.search("Berkeley city"))
        self.assertIsNone(V.CENSUS_SUFFIX.search("Union City"))

    def test_legal_name_strips_suffix_and_state(self):
        self.assertEqual(V.legal_name("Berkeley city, CA"), "berkeley")
        self.assertEqual(V.legal_name("East Boston, MA"), "east boston")
        self.assertIsNone(V.legal_name(None))

    def test_ascii_cell(self):
        self.assertEqual(V.ascii_cell("a|b"), "a/b")
        self.assertEqual(V.ascii_cell("caf" + chr(0xe9) + " " + chr(0x2014) + " x"), "caf\\xe9 \\u2014 x")

    def test_required_keys_are_the_fourteen_in_the_brief(self):
        self.assertEqual(len(V.REQUIRED_KEYS), 14)
        self.assertEqual(set(V.REQUIRED_KEYS), {
            "street_address", "postal_city", "state", "zip", "legal_city", "county",
            "state_code", "method", "match_quality", "lat", "lon", "year_built",
            "units", "flags"})


class TestPreconditions(unittest.TestCase):
    def test_missing_output_file_fails_and_still_writes_batch_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _out, report = run_cli(Path(tmp) / "nope.json", tmp)
            self.assertEqual(code, 1)
            self.assertIn("does not exist yet", report)
            self.assertTrue((Path(tmp) / "batch.csv").exists())

    def test_missing_csv_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run([sys.executable, str(VALIDATE), "--addresses", str(Path(tmp) / "x.csv"),
                                   "--jurisdictions", str(FIXDIR / "clean.json"),
                                   "--report", str(Path(tmp) / "r.md"), "--no-batch-input"],
                                  cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  universal_newlines=True)
            self.assertEqual(proc.returncode, 2)


class TestBatchInput(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "batch.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_shape_no_header_blank_zips_untouched(self):
        info = V.write_batch_input(CSV_ROWS, self.path)
        self.assertEqual(info["status"], "written")
        raw = self.path.read_bytes().decode("utf-8")
        rows = list(csv.reader(raw.splitlines()))
        self.assertEqual(len(rows), 500)
        self.assertTrue(all(len(r) == 5 for r in rows))
        self.assertNotEqual(rows[0][0], "address_id")
        for out, src in zip(rows, CSV_ROWS):
            self.assertEqual(out, [src["address_id"], src["street_address"], src["postal_city"],
                                   src["state"], src["zip"]])
        self.assertEqual(sum(1 for r in rows if r[4] == ""), 130)
        self.assertEqual([r for r in rows if r[0] == "A0002"][0][4], "07030")
        self.assertNotIn("\r", raw)

    def test_identical_existing_file_is_recognised(self):
        V.write_batch_input(CSV_ROWS, self.path)
        self.assertEqual(V.write_batch_input(CSV_ROWS, self.path)["status"], "identical")

    def test_crlf_variant_is_same_content(self):
        V.write_batch_input(CSV_ROWS, self.path)
        self.path.write_bytes(self.path.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(V.write_batch_input(CSV_ROWS, self.path)["status"], "same_content_different_bytes")

    def test_difference_is_reported_and_existing_file_left_alone(self):
        # a header row and a padded blank zip are the kinds of drift to catch
        V.write_batch_input(CSV_ROWS, self.path)
        lines = self.path.read_text(encoding="utf-8").splitlines()
        lines[0] = lines[0].replace("90028", "9002")
        lines.insert(0, "address_id,street,city,state,zip")
        theirs = ("\n".join(lines) + "\n").encode("utf-8")
        self.path.write_bytes(theirs)
        info = V.write_batch_input(CSV_ROWS, self.path)
        self.assertEqual(info["status"], "differs")
        self.assertEqual(self.path.read_bytes(), theirs)
        alt = self.path.with_name("batch.validator.csv")
        self.assertTrue(alt.exists())
        self.assertEqual(len(alt.read_bytes().splitlines()), 500)
        self.assertTrue(any("header" in d for d in info["detail"]))

    def test_difference_shows_up_as_a_warning_in_the_report(self):
        V.write_batch_input(CSV_ROWS, self.path)
        self.path.write_bytes(self.path.read_bytes() + b"A9999,1 X ST,Nowhere,CA,90000\n")
        code, _out, report = run_cli(FIXDIR / "clean.json", self.tmp.name)
        self.assertEqual(code, 0)  # a warning, not a hard failure
        self.assertIn("census_batch_input.csv DIFFERS", report)
        self.assertIn("ids only in existing file: A9999", report)


class TestFixturesAreInSync(unittest.TestCase):
    def test_on_disk_fixtures_match_the_generator(self):
        expected = MK.build_all(MK.load_rows())
        self.assertEqual(set(expected), set(p.name for p in FIXDIR.glob("*.json")))
        for name, text in expected.items():
            self.assertEqual((FIXDIR / name).read_text(encoding="ascii"), text, name)


@unittest.skipUnless(REAL_OUTPUT.exists(), "navigator/out/jurisdictions.json not produced yet")
class TestRealOutput(unittest.TestCase):
    def test_real_jurisdictions_pass_every_hard_check(self):
        res = validate_file(REAL_OUTPUT)
        detail = dict((c, v[:3]) for c, v in res.failures.items() if v)
        self.assertFalse(res.failed, detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
