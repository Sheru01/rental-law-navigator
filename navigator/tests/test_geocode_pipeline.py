"""Tests for the geocoding pipeline: normalize.py, fetch.py, parse.py, run.py.

Run from the project root:

    python3 -m unittest navigator.tests.test_geocode_pipeline -v
    python3 -m navigator.tests.test_geocode_pipeline

Nothing here reaches the Census Geocoder, and nothing writes to navigator/out/.
Stage 1 (normalize) and stage 3 (parse) are tested with no network at all.
Stage 2 (fetch) is tested against an unroutable host and against a local fake
Census server on 127.0.0.1. The raw response fixtures in
navigator/tests/fixtures/geocode_responses/ are hand-authored and synthetic.
"""
import csv
import hashlib
import http.server
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GEO = ROOT / "navigator" / "geocode"
FIX = ROOT / "navigator" / "tests" / "fixtures" / "geocode_responses"
CSV_PATH = ROOT / "data" / "sample_addresses.csv"
VALIDATE = GEO / "validate.py"
RUN = GEO / "run.py"
FETCH = GEO / "fetch.py"

sys.path.insert(0, str(GEO))
import fetch  # noqa: E402
import normalize  # noqa: E402
import parse  # noqa: E402

CSV_ROWS = normalize.load_addresses(CSV_PATH)
REQ = normalize.normalize_rows(CSV_ROWS)
REQ_BY_ID = OrderedDict((r["address_id"], r) for r in REQ)
RAW_CSV = {r["address_id"]: r for r in CSV_ROWS}

MAILING_NAMES = ["Dorchester", "Roxbury", "Allston", "Brighton", "East Boston",
                 "South Boston", "Hyde Park", "Jamaica Plain", "Mattapan",
                 "San Ysidro", "Van Nuys"]

_SAVED_ENV = {}


def setUpModule():
    # Keep the loopback fake server away from any proxy in the environment.
    for k in ("NO_PROXY", "no_proxy"):
        _SAVED_ENV[k] = os.environ.get(k)
        os.environ[k] = "127.0.0.1,localhost"


def tearDownModule():
    for k, v in _SAVED_ENV.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def run_script(script, *args, **kw):
    env = kw.pop("env", None)
    return subprocess.run([sys.executable, str(script)] + [str(a) for a in args],
                          cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=180, env=env)


def places_jsonl(ids, status=200, body_override=None):
    """Assemble the JSONL that fetch.py writes from the hand-authored bodies."""
    lines = []
    for aid in ids:
        body = body_override if body_override is not None else \
            (FIX / "places" / (aid + ".json")).read_text(encoding="utf-8")
        lines.append(json.dumps({"address_id": aid, "x": "0", "y": "0", "url": "fixture",
                                 "http_status": status, "body": body}))
    return ("\n".join(lines) + "\n").encode("ascii")


def parse_fixture(name, place_ids=(), meta=None):
    batch = (FIX / name).read_bytes()
    places = places_jsonl(place_ids) if place_ids is not None else None
    return parse.parse_response(batch, REQ, places, meta)


def match_line(aid, state_fips="06", county="037", coords="-118.3213,34.0957",
               mtype="Exact", matched="1 MAIN ST, LOS ANGELES, CA, 90028"):
    buf = io.StringIO()
    csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n").writerow(
        [aid, "input", "Match", mtype, matched, coords, "1", "L", state_fips, county, "000100", "1000"])
    return buf.getvalue()


def place_body(place_name, state_fips="06", extra_places=(), counties=True):
    places = []
    for n in ([place_name] if place_name else []) + list(extra_places):
        places.append({"STATE": state_fips, "PLACE": "00000", "GEOID": state_fips + "00000", "NAME": n})
    geos = {"States": [{"STATE": state_fips, "STUSAB": "XX"}],
            "Incorporated Places": places, "County Subdivisions": []}
    if counties:
        geos["Counties"] = [{"STATE": state_fips, "COUNTY": "037", "NAME": "Test County"}]
    return json.dumps({"result": {"geographies": geos}})


def one_place(aid, body_text):
    return places_jsonl([aid], body_override=body_text)


# ---- synthetic full 500 row response, built deterministically from the CSV ----

CITY = {
    "Los Angeles": ("Los Angeles city", "06", "CA", "037", "Los Angeles County", -118.40, 34.00),
    "San Francisco": ("San Francisco city", "06", "CA", "075", "San Francisco County", -122.45, 37.74),
    "San Diego": ("San Diego city", "06", "CA", "073", "San Diego County", -117.20, 32.70),
    "San Ysidro": ("San Diego city", "06", "CA", "073", "San Diego County", -117.10, 32.60),
    "Berkeley": ("Berkeley city", "06", "CA", "001", "Alameda County", -122.30, 37.86),
    "Jersey City": ("Jersey City city", "34", "NJ", "017", "Hudson County", -74.10, 40.70),
    "Hoboken": ("Hoboken city", "34", "NJ", "017", "Hudson County", -74.05, 40.74),
    "Newark": ("Newark city", "34", "NJ", "013", "Essex County", -74.20, 40.72),
    "Cambridge": ("Cambridge city", "25", "MA", "017", "Middlesex County", -71.13, 42.36),
}
for _n in ("Boston", "Dorchester", "Roxbury", "East Boston", "Brighton", "Allston",
           "South Boston", "Jamaica Plain", "Hyde Park", "Mattapan"):
    CITY[_n] = ("Boston city", "25", "MA", "025", "Suffolk County", -71.10, 42.32)
DEFAULT_ZIP = {"CA": "90028", "NJ": "07102", "MA": "02115"}
SCENARIO = {"A0003": "no_match", "A0021": "tie", "A0004": "unincorporated", "A0016": "non_exact"}


def synth(req, idx):
    """Return (response_line, coordinates, place_body_text_or_None) for one row."""
    place, sf, st, cf, cname, lon0, lat0 = CITY[req["postal_city"]]
    coords = "%.6f,%.6f" % (lon0 + (idx % 50) * 0.0007, lat0 + (idx // 50) * 0.0007)
    scenario = SCENARIO.get(req["address_id"], "exact")
    sent = ", ".join([req["normalized_address"], req["postal_city"], req["state"], req["zip"]])
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    if scenario == "no_match":
        w.writerow([req["address_id"], sent, "No_Match"])
        return buf.getvalue(), None, None
    if scenario == "tie":
        w.writerow([req["address_id"], sent, "Tie"])
        return buf.getvalue(), None, None
    supplied = req["zip"]
    inside = parse.zip_in_state_range(st, supplied)
    gzip = supplied if (supplied and inside) else DEFAULT_ZIP[st]
    matched = "%s, %s, %s, %s" % (req["normalized_address"].upper(), req["postal_city"].upper(), st, gzip)
    w.writerow([req["address_id"], sent, "Match", "Non_Exact" if scenario == "non_exact" else "Exact",
                matched, coords, str(100000 + idx), "L", sf, cf, "%06d" % (idx + 100), "1000"])
    geos = {"States": [{"STATE": sf, "STUSAB": st}],
            "Counties": [{"STATE": sf, "COUNTY": cf, "NAME": cname}],
            "County Subdivisions": [{"STATE": sf, "COUNTY": cf, "NAME": "Example subdivision"}],
            "Incorporated Places": ([] if scenario == "unincorporated"
                                    else [{"STATE": sf, "PLACE": "99999", "NAME": place}])}
    return buf.getvalue(), coords, json.dumps({"result": {"geographies": geos}})


def build_full(reqs=REQ):
    lines, places, by_coord = [], [], {}
    for idx, req in enumerate(reqs):
        line, coords, body = synth(req, idx)
        lines.append(line)
        if body is not None:
            by_coord[coords] = body
            x, y = coords.split(",")
            places.append(json.dumps({"address_id": req["address_id"], "x": x, "y": y,
                                      "url": "synthetic", "http_status": 200, "body": body}))
    return "".join(lines).encode("utf-8"), ("\n".join(places) + "\n").encode("ascii"), by_coord


# ---- a local fake Census server ----

def parse_multipart(ctype, body):
    boundary = ctype.split("boundary=")[1].encode()
    out = {}
    for part in body.split(b"--" + boundary)[1:]:
        if part.startswith(b"--"):
            break
        head, _, content = part.partition(b"\r\n\r\n")
        if content.endswith(b"\r\n"):
            content = content[:-2]
        out[re.search(rb'name="([^"]+)"', head).group(1).decode()] = content
    return out


class FakeCensus(object):
    """Loopback server. batch_mode(n, ids, full) returns bytes or an int status."""

    def __init__(self, batch_mode=None, place_mode=None):
        self.batch_requests = []
        self.forms = []
        self.place_queries = []
        self.batch_mode = batch_mode or (lambda n, ids, full: full)
        self.place_mode = place_mode
        _, _, self.by_coord = build_full()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, data, ctype="text/plain"):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                form = parse_multipart(self.headers["Content-Type"], self.rfile.read(length))
                outer.forms.append(form)
                ids = [r[0] for r in csv.reader(io.StringIO(form["addressFile"].decode("utf-8")))]
                outer.batch_requests.append(ids)
                full = "".join(synth(REQ_BY_ID[i], list(REQ_BY_ID).index(i))[0] for i in ids).encode("utf-8")
                result = outer.batch_mode(len(outer.batch_requests), ids, full)
                if isinstance(result, int):
                    self._send(result, b"error")
                else:
                    self._send(200, result)

            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                outer.place_queries.append(q)
                key = "%s,%s" % (q["x"][0], q["y"][0])
                if outer.place_mode:
                    r = outer.place_mode(key)
                    if r is not None:
                        status, data = r
                        self._send(status, data)
                        return
                self._send(200, outer.by_coord[key].encode("utf-8"), "application/json")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def free_closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --------------------------------------------------------------------------
# stage 1: normalize
# --------------------------------------------------------------------------

class NormalizeTests(unittest.TestCase):
    def test_deterministic_byte_identical(self):
        a = normalize.build_batch_bytes(normalize.normalize_rows(normalize.load_addresses(CSV_PATH)))
        b = normalize.build_batch_bytes(normalize.normalize_rows(normalize.load_addresses(CSV_PATH)))
        self.assertEqual(a, b)
        self.assertEqual(hashlib.sha256(a).hexdigest(), hashlib.sha256(b).hexdigest())

    def test_cli_twice_writes_byte_identical_files(self):
        with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
            for t in (t1, t2):
                proc = run_script(GEO / "normalize.py", "--out-dir", t)
                self.assertEqual(proc.returncode, 0, proc.stderr)
            f1 = (Path(t1) / "census_batch_input.csv").read_bytes()
            f2 = (Path(t2) / "census_batch_input.csv").read_bytes()
            self.assertEqual(f1, f2)

    def test_five_columns_no_header_500_rows(self):
        data = normalize.build_batch_bytes(REQ).decode("utf-8")
        rows = list(csv.reader(io.StringIO(data, newline="")))
        self.assertEqual(len(rows), 500)
        self.assertTrue(all(len(r) == 5 for r in rows))
        self.assertNotEqual(rows[0][0], "address_id")
        self.assertEqual([r[0] for r in rows], [r["address_id"] for r in CSV_ROWS])

    def test_no_zip_is_ever_padded_or_altered_and_blanks_stay_blank(self):
        rows = list(csv.reader(io.StringIO(normalize.build_batch_bytes(REQ).decode("utf-8"), newline="")))
        blank = 0
        for req, sent, raw in zip(REQ, rows, CSV_ROWS):
            self.assertEqual(req["zip"], raw["zip"])
            self.assertEqual(sent[4], raw["zip"])
            self.assertEqual(sent[2], raw["postal_city"])
            self.assertEqual(sent[3], raw["state"])
            if raw["zip"] == "":
                blank += 1
            else:
                self.assertRegex(raw["zip"], r"^\d{5}$")
        self.assertEqual(blank, 130)
        self.assertEqual(sum(1 for r in REQ if r["state"] == "NJ" and r["zip"] == "07030"),
                         sum(1 for r in CSV_ROWS if r["state"] == "NJ" and r["zip"] == "07030"))
        self.assertTrue(any(r["zip"].startswith("0") for r in REQ))

    def test_a_short_or_blank_zip_is_never_padded_even_when_fed_one(self):
        # The CSV happens to hold only 5 digit zips, so a padding bug would hide
        # behind real data. Feed unpadded and odd values directly.
        base = CSV_ROWS[1]
        for value in ("7030", "07030", "", "7030-1234", " 7030"):
            row = normalize.normalize_rows([dict(base, zip=value)])[0]
            self.assertEqual(row["zip"], value)
            sent = list(csv.reader(io.StringIO(normalize.build_batch_bytes([row]).decode("utf-8"))))[0]
            self.assertEqual(sent[4], value)

    def test_original_address_is_verbatim_and_both_are_carried(self):
        for req, raw in zip(REQ, CSV_ROWS):
            self.assertEqual(req["original_address"], raw["street_address"])
            if not req["normalizations"]:
                self.assertEqual(req["normalized_address"], req["original_address"])
            else:
                self.assertNotEqual(req["normalized_address"], req["original_address"])
        # the one with a triple space survives untouched in original_address
        self.assertEqual(REQ_BY_ID["A0421"]["original_address"], "17106 CHATSWORTH ST   APT 0001")

    def test_known_normalizations(self):
        cases = {
            "1031-1035 CLINTON ST": ("1031 CLINTON ST", ["range_to_first_number:1031-1035->1031"]),
            "1064 SUMMIT AVE.": ("1064 SUMMIT AVE", ["strip_trailing_period"]),
            "17106 CHATSWORTH ST   APT 0001": ("17106 CHATSWORTH ST", ["collapse_whitespace", "strip_unit:APT 0001"]),
            "238 & 242 GARFIELD AVE.": ("238 GARFIELD AVE", ["strip_trailing_period", "multi_number_to_first:238 & 242->238"]),
            "38-38- SOMME ST": ("38 SOMME ST", ["range_to_first_number:38-38->38"]),
            "14.5-16 Vandine St": ("14 Vandine St", ["range_to_first_number:14.5-16->14.5",
                                                      "drop_fractional_house_number:14.5->14"]),
            "38A GAUTIER AVE.": ("38A GAUTIER AVE", ["strip_trailing_period"]),
            "6238 DE LONGPRE AVE": ("6238 DE LONGPRE AVE", []),
            "WILLOWWOOD ST": ("WILLOWWOOD ST", []),
        }
        for raw, (want, codes) in cases.items():
            self.assertEqual(normalize.normalize_street(raw), (want, codes), raw)

    def test_every_change_is_recorded(self):
        changed = [r for r in REQ if r["normalized_address"] != r["original_address"]]
        self.assertGreater(len(changed), 100)
        for r in changed:
            self.assertTrue(r["normalizations"], r["address_id"])
            self.assertTrue(all(isinstance(c, str) and c for c in r["normalizations"]))

    def test_existing_batch_file_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as t:
            plain = Path(t) / "census_batch_input.csv"
            plain.write_bytes(b"A0001,x,y,CA,90028\n")
            path, status = normalize.write_batch_input(REQ, t)
            self.assertEqual(status, "differs_written_beside")
            self.assertEqual(path.name, "census_batch_input.pipeline.csv")
            self.assertEqual(plain.read_bytes(), b"A0001,x,y,CA,90028\n")
            self.assertEqual(path.read_bytes(), normalize.build_batch_bytes(REQ))
            self.assertEqual(normalize.send_path(t), path)

    def test_identical_existing_file_is_left_alone(self):
        with tempfile.TemporaryDirectory() as t:
            normalize.write_batch_input(REQ, t)
            path, status = normalize.write_batch_input(REQ, t)
            self.assertEqual(status, "identical")
            self.assertEqual(path.name, "census_batch_input.csv")
            self.assertFalse((Path(t) / "census_batch_input.pipeline.csv").exists())

    def test_facts_equal_csv_and_never_repaired(self):
        for req, raw in zip(REQ, CSV_ROWS):
            for k in ("year_built", "units"):
                want = int(raw[k]) if raw[k] else None
                self.assertEqual(req[k], want)
                self.assertTrue(req[k] is None or (isinstance(req[k], int) and not isinstance(req[k], bool)))
        bad = dict(CSV_ROWS[0], year_built="1900.0")
        with self.assertRaises(ValueError):
            normalize.normalize_rows([bad])


# --------------------------------------------------------------------------
# stage 3: parse, one hand-authored fixture at a time
# --------------------------------------------------------------------------

class ParseFixtureTests(unittest.TestCase):
    def test_exact_match(self):
        recs, rep = parse_fixture("batch_exact.csv", ["A0001"])  # CRLF fixture
        r = recs["A0001"]
        self.assertEqual(r["match_status"], "match_exact")
        self.assertEqual(r["legal_city"], "Los Angeles, CA")
        self.assertEqual(r["method"], "census_batch")
        self.assertEqual(r["county"], "Los Angeles County")
        self.assertEqual(r["state_code"], "CA")
        self.assertEqual((r["lat"], r["lon"]), (34.0957, -118.3213))
        self.assertEqual(r["match_quality"], "Exact")
        self.assertEqual(r["matched_address"], "6238 DE LONGPRE AVE, LOS ANGELES, CA, 90028")
        self.assertEqual(r["flags"], [])
        self.assertEqual(r["provider"], "census_geographies")
        self.assertEqual(r["provider_benchmark"], "Public_AR_Current")
        self.assertEqual(r["provider_vintage"], "Current_Current")
        self.assertEqual(r["provenance"]["raw_response_line"], 1)
        self.assertEqual(r["provenance"]["source_file"], "data/sample_addresses.csv")
        self.assertEqual(r["provenance"]["source_row"], 2)
        self.assertEqual(r["geography_layers"]["batch"]["state_fips"], "06")
        self.assertIn("Incorporated Places", r["geography_layers"]["place_lookup"]["geographies"])
        self.assertIsNone(r["request_timestamp"])
        self.assertEqual(rep["with_legal_city"], 1)

    def test_non_exact_match_is_resolved_but_flagged(self):
        recs, _ = parse_fixture("batch_non_exact.csv", ["A0016"])
        r = recs["A0016"]
        self.assertEqual(r["match_status"], "match_non_exact")
        self.assertEqual(r["match_quality"], "Non_Exact")
        self.assertEqual(r["legal_city"], "San Francisco, CA")
        self.assertIn("non_exact_match", r["flags"])
        self.assertEqual(r["zip"], "")  # blank supplied zip stays blank
        self.assertEqual(r["geocoded_zip"], "94123")
        self.assertFalse([f for f in r["flags"] if f.startswith("zip")])

    def test_tie_is_explicit_and_has_no_legal_city(self):
        recs, _ = parse_fixture("batch_tie.csv", ["A0016"])
        for aid in ("A0021", "A0027"):
            r = recs[aid]
            self.assertEqual(r["match_status"], "tie")
            self.assertIsNone(r["legal_city"])
            self.assertEqual(r["method"], "unresolved")
            self.assertIn("tie_ambiguous", r["flags"])
            self.assertIsNone(r["lat"])
            self.assertIsNone(r["matched_address"])

    def test_no_match_is_explicit_and_has_no_legal_city(self):
        recs, _ = parse_fixture("batch_no_match.csv", [])
        r = recs["A0003"]
        self.assertEqual(r["match_status"], "no_match")
        self.assertIsNone(r["legal_city"])
        self.assertEqual(r["method"], "unresolved")
        self.assertIn("no_match", r["flags"])
        self.assertTrue(any(f.startswith("zip_outside_state_range:supplied=11219") for f in r["flags"]))
        self.assertEqual(r["zip"], "11219")

    def test_empty_place_layer_is_flagged_unincorporated_and_not_inferred(self):
        recs, _ = parse_fixture("batch_unincorporated.csv", ["A0004"])
        r = recs["A0004"]
        self.assertEqual(r["match_status"], "match_exact")
        self.assertIsNone(r["legal_city"])
        self.assertIn("no_incorporated_place_probably_unincorporated", r["flags"])
        self.assertEqual(r["postal_city"], "Los Angeles")  # untouched, and never used
        self.assertEqual(r["method"], "census_batch")
        subs = r["geography_layers"]["place_lookup"]["geographies"]["County Subdivisions"]
        self.assertEqual(subs[0]["NAME"], "Example CCD")  # kept for a human, not promoted

    def test_boston_neighborhood_resolves_to_boston(self):
        recs, _ = parse_fixture("batch_boston_neighborhood.csv", ["A0118"])
        r = recs["A0118"]
        self.assertEqual(r["postal_city"], "Dorchester")
        self.assertEqual(r["legal_city"], "Boston, MA")
        self.assertEqual(r["original_address"], "18-34 KINGBIRD RD")
        self.assertEqual(r["normalized_address"], "18 KINGBIRD RD")
        self.assertEqual(r["normalizations"], ["range_to_first_number:18-34->18"])

    def test_san_ysidro_resolves_to_san_diego(self):
        recs, _ = parse_fixture("batch_san_ysidro.csv", ["A0322"])
        r = recs["A0322"]
        self.assertEqual(r["postal_city"], "San Ysidro")
        self.assertEqual(r["legal_city"], "San Diego, CA")
        self.assertEqual(r["county"], "San Diego County")

    def test_zip_difference_is_flagged_not_corrected(self):
        recs, _ = parse_fixture("batch_zip_differs.csv", ["A0008"])
        r = recs["A0008"]
        self.assertEqual(r["zip"], "78746")  # supplied value untouched
        self.assertEqual(r["geocoded_zip"], "07307")
        self.assertIn("zip_mismatch_supplied_vs_geocoded:supplied=78746,geocoded=07307", r["flags"])
        self.assertIn("zip_outside_state_range:supplied=78746", r["flags"])
        self.assertEqual(r["legal_city"], "Jersey City, NJ")

    def test_truncated_response_never_produces_a_legal_city(self):
        # Even with a place lookup on hand for the cut-off row, it stays unresolved.
        recs, rep = parse_fixture("batch_truncated.csv", ["A0001", "A0002", "A0005"])
        self.assertEqual(recs["A0001"]["legal_city"], "Los Angeles, CA")
        self.assertEqual(recs["A0002"]["legal_city"], "Hoboken, NJ")
        cut = recs["A0005"]
        self.assertIsNone(cut["legal_city"])
        self.assertEqual(cut["match_status"], "not_attempted")
        self.assertIn("malformed_response_line", cut["flags"])
        self.assertEqual(cut["method"], "unresolved")
        self.assertEqual(len(rep["missing_ids"]), 497)
        self.assertEqual(len(rep["response_lines_malformed"]), 1)
        others = [r for aid, r in recs.items() if aid not in ("A0001", "A0002")]
        self.assertEqual(len(others), 498)
        for r in others:
            self.assertIsNone(r["legal_city"], r["address_id"])
            self.assertEqual(r["match_status"], "not_attempted")
            self.assertEqual(r["method"], "unresolved")
            self.assertTrue(r["flags"])
        missing = recs["A0003"]
        self.assertIn("missing_from_response_truncated", missing["flags"])

    def test_malformed_lines_are_flagged_and_do_not_stop_the_parse(self):
        recs, rep = parse_fixture("batch_malformed.csv", ["A0006"])
        self.assertEqual(recs["A0006"]["legal_city"], "Boston, MA")
        for aid in ("A0007", "A0009", "A0010", "A0011"):
            r = recs[aid]
            self.assertIsNone(r["legal_city"], aid)
            self.assertEqual(r["match_status"], "not_attempted")
            self.assertIn("malformed_response_line", r["flags"])
        reasons = [m["reason"] for m in rep["response_lines_malformed"]]
        self.assertTrue(any(x.startswith("match_line_has_5_fields") for x in reasons))
        self.assertIn("unparseable_coordinates", reasons)
        self.assertTrue(any(x.startswith("unknown_match_indicator") for x in reasons))
        self.assertTrue(any(x.startswith("csv_error") for x in reasons))
        self.assertTrue(any(x.startswith("too_few_fields") for x in reasons))
        self.assertGreaterEqual(len(rep["response_lines_malformed"]), 6)
        self.assertIn("garbage", rep["unknown_ids_in_response"])

    def test_duplicate_conflicting_lines_stay_unresolved(self):
        recs, _ = parse_fixture("batch_duplicate.csv", ["A0001"])
        r = recs["A0001"]
        self.assertIsNone(r["legal_city"])
        self.assertIn("duplicate_response_lines_ambiguous", r["flags"])
        self.assertEqual(r["match_status"], "not_attempted")

    def test_no_place_file_means_unresolved_not_a_guess(self):
        recs, _ = parse_fixture("batch_exact.csv", None)
        r = recs["A0001"]
        self.assertIsNone(r["legal_city"])
        self.assertEqual(r["method"], "unresolved")
        self.assertIn("place_layer_not_fetched", r["flags"])
        self.assertNotIn("no_incorporated_place_probably_unincorporated", r["flags"])
        self.assertIsNone(r["lat"])  # coordinates only published for trusted rows
        self.assertEqual(r["geography_layers"]["batch"]["coordinates"], "-118.3213,34.0957")

    def test_failed_place_lookups_are_not_read_as_unincorporated(self):
        batch = (FIX / "batch_exact.csv").read_bytes()
        cases = [
            (places_jsonl(["A0001"], status=500, body_override=""), "place_lookup_failed:http_status_500"),
            (one_place("A0001", "<html>nope</html>"), "place_lookup_failed:no_geographies_in_body"),
            (one_place("A0001", json.dumps({"result": {"geographies": {}}})),
             "place_lookup_failed:empty_geographies"),
        ]
        for places, want in cases:
            recs, _ = parse.parse_response(batch, REQ, places)
            r = recs["A0001"]
            self.assertIsNone(r["legal_city"])
            self.assertEqual(r["method"], "unresolved")
            self.assertIn(want, r["flags"])
            self.assertNotIn("no_incorporated_place_probably_unincorporated", r["flags"])

    def test_multiple_incorporated_places_are_ambiguous(self):
        batch = (FIX / "batch_exact.csv").read_bytes()
        recs, _ = parse.parse_response(batch, REQ, one_place("A0001", place_body("Alpha city", "06", ["Beta city"])))
        self.assertIsNone(recs["A0001"]["legal_city"])
        self.assertIn("multiple_incorporated_places_ambiguous", recs["A0001"]["flags"])

    def test_geocoded_state_mismatch_is_unresolved(self):
        batch = match_line("A0002", state_fips="36", county="061", coords="-73.99,40.73",
                           matched="1 X ST, NEW YORK, NY, 10003").encode()
        recs, _ = parse.parse_response(batch, REQ, one_place("A0002", place_body("New York city", "36")))
        r = recs["A0002"]
        self.assertIsNone(r["legal_city"])
        self.assertEqual(r["method"], "unresolved")
        self.assertTrue(any(f.startswith("geocoded_state_mismatch:csv=NJ,geocoded=NY") for f in r["flags"]))

    def test_coordinates_outside_the_state_are_unresolved(self):
        batch = match_line("A0001", coords="-74.00,40.70").encode()
        recs, _ = parse.parse_response(batch, REQ, one_place("A0001", place_body("Los Angeles city", "06")))
        self.assertIsNone(recs["A0001"]["legal_city"])
        self.assertIn("coordinates_outside_state_box", recs["A0001"]["flags"])

    def test_place_in_another_state_is_rejected(self):
        batch = match_line("A0001").encode()
        recs, _ = parse.parse_response(batch, REQ, one_place("A0001", place_body("Newark city", "34")))
        self.assertIsNone(recs["A0001"]["legal_city"])
        self.assertTrue(any(f.startswith("place_state_mismatch") for f in recs["A0001"]["flags"]))

    def test_suffix_stripping(self):
        cases = {"Berkeley city": "Berkeley", "Jersey City city": "Jersey City",
                 "Boston city": "Boston", "Fanwood borough": "Fanwood",
                 "Brookline town": "Brookline", "Ross village": "Ross",
                 "Cambridge": "Cambridge", "Jersey City": "Jersey City"}
        for raw, want in cases.items():
            self.assertEqual(parse.strip_place_suffix(raw), want)

    def test_provider_returned_mailing_name_is_rejected_as_legal_city(self):
        batch = match_line("A0001").encode()
        for name in MAILING_NAMES:
            recs, _ = parse.parse_response(batch, REQ, one_place("A0001", place_body(name + " city", "06")))
            r = recs["A0001"]
            self.assertIsNone(r["legal_city"], name)
            self.assertEqual(r["method"], "unresolved")
            self.assertTrue(any(f.startswith("legal_city_is_mailing_name_rejected") for f in r["flags"]))

    def test_meta_supplies_the_request_timestamp(self):
        meta = {"batch": {"retrieved_at": "2026-10-05T12:00:00Z"}, "places": {"retrieved_at": "2026-10-05T12:01:00Z"}}
        recs, _ = parse_fixture("batch_exact.csv", ["A0001"], meta)
        r = recs["A0001"]
        self.assertEqual(r["request_timestamp"], "2026-10-05T12:00:00Z")
        self.assertEqual(r["provenance"]["retrieved_at"], "2026-10-05T12:00:00Z")
        self.assertEqual(r["geography_layers"]["place_lookup"]["retrieved_at"], "2026-10-05T12:01:00Z")

    def test_parse_is_deterministic(self):
        a = parse.dumps(parse_fixture("batch_exact.csv", ["A0001"])[0])
        b = parse.dumps(parse_fixture("batch_exact.csv", ["A0001"])[0])
        self.assertEqual(a, b)
        a.encode("ascii")


# --------------------------------------------------------------------------
# invariants over a full synthetic 500 row response
# --------------------------------------------------------------------------

class InvariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        batch, places, _ = build_full()
        cls.recs, cls.report = parse.parse_response(batch, REQ, places)

    def test_all_500_present_in_order(self):
        self.assertEqual(list(self.recs), [r["address_id"] for r in CSV_ROWS])

    def test_original_address_survives_all_three_stages(self):
        batch_rows = list(csv.reader(io.StringIO(normalize.build_batch_bytes(REQ).decode("utf-8"), newline="")))
        for raw, req, sent, rec in zip(CSV_ROWS, REQ, batch_rows, self.recs.values()):
            self.assertEqual(req["original_address"], raw["street_address"])      # stage 1
            self.assertEqual(sent[1], req["normalized_address"])                  # what was sent
            self.assertEqual(rec["original_address"], raw["street_address"])      # stage 3
            self.assertEqual(rec["street_address"], raw["street_address"])
            self.assertEqual(rec["normalized_address"], sent[1])
            self.assertEqual(rec["normalizations"], req["normalizations"])

    def test_year_built_and_units_always_equal_the_csv(self):
        for raw, rec in zip(CSV_ROWS, self.recs.values()):
            for k in ("year_built", "units"):
                want = int(raw[k]) if raw[k] else None
                self.assertEqual(rec[k], want)
                self.assertTrue(rec[k] is None or type(rec[k]) is int)

    def test_zip_and_passthrough_fields_equal_the_csv(self):
        for raw, rec in zip(CSV_ROWS, self.recs.values()):
            self.assertEqual(rec["zip"], raw["zip"])
            self.assertEqual(rec["postal_city"], raw["postal_city"])
            self.assertEqual(rec["state"], raw["state"])
            self.assertIsInstance(rec["zip"], str)

    def test_failures_never_carry_a_legal_city(self):
        for aid in ("A0003", "A0021"):
            r = self.recs[aid]
            self.assertIsNone(r["legal_city"])
            self.assertEqual(r["method"], "unresolved")
            self.assertTrue(r["flags"])
        self.assertEqual(self.recs["A0003"]["match_status"], "no_match")
        self.assertEqual(self.recs["A0021"]["match_status"], "tie")
        for r in self.recs.values():
            if r["method"] == "unresolved":
                self.assertIsNone(r["legal_city"])
                self.assertTrue(r["flags"])
            if r["match_status"] in ("no_match", "tie", "not_attempted"):
                self.assertIsNone(r["legal_city"])

    def test_no_mailing_name_ever_appears_as_a_legal_city(self):
        banned = set(n.casefold() for n in MAILING_NAMES)
        for r in self.recs.values():
            if r["legal_city"]:
                self.assertNotIn(r["legal_city"].rsplit(",", 1)[0].casefold(), banned, r["address_id"])
        by_postal = {}
        for r in self.recs.values():
            by_postal.setdefault(r["postal_city"], set()).add(r["legal_city"])
        for n in ("Dorchester", "Roxbury", "Allston", "Brighton", "East Boston", "South Boston",
                  "Hyde Park", "Jamaica Plain", "Mattapan"):
            self.assertEqual(by_postal[n], {"Boston, MA"}, n)
        self.assertEqual(by_postal["San Ysidro"], {"San Diego, CA"})

    def test_no_postal_city_fallback_for_unincorporated(self):
        r = self.recs["A0004"]
        self.assertEqual(r["postal_city"], "Los Angeles")
        self.assertIsNone(r["legal_city"])
        self.assertIn("no_incorporated_place_probably_unincorporated", r["flags"])

    def test_all_27_out_of_range_nj_zips_are_flagged(self):
        nj = [a for a, raw in RAW_CSV.items() if raw["state"] == "NJ" and raw["zip"]
              and not raw["zip"].startswith(("07", "08"))]
        self.assertEqual(len(nj), 27)
        for a in nj:
            self.assertTrue(any(f.startswith("zip_outside_state_range") for f in self.recs[a]["flags"]), a)
            self.assertEqual(self.recs[a]["zip"], RAW_CSV[a]["zip"])

    def test_legal_city_has_no_census_suffix(self):
        for r in self.recs.values():
            if r["legal_city"]:
                name, st = r["legal_city"].rsplit(", ", 1)
                self.assertFalse(re.search(r"\s(city|town|village|borough)$", name), r["legal_city"])
                self.assertEqual(st, r["state_code"])

    def test_record_has_every_required_key(self):
        keys = ["original_address", "normalized_address", "normalizations", "postal_city", "state", "zip",
                "provider", "provider_benchmark", "provider_vintage", "request_timestamp", "match_status",
                "matched_address", "legal_city", "county", "state_code", "lat", "lon", "match_quality",
                "geography_layers", "method", "year_built", "units", "flags", "provenance"]
        prov = ["source_file", "source_row", "provider", "retrieved_at", "raw_response_line"]
        for r in self.recs.values():
            for k in keys:
                self.assertIn(k, r)
            for k in prov:
                self.assertIn(k, r["provenance"])
            self.assertIn(r["match_status"], parse.STATUSES)
            self.assertIn(r["method"], ("census_batch", "census_oneline", "unresolved"))
            self.assertIsNone(r["request_timestamp"])  # no live call has happened


# --------------------------------------------------------------------------
# stage 2: fetch, never against Census
# --------------------------------------------------------------------------

def write_input(dirpath, rows=REQ):
    p = Path(dirpath) / "input.csv"
    p.write_bytes(normalize.build_batch_bytes(rows))
    return p


class FetchRequestTests(unittest.TestCase):
    def test_same_inputs_give_the_same_request_bytes(self):
        chunk = normalize.build_batch_bytes(REQ[:100])
        a = fetch.build_batch_request(fetch.BASE_URL, chunk)
        b = fetch.build_batch_request(fetch.BASE_URL, chunk)
        self.assertEqual(a, b)
        self.assertEqual(a[0], "https://geocoding.geo.census.gov/geocoder/geographies/addressbatch")
        body = a[2]
        self.assertIn(b'name="addressFile"', body)
        self.assertIn(b'name="benchmark"\r\n\r\nPublic_AR_Current', body)
        self.assertIn(b'name="vintage"\r\n\r\nCurrent_Current', body)
        self.assertIn(chunk, body)
        self.assertNotEqual(a[2], fetch.build_batch_request(fetch.BASE_URL, normalize.build_batch_bytes(REQ[100:200]))[2])

    def test_coordinates_url_is_reproducible(self):
        u = fetch.build_coordinates_url(fetch.BASE_URL, "-118.3213,34.0957")
        self.assertEqual(u, "https://geocoding.geo.census.gov/geocoder/geographies/coordinates?"
                            "x=-118.3213&y=34.0957&benchmark=Public_AR_Current&vintage=Current_Current"
                            "&layers=all&format=json")

    def test_chunks_of_100(self):
        sizes = [len(c) for c in fetch.chunk_rows(list(range(500)))]
        self.assertEqual(sizes, [100] * 5)
        self.assertEqual([len(c) for c in fetch.chunk_rows(list(range(250)))], [100, 100, 50])

    def test_default_host_is_named_in_a_proxy_block_message(self):
        class Blocked(object):
            def open(self, req, timeout=None):
                raise urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden"))
        slept = []
        with tempfile.TemporaryDirectory() as d:
            inp = write_input(d, REQ[:3])
            out = Path(d) / "out.csv"
            with self.assertRaises(fetch.HostUnreachable) as cm:
                fetch.fetch_batch(inp, out, retries=3, backoff=5, sleep=slept.append, opener=Blocked())
            self.assertIn("geocoding.geo.census.gov", str(cm.exception))
            self.assertIn("Nothing was written", str(cm.exception))
            self.assertEqual(slept, [])  # a policy block fails fast, no backoff
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["input.csv"])

    def test_http_403_is_reported_as_a_blocked_host(self):
        with FakeCensus(batch_mode=lambda n, ids, full: 403) as fake, tempfile.TemporaryDirectory() as d:
            inp = write_input(d, REQ[:3])
            with self.assertRaises(fetch.HostUnreachable) as cm:
                fetch.fetch_batch(inp, Path(d) / "out.csv", base_url=fake.url, retries=2,
                                  backoff=0, sleep=lambda s: None)
            self.assertIn("127.0.0.1", str(cm.exception))
            self.assertEqual(len(fake.batch_requests), 1)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["input.csv"])


class FetchBlockedHostCliTests(unittest.TestCase):
    """fetch.py pointed at hosts that cannot answer. Never touches Census."""

    def _env(self):
        return {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}

    def _check(self, base_url, extra):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as src:
            inp = write_input(src)
            out = Path(d) / "census_batch_output.csv"
            proc = run_script(FETCH, "batch", "--input", inp, "--out", out, "--base-url", base_url,
                              "--retries", "1", "--backoff", "0", *extra, env=self._env())
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            host = base_url.split("//")[1].split(":")[0]
            self.assertIn(host, proc.stderr)
            self.assertIn("cannot reach", proc.stderr)
            self.assertIn("Nothing was written", proc.stderr)
            self.assertEqual(list(Path(d).iterdir()), [], "nothing partial may be written")

    def test_refused_connection_fails_cleanly_and_writes_nothing(self):
        self._check("http://127.0.0.1:%d" % free_closed_port(), ["--timeout", "3"])

    def test_unroutable_address_fails_cleanly_and_writes_nothing(self):
        # 192.0.2.0/24 is TEST-NET-1: reserved, never routable.
        self._check("http://192.0.2.1:81", ["--timeout", "2"])

    def test_places_step_also_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            batch = Path(d) / "census_batch_output.csv"
            batch.write_bytes((FIX / "batch_exact.csv").read_bytes())
            out = Path(d) / "census_places_output.jsonl"
            proc = run_script(FETCH, "places", "--batch-output", batch, "--out", out,
                              "--base-url", "http://127.0.0.1:%d" % free_closed_port(),
                              "--retries", "0", "--backoff", "0", env=self._env())
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("127.0.0.1", proc.stderr)
            self.assertFalse(out.exists())
            self.assertFalse((Path(d) / "census_fetch_meta.json").exists())
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["census_batch_output.csv"])


class FetchAgainstFakeServerTests(unittest.TestCase):
    def test_five_chunks_of_100_and_form_fields(self):
        with FakeCensus() as fake, tempfile.TemporaryDirectory() as d:
            inp = write_input(d)
            out = Path(d) / "census_batch_output.csv"
            info = fetch.fetch_batch(inp, out, base_url=fake.url, sleep=lambda s: None,
                                     now=lambda: "2026-10-05T12:00:00Z")
            self.assertEqual([len(ids) for ids in fake.batch_requests], [100] * 5)
            self.assertEqual([i for ids in fake.batch_requests for i in ids], [r["address_id"] for r in REQ])
            for form in fake.forms:
                self.assertEqual(form["benchmark"], b"Public_AR_Current")
                self.assertEqual(form["vintage"], b"Current_Current")
            expected, _, _ = build_full()
            self.assertEqual(out.read_bytes(), expected)  # raw response saved verbatim
            meta = json.loads((Path(d) / "census_fetch_meta.json").read_text())
            self.assertEqual(meta["batch"]["retrieved_at"], "2026-10-05T12:00:00Z")
            self.assertEqual(meta["batch"]["chunks"], 5)
            self.assertEqual(info["attempts_per_chunk"], [1] * 5)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()),
                             ["census_batch_output.csv", "census_fetch_meta.json", "input.csv"])

    def test_truncated_chunk_is_retried_with_backoff(self):
        def mode(n, ids, full):
            return full[: len(full) // 2] if n == 1 else full  # first try is cut mid line
        slept = []
        with FakeCensus(batch_mode=mode) as fake, tempfile.TemporaryDirectory() as d:
            inp = write_input(d, REQ[:100])
            out = Path(d) / "census_batch_output.csv"
            info = fetch.fetch_batch(inp, out, base_url=fake.url, retries=3, backoff=0.5, sleep=slept.append)
            self.assertEqual(len(fake.batch_requests), 2)
            self.assertEqual(slept, [0.5])
            self.assertEqual(info["attempts_per_chunk"], [2])
            self.assertEqual(out.read_bytes(), build_full(REQ[:100])[0])

    def test_server_error_is_retried_then_succeeds(self):
        slept = []
        with FakeCensus(batch_mode=lambda n, ids, full: 503 if n <= 2 else full) as fake, \
                tempfile.TemporaryDirectory() as d:
            inp = write_input(d, REQ[:10])
            fetch.fetch_batch(inp, Path(d) / "o.csv", base_url=fake.url, retries=3, backoff=1, sleep=slept.append)
            self.assertEqual(slept, [1, 2])

    def test_persistent_truncation_fails_and_writes_nothing(self):
        slept = []
        with FakeCensus(batch_mode=lambda n, ids, full: full[: len(full) // 2]) as fake, \
                tempfile.TemporaryDirectory() as d:
            inp = write_input(d, REQ[:100])
            with self.assertRaises(fetch.IncompleteResponse) as cm:
                fetch.fetch_batch(inp, Path(d) / "o.csv", base_url=fake.url, retries=2, backoff=1, sleep=slept.append)
            self.assertIn("incomplete", str(cm.exception))
            self.assertEqual(len(fake.batch_requests), 3)
            self.assertEqual(slept, [1, 2])
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["input.csv"])

    def test_late_chunk_failure_leaves_nothing_partial(self):
        # chunks 1 and 2 are fine, chunk 3 never completes: no file at all.
        def mode(n, ids, full):
            return full if ids[0] in (REQ[0]["address_id"], REQ[100]["address_id"]) else full[:40]
        with FakeCensus(batch_mode=mode) as fake, tempfile.TemporaryDirectory() as d:
            inp = write_input(d)
            with self.assertRaises(fetch.IncompleteResponse):
                fetch.fetch_batch(inp, Path(d) / "o.csv", base_url=fake.url, retries=1, backoff=0, sleep=lambda s: None)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["input.csv"])

    def test_existing_output_is_never_replaced(self):
        with FakeCensus() as fake, tempfile.TemporaryDirectory() as d:
            inp = write_input(d, REQ[:5])
            out = Path(d) / "census_batch_output.csv"
            out.write_bytes(b"precious")
            with self.assertRaises(fetch.OutputExists):
                fetch.fetch_batch(inp, out, base_url=fake.url, sleep=lambda s: None)
            self.assertEqual(out.read_bytes(), b"precious")
            self.assertEqual(fake.batch_requests, [])
            proc = run_script(FETCH, "batch", "--input", inp, "--out", out, "--base-url", fake.url)
            self.assertEqual(proc.returncode, 4)

    def test_input_with_a_header_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "in.csv"
            p.write_text("address_id,street_address,postal_city,state,zip\nA1,x,y,CA,90028\n")
            with self.assertRaises(fetch.FetchError):
                fetch.fetch_batch(p, Path(d) / "o.csv", base_url="http://127.0.0.1:9", sleep=lambda s: None)

    def test_places_lookup_saves_raw_bodies_and_nothing_partial_on_failure(self):
        batch, _, by_coord = build_full()
        with FakeCensus() as fake, tempfile.TemporaryDirectory() as d:
            bpath = Path(d) / "census_batch_output.csv"
            bpath.write_bytes(batch)
            out = Path(d) / "census_places_output.jsonl"
            info = fetch.fetch_places(bpath, out, base_url=fake.url, delay=0, sleep=lambda s: None,
                                      now=lambda: "2026-10-05T12:05:00Z")
            lines = out.read_text().splitlines()
            self.assertEqual(len(lines), 498)  # 500 less the no_match and the tie
            first = json.loads(lines[0])
            self.assertEqual(first["address_id"], "A0001")
            coords = "%s,%s" % (first["x"], first["y"])
            self.assertEqual(first["body"], by_coord[coords])  # verbatim
            q = fake.place_queries[0]
            self.assertEqual((q["benchmark"], q["vintage"], q["layers"], q["format"]),
                             (["Public_AR_Current"], ["Current_Current"], ["all"], ["json"]))
            meta = json.loads((Path(d) / "census_fetch_meta.json").read_text())
            self.assertEqual(meta["places"]["retrieved_at"], "2026-10-05T12:05:00Z")
            self.assertEqual(info["lookups"], 498)

        def bad(key):
            return (500, b"boom") if key == sorted(by_coord)[3] else None
        with FakeCensus(place_mode=bad) as fake, tempfile.TemporaryDirectory() as d:
            bpath = Path(d) / "census_batch_output.csv"
            bpath.write_bytes(batch)
            with self.assertRaises(fetch.IncompleteResponse):
                fetch.fetch_places(bpath, Path(d) / "p.jsonl", base_url=fake.url, retries=1, backoff=0,
                                   delay=0, sleep=lambda s: None)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["census_batch_output.csv"])

    def test_fake_server_end_to_end_through_the_validator(self):
        with FakeCensus() as fake, tempfile.TemporaryDirectory() as d:
            d = Path(d)
            inp = write_input(d)
            fetch.fetch_batch(inp, d / "census_batch_output.csv", base_url=fake.url, sleep=lambda s: None,
                              now=lambda: "2026-10-05T12:00:00Z")
            fetch.fetch_places(d / "census_batch_output.csv", d / "census_places_output.jsonl",
                               base_url=fake.url, delay=0, sleep=lambda s: None,
                               now=lambda: "2026-10-05T12:01:00Z")
            meta = parse.load_meta(d / "census_fetch_meta.json")
            recs, rep = parse.parse_response((d / "census_batch_output.csv").read_bytes(), REQ,
                                             (d / "census_places_output.jsonl").read_bytes(), meta)
            self.assertEqual(recs["A0001"]["request_timestamp"], "2026-10-05T12:00:00Z")
            self.assertEqual(rep["response_lines_malformed"], [])
            parse.write_jurisdictions(recs, d / "jurisdictions.json")
            proc = run_script(VALIDATE, "--jurisdictions", d / "jurisdictions.json",
                              "--report", d / "report.md", "--no-batch-input")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


# --------------------------------------------------------------------------
# the offline path end to end, ending at the real validator
# --------------------------------------------------------------------------

class OfflineEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.batch, cls.places, _ = build_full()
        (d / "resp.csv").write_bytes(cls.batch)
        (d / "places.jsonl").write_bytes(cls.places)
        cls.dir = d
        cls.out1 = d / "out1"
        cls.proc = run_script(RUN, "--offline", "--response", d / "resp.csv", "--places", d / "places.jsonl",
                              "--out-dir", cls.out1)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_offline_run_succeeds(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stdout + self.proc.stderr)
        self.assertTrue((self.out1 / "jurisdictions.json").exists())
        self.assertTrue((self.out1 / "census_batch_input.csv").exists())

    def test_jurisdictions_json_passes_the_existing_validator_exit_0(self):
        report = self.dir / "validation.md"
        proc = run_script(VALIDATE, "--jurisdictions", self.out1 / "jurisdictions.json",
                          "--report", report, "--no-batch-input")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("geocode validation: PASS", proc.stdout)
        self.assertIn("Status: **PASS**", report.read_text())

    def test_output_has_500_records_and_is_ascii(self):
        raw = (self.out1 / "jurisdictions.json").read_bytes()
        raw.decode("ascii")
        data = json.loads(raw)
        self.assertEqual(len(data), 500)
        self.assertEqual(list(data), [r["address_id"] for r in CSV_ROWS])

    def test_offline_run_twice_is_byte_identical(self):
        out2 = self.dir / "out2"
        proc = run_script(RUN, "--offline", "--response", self.dir / "resp.csv", "--places",
                          self.dir / "places.jsonl", "--out-dir", out2)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((out2 / "jurisdictions.json").read_bytes(), (self.out1 / "jurisdictions.json").read_bytes())
        self.assertEqual((out2 / "census_batch_input.csv").read_bytes(), (self.out1 / "census_batch_input.csv").read_bytes())

    def test_offline_never_loads_the_network_module(self):
        code = ("import sys; sys.path.insert(0, %r); import run; "
                "rc = run.main(['--offline', '--response', %r, '--places', %r, '--out-dir', %r]); "
                "print(rc, 'fetch' in sys.modules, 'urllib.request' in sys.modules, 'socket' in sys.modules)"
                % (str(GEO), str(self.dir / "resp.csv"), str(self.dir / "places.jsonl"), str(self.dir / "out3")))
        proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.strip().endswith("0 False False False"), proc.stdout)

    def test_offline_requires_explicit_response_and_out_dir(self):
        for args in (["--offline"], ["--offline", "--response", str(self.dir / "resp.csv")]):
            proc = run_script(RUN, *args)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("requires", proc.stderr)

    def test_skip_fetch_with_no_raw_files_says_so_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            proc = run_script(RUN, "--skip-fetch", "--out-dir", d)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("raw response not found", proc.stderr)
            self.assertFalse((Path(d) / "jurisdictions.json").exists())

    def test_skip_fetch_reads_meta_and_sets_request_timestamp(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "census_batch_output.csv").write_bytes(self.batch)
            (d / "census_places_output.jsonl").write_bytes(self.places)
            (d / "census_fetch_meta.json").write_text(json.dumps({"batch": {"retrieved_at": "2026-10-05T12:00:00Z"}}))
            proc = run_script(RUN, "--skip-fetch", "--out-dir", d)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads((d / "jurisdictions.json").read_text())
            self.assertEqual(data["A0001"]["request_timestamp"], "2026-10-05T12:00:00Z")

    def test_truncated_offline_response_still_validates_structure_but_is_all_flagged(self):
        # A cut response parses, every missing row stays unresolved and explicit.
        cut = self.batch[: len(self.batch) // 2]
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "r.csv").write_bytes(cut)
            proc = run_script(RUN, "--offline", "--response", d / "r.csv", "--out-dir", d / "o")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads((d / "o" / "jurisdictions.json").read_text())
            self.assertEqual(len(data), 500)
            for r in data.values():
                self.assertIsNone(r["legal_city"])  # no places file at all
                self.assertTrue(r["flags"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
