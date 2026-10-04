#!/usr/bin/env python3
"""Validate the jurisdiction resolver output. Trust nothing, check everything.

Run from the project root:

    python3 navigator/geocode/validate.py

Reads   navigator/out/jurisdictions.json  and  data/sample_addresses.csv
Writes  navigator/out/geocode_validation.md
        navigator/out/census_batch_input.csv   (cross-check artifact, see below)
Exit    0 when there are no hard failures, 1 otherwise, 2 on usage or
        precondition errors (for example the address CSV is missing).

Hard failure classes (any one makes the exit code non-zero):
  C1  id set: exactly 500 ids, every id present in the CSV, no duplicates
  C2  every record has all 14 required keys
  C3  postal-city fallback: unresolved records must have legal_city null, and a
      mailing name that is not an incorporated place (Dorchester, Roxbury, ...)
      must never appear as legal_city
  C4  legal_city must be "City, ST" with no Census suffix ("Berkeley city, CA")
  C5  year_built and units must equal the CSV, as int or null, never invented
  C6  lat and lon must be real numbers inside a plausible box for the state
  C7  integrity extras: method enum, zip kept as a 5 digit string (a float zip
      is the pandas coercion bug), passthrough fields equal to the CSV, flags
      is a list of strings

Everything else is a warning: reported, never fatal.

census_batch_input.csv is five columns, no header: address_id, street_address,
postal_city, state, zip. Blank zips stay blank, nothing is padded or altered.
If a file already exists at that path and its rows differ from what this script
would write, the existing file is left alone, ours goes next to it as
census_batch_input.validator.csv, and the difference is reported as a warning.
Delete the file to regenerate it.

Plain ASCII output only. Standard library only. Python 3.8+.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import re
import sys
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JURISDICTIONS = ROOT / "navigator" / "out" / "jurisdictions.json"
DEFAULT_ADDRESSES = ROOT / "data" / "sample_addresses.csv"
DEFAULT_REPORT = ROOT / "navigator" / "out" / "geocode_validation.md"
DEFAULT_BATCH_INPUT = ROOT / "navigator" / "out" / "census_batch_input.csv"

EXPECTED_TOTAL = 500

REQUIRED_KEYS = [
    "street_address", "postal_city", "state", "zip", "legal_city", "county",
    "state_code", "method", "match_quality", "lat", "lon", "year_built",
    "units", "flags",
]
METHODS = ("census_batch", "census_oneline", "unresolved")
BATCH_COLS = ["address_id", "street_address", "postal_city", "state", "zip"]

# README 4.1 expectation, keyed by the "City, ST" legal_city form.
EXPECTED_COUNTS = OrderedDict([
    ("Los Angeles, CA", 80),
    ("San Francisco, CA", 80),
    ("San Diego, CA", 50),
    ("Berkeley, CA", 40),
    ("Jersey City, NJ", 50),
    ("Hoboken, NJ", 40),
    ("Newark, NJ", 50),
    ("Boston, MA", 60),
    ("Cambridge, MA", 50),
])

# Mailing names that are not incorporated places. The first group is the
# coordinator's list and is mandatory. The second group is extra Los Angeles
# area neighborhood names (README 4.1 cites Van Nuys) added as a safety net.
MAILING_NAMES = [
    "Dorchester", "Roxbury", "Allston", "Brighton", "East Boston",
    "South Boston", "Hyde Park", "Jamaica Plain", "Mattapan", "San Ysidro",
]
EXTRA_MAILING_NAMES = [
    "Van Nuys", "North Hollywood", "Sherman Oaks", "Encino", "Woodland Hills",
    "Canoga Park", "Reseda", "Tarzana", "Sun Valley", "Pacoima", "Sylmar",
    "Chatsworth", "Northridge", "Granada Hills", "Mission Hills",
    "Panorama City", "Studio City", "Venice", "Wilmington", "San Pedro",
    "Harbor City", "Sunland", "Tujunga", "Winnetka", "Arleta", "Lake View Terrace",
    "Playa del Rey", "Pacific Beach", "La Jolla", "Ocean Beach",
    "Charlestown", "West Roxbury", "Roslindale", "Back Bay", "Fenway",
]
MAILING_SET = set(n.casefold() for n in MAILING_NAMES + EXTRA_MAILING_NAMES)

# Generous bounding boxes: (lat_min, lat_max, lon_min, lon_max).
STATE_BOX = {
    "CA": (32.0, 42.1, -124.6, -114.0),
    "NJ": (38.8, 41.4, -75.6, -73.8),
    "MA": (41.2, 43.0, -73.6, -69.8),
}
# Rough city boxes, used only for a warning when coordinates are in the state
# but nowhere near the city the record claims.
CITY_BOX = {
    "Los Angeles, CA": (33.70, 34.34, -118.67, -118.15),
    "San Francisco, CA": (37.69, 37.84, -122.54, -122.35),
    "San Diego, CA": (32.53, 33.11, -117.32, -116.90),
    "Berkeley, CA": (37.84, 37.91, -122.34, -122.23),
    "Jersey City, NJ": (40.62, 40.78, -74.13, -74.03),
    "Hoboken, NJ": (40.72, 40.77, -74.06, -74.00),
    "Newark, NJ": (40.66, 40.80, -74.28, -74.11),
    "Boston, MA": (42.20, 42.42, -71.20, -70.90),
    "Cambridge, MA": (42.34, 42.42, -71.17, -71.05),
}

CLASSES = OrderedDict([
    ("C1", "Address id set: exactly 500 ids, all present in the CSV, no duplicates"),
    ("C2", "Required keys: every record has all 14 keys"),
    ("C3", "Postal-city fallback: unresolved with legal_city, or mailing name as legal_city"),
    ("C4", "legal_city form: must be 'City, ST' with no Census suffix"),
    ("C5", "Facts: year_built and units must equal the CSV (int or null), never invented"),
    ("C6", "Coordinates: lat and lon plausible for the stated state"),
    ("C7", "Integrity: method enum, zip as 5 digit string, passthrough fields, flags list"),
])

LEGAL_RE = re.compile(r"^(?P<name>[^,]+), (?P<st>[A-Z]{2})$")
CENSUS_SUFFIX = re.compile(r"\s(city|town|village|borough|township|municipality|CDP|plantation)$")
ZIP_RE = re.compile(r"^\d{5}$")
MISSING = object()


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def ascii_cell(value, limit=None):
    """Make any value safe for a plain ASCII markdown table cell."""
    text = "" if value is None else str(value)
    text = text.replace("|", "/").replace("\r", " ").replace("\n", " ")
    text = text.encode("ascii", "backslashreplace").decode("ascii")
    if limit and len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def rel(path):
    """Display a path without leaking an absolute machine path."""
    path = Path(path)
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def split_legal(legal):
    """Return (name, state) for a 'City, ST' string, else (legal, None)."""
    m = LEGAL_RE.match(legal)
    if m and m.group("name") == m.group("name").strip() and m.group("name"):
        return m.group("name"), m.group("st")
    return legal, None


def legal_name(legal):
    """City name part of a legal_city string, lowercase, no state."""
    if not isinstance(legal, str):
        return None
    name, _ = split_legal(legal)
    name = CENSUS_SUFFIX.sub("", name)
    return name.strip().casefold()


def norm_text(s):
    return re.sub(r"\s+", " ", str(s)).strip().casefold()


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(ascii_cell(c) for c in r) + " |")
    return out


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def load_csv_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def load_json_strict(path):
    """Return (data, sha256, failures). Detects duplicate JSON keys."""
    failures = []
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return None, None, ["cannot read %s: %s" % (rel(path), exc.__class__.__name__)]
    sha = hashlib.sha256(raw).hexdigest()
    dups = []

    def hook(pairs):
        seen = {}
        for k, v in pairs:
            if k in seen:
                dups.append(k)
            seen[k] = v
        return seen

    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=hook)
    except (ValueError, UnicodeDecodeError) as exc:
        return None, sha, ["%s is not valid JSON: %s" % (rel(path), ascii_cell(exc, 120))]
    for k in dups:
        failures.append("duplicate JSON key %r (the later value silently wins)" % (k,))
    return data, sha, failures


def build_batch_bytes(rows):
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    for r in rows:
        writer.writerow([r[c] for c in BATCH_COLS])
    return buf.getvalue().encode("utf-8")


def parse_batch_rows(raw):
    text = raw.decode("utf-8-sig", "replace")
    return [row for row in csv.reader(io.StringIO(text, newline=""))]


def write_batch_input(rows, path):
    """Write or cross-check census_batch_input.csv. Returns a status dict."""
    path = Path(path)
    ours = build_batch_bytes(rows)
    info = {"path": rel(path), "status": None, "detail": [], "rows": len(rows)}
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(ours)
        info["status"] = "written"
        return info
    existing = path.read_bytes()
    if existing == ours:
        info["status"] = "identical"
        return info
    their_rows = parse_batch_rows(existing)
    our_rows = parse_batch_rows(ours)
    if their_rows == our_rows:
        info["status"] = "same_content_different_bytes"
        info["detail"].append("rows are identical but bytes differ (line endings or quoting)")
        return info
    alt = path.with_name(path.stem + ".validator" + path.suffix)
    alt.write_bytes(ours)
    info["status"] = "differs"
    info["alt"] = rel(alt)
    their = OrderedDict((r[0] if r else "", r) for r in their_rows)
    mine = OrderedDict((r[0], r) for r in our_rows)
    info["detail"].append("existing file has %d rows, expected %d" % (len(their_rows), len(our_rows)))
    if any(len(r) != 5 for r in their_rows):
        info["detail"].append("existing file has %d rows without exactly 5 columns"
                              % sum(1 for r in their_rows if len(r) != 5))
    if their_rows and their_rows[0] and their_rows[0][0] == "address_id":
        info["detail"].append("existing file starts with a header row (ours has none)")
    only_theirs = [k for k in their if k not in mine]
    only_ours = [k for k in mine if k not in their]
    if only_theirs:
        info["detail"].append("ids only in existing file: %s" % ", ".join(only_theirs[:10]))
    if only_ours:
        info["detail"].append("ids only in expected file: %s" % ", ".join(only_ours[:10]))
    changed = [k for k in mine if k in their and mine[k] != their[k]]
    info["detail"].append("%d ids with different field values" % len(changed))
    for k in changed[:10]:
        info["detail"].append("  %s expected %s got %s" % (k, mine[k][1:], their[k][1:]))
    return info


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

class Result(object):
    def __init__(self):
        self.failures = OrderedDict((c, []) for c in CLASSES)
        self.warnings = []  # (kind, id, message)

    def fail(self, cls, aid, msg):
        self.failures[cls].append((aid, msg))

    def warn(self, kind, aid, msg):
        self.warnings.append((kind, aid, msg))

    @property
    def failed(self):
        return any(self.failures[c] for c in self.failures)

    def failure_count(self):
        return sum(len(v) for v in self.failures.values())


def parse_fact(value):
    """CSV fact to int, None when blank, False when unparseable."""
    value = (value or "").strip()
    if value == "":
        return None
    if re.match(r"^\d+$", value):
        return int(value)
    return False


def check_record(res, aid, rec, row):
    if not isinstance(rec, dict):
        res.fail("C2", aid, "record is %s, not an object" % type(rec).__name__)
        return

    # C2 required keys
    missing = [k for k in REQUIRED_KEYS if k not in rec]
    if missing:
        res.fail("C2", aid, "missing keys: " + ", ".join(missing))

    method = rec.get("method", MISSING)
    if method is not MISSING and method not in METHODS:
        res.fail("C7", aid, "method %r is not one of %s" % (method, "/".join(METHODS)))
    unresolved = method == "unresolved"
    legal = rec.get("legal_city", MISSING)

    # C3 postal-city fallback
    if unresolved and legal is not MISSING and legal is not None:
        res.fail("C3", aid, "unresolved record carries legal_city %r (silent fallback to a guess)" % (legal,))
    if isinstance(legal, str):
        key = legal_name(legal)
        if key in MAILING_SET:
            res.fail("C3", aid, "legal_city %r is a mailing name, not an incorporated place "
                     "(postal_city was %r)" % (legal, row["postal_city"]))

    # C4 legal_city form
    if legal is not MISSING and legal is not None:
        if not isinstance(legal, str):
            res.fail("C4", aid, "legal_city is %s, not a string" % type(legal).__name__)
        else:
            m = LEGAL_RE.match(legal)
            name = m.group("name") if m else None
            if not m or not name or name != name.strip():
                res.fail("C4", aid, "legal_city %r is not in 'City, ST' form" % (legal,))
            else:
                if CENSUS_SUFFIX.search(name):
                    res.fail("C4", aid, "legal_city %r still carries a Census suffix" % (legal,))
                sc = rec.get("state_code", MISSING)
                if isinstance(sc, str) and sc != m.group("st"):
                    res.fail("C4", aid, "legal_city state %s disagrees with state_code %s" % (m.group("st"), sc))
                elif sc is None or sc is MISSING:
                    res.warn("state_code_null", aid, "legal_city %r set but state_code is null or missing" % (legal,))

    # C5 facts carried from the CSV
    for key in ("year_built", "units"):
        val = rec.get(key, MISSING)
        if val is MISSING:
            continue  # reported by C2
        truth = parse_fact(row.get(key))
        if truth is False:
            res.fail("C5", aid, "CSV %s %r is not an integer, cannot compare" % (key, row.get(key)))
            continue
        if val is None:
            if truth is not None:
                res.fail("C5", aid, "%s dropped: CSV has %d, output has null" % (key, truth))
        elif isinstance(val, bool) or not isinstance(val, int):
            res.fail("C5", aid, "%s is %s %r, must be int or null (float coercion?)"
                     % (key, type(val).__name__, val))
        elif truth is None:
            res.fail("C5", aid, "%s invented: CSV is blank, output has %d" % (key, val))
        elif val != truth:
            res.fail("C5", aid, "%s differs: CSV has %d, output has %d" % (key, truth, val))

    # C6 coordinates
    box = STATE_BOX.get(row["state"])
    lat = rec.get("lat", MISSING)
    lon = rec.get("lon", MISSING)
    if lat is not MISSING and lon is not MISSING:
        if unresolved:
            if lat is not None or lon is not None:
                res.warn("unresolved_with_coords", aid, "unresolved record has lat/lon %r, %r" % (lat, lon))
        elif lat is None or lon is None:
            res.fail("C6", aid, "resolved record has null coordinates (lat=%r lon=%r)" % (lat, lon))
        elif not (is_number(lat) and is_number(lon)):
            res.fail("C6", aid, "lat/lon not finite numbers: %r, %r" % (lat, lon))
        elif box is None:
            res.fail("C6", aid, "no plausibility box for state %r" % (row["state"],))
        else:
            la0, la1, lo0, lo1 = box
            if not (la0 <= lat <= la1 and lo0 <= lon <= lo1):
                res.fail("C6", aid, "lat %s lon %s outside the plausible range for %s" % (lat, lon, row["state"]))
            elif isinstance(legal, str) and legal in CITY_BOX:
                a0, a1, b0, b1 = CITY_BOX[legal]
                if not (a0 <= lat <= a1 and b0 <= lon <= b1):
                    res.warn("outside_city_box", aid, "%s lat %s lon %s is outside the rough box for that city"
                             % (legal, lat, lon))

    # C7 integrity extras
    flags = rec.get("flags", MISSING)
    if flags is not MISSING and not (isinstance(flags, list) and all(isinstance(f, str) for f in flags)):
        res.fail("C7", aid, "flags must be a list of strings, got %r" % (flags,))
    sc = rec.get("state_code", MISSING)
    if sc is not MISSING and sc is not None and not (isinstance(sc, str) and re.match(r"^[A-Z]{2}$", sc)):
        res.fail("C7", aid, "state_code %r is not a 2 letter code" % (sc,))
    elif isinstance(sc, str) and sc != row["state"]:
        res.warn("state_code_differs", aid, "state_code %s differs from the CSV state %s" % (sc, row["state"]))

    zval = rec.get("zip", MISSING)
    if zval is not MISSING:
        csv_zip = row["zip"]
        if zval is not None and not isinstance(zval, str):
            res.fail("C7", aid, "zip is %s %r, must be a string (float coercion drops leading zeros)"
                     % (type(zval).__name__, zval))
        else:
            zs = zval or ""
            if zs and not ZIP_RE.match(zs):
                res.fail("C7", aid, "zip %r is not a 5 digit string" % (zs,))
            elif csv_zip and zs != csv_zip:
                res.fail("C7", aid, "zip %r differs from the CSV zip %r" % (zs, csv_zip))
            elif not csv_zip and zs:
                res.warn("zip_filled", aid, "CSV zip is blank, output has %r" % (zs,))
    for key in ("street_address", "postal_city", "state"):
        val = rec.get(key, MISSING)
        if val is MISSING or val == row[key]:
            continue
        if isinstance(val, str) and norm_text(val) == norm_text(row[key]):
            res.warn("passthrough_normalised", aid, "%s %r vs CSV %r differ only in case or spacing" % (key, val, row[key]))
        else:
            res.fail("C7", aid, "%s %r differs from the CSV value %r" % (key, val, row[key]))

    # warnings about completeness
    if not unresolved and method is not MISSING and legal is None:
        if isinstance(flags, list) and flags:
            res.warn("resolved_no_place", aid, "resolved with no incorporated place, flags: %s" % ", ".join(map(str, flags)))
        else:
            res.warn("resolved_no_place_unflagged", aid, "resolved with legal_city null and NO flag (unincorporated must be flagged)")
    if unresolved and not (isinstance(flags, list) and flags):
        res.warn("unresolved_no_flag", aid, "unresolved record has no flags explaining why")


def validate(records, csv_rows, load_failures=()):
    res = Result()
    for msg in load_failures:
        res.fail("C1", "-", msg)

    csv_by_id = OrderedDict()
    for r in csv_rows:
        aid = r["address_id"]
        if aid in csv_by_id:
            res.fail("C1", aid, "address_id appears twice in the sample CSV")
        csv_by_id[aid] = r
    if len(csv_rows) != EXPECTED_TOTAL:
        res.fail("C1", "-", "sample CSV has %d rows, expected %d" % (len(csv_rows), EXPECTED_TOTAL))

    if records is None:
        return res
    if not isinstance(records, dict):
        res.fail("C1", "-", "top level must be an object mapping address_id to record, got %s"
                 % type(records).__name__)
        return res

    ids = list(records)
    if len(ids) != EXPECTED_TOTAL:
        res.fail("C1", "-", "found %d address_ids, expected exactly %d" % (len(ids), EXPECTED_TOTAL))
    for aid in ids:
        if aid not in csv_by_id:
            res.fail("C1", aid, "address_id is not in sample_addresses.csv")
    for aid in csv_by_id:
        if aid not in records:
            res.fail("C1", aid, "address_id from the CSV is missing from the output")
    for aid in ids:
        if aid in csv_by_id:
            check_record(res, aid, records[aid], csv_by_id[aid])
    return res


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

MAX_EXAMPLES = 15


def rec_of(records, aid):
    rec = records.get(aid) if isinstance(records, dict) else None
    return rec if isinstance(rec, dict) else {}


def render_report(res, records, csv_rows, meta, batch_info):
    csv_by_id = OrderedDict((r["address_id"], r) for r in csv_rows)
    usable = isinstance(records, dict)
    L = []
    status = "FAIL" if res.failed else "PASS"
    L.append("# Geocode validation report")
    L.append("")
    L.append("Status: **%s** (%d hard failure(s), %d warning(s))" % (status, res.failure_count(), len(res.warnings)))
    L.append("")
    L.append("Generated by `navigator/geocode/validate.py`. This report is regenerated on every run.")
    L.append("")
    L.append("## Inputs")
    L.append("")
    L.extend(table(["file", "detail"], [
        [meta["jurisdictions"], meta.get("jur_detail", "")],
        [meta["addresses"], "%d rows" % len(csv_rows)],
    ]))
    L.append("")

    # method counts
    recs = [rec_of(records, a) for a in records] if usable else []
    methods = Counter(r.get("method", "(missing)") if isinstance(r.get("method", "(missing)"), str) else "(invalid)" for r in recs)
    if usable:
        resolved = sum(v for k, v in methods.items() if k in ("census_batch", "census_oneline"))
        L.append("Records: %d. Resolved: %d. Unresolved: %d. By method: %s."
                 % (len(records), resolved, methods.get("unresolved", 0),
                    ", ".join("%s=%d" % (k, v) for k, v in sorted(methods.items())) or "none"))
        L.append("")

    # hard failures
    L.append("## Hard failures")
    L.append("")
    L.extend(table(["class", "rule", "count"], [[c, CLASSES[c], len(res.failures[c])] for c in CLASSES]))
    L.append("")
    for c in CLASSES:
        items = res.failures[c]
        if not items:
            continue
        L.append("### %s: %s" % (c, CLASSES[c]))
        L.append("")
        for aid, msg in items[:MAX_EXAMPLES]:
            L.append("- %s: %s" % (ascii_cell(aid), ascii_cell(msg, 220)))
        if len(items) > MAX_EXAMPLES:
            L.append("- ... and %d more" % (len(items) - MAX_EXAMPLES))
        L.append("")
    if not res.failed:
        L.append("None.")
        L.append("")

    # warnings summary
    L.append("## Warnings")
    L.append("")
    if batch_info and batch_info["status"] == "differs":
        L.append("- **census_batch_input.csv DIFFERS from the validator's expected file.** "
                 "That is a signal worth investigating; see the cross-check section.")
    kinds = Counter(k for k, _, _ in res.warnings)
    if kinds:
        L.extend(table(["warning kind", "count"], sorted(kinds.items())))
        L.append("")
        for kind in sorted(kinds):
            items = [(a, m) for k, a, m in res.warnings if k == kind]
            L.append("### %s" % kind)
            L.append("")
            for aid, msg in items[:MAX_EXAMPLES]:
                L.append("- %s: %s" % (ascii_cell(aid), ascii_cell(msg, 200)))
            if len(items) > MAX_EXAMPLES:
                L.append("- ... and %d more" % (len(items) - MAX_EXAMPLES))
            L.append("")
    else:
        L.append("No record-level warnings.")
        L.append("")

    if not usable:
        L.append("## Remaining sections skipped")
        L.append("")
        L.append("The output file could not be used as an id to record mapping, so the count, "
                 "difference and missing-fact sections were not computed.")
        L.append("")
    else:
        def legal_of(aid):
            v = rec_of(records, aid).get("legal_city")
            return v if isinstance(v, str) else None

        # counts by legal_city
        L.append("## Counts by legal_city versus the README expectation")
        L.append("")
        actual = Counter(legal_of(a) for a in records)
        rows = []
        for city, exp in EXPECTED_COUNTS.items():
            n = actual.get(city, 0)
            rows.append([city, exp, n, "%+d" % (n - exp), "ok" if n == exp else "DEVIATION"])
        others = sorted((c for c in actual if c not in EXPECTED_COUNTS and c is not None),
                        key=lambda c: (-actual[c], c))
        for c in others:
            rows.append([c, 0, actual[c], "%+d" % actual[c], "NOT IN README TABLE"])
        rows.append(["(null legal_city)", 0, actual.get(None, 0), "%+d" % actual.get(None, 0),
                     "unresolved or unincorporated" if actual.get(None, 0) else "ok"])
        L.extend(table(["legal_city", "expected", "actual", "delta", "note"], rows))
        L.append("")
        L.append("A deviation is a warning, not a failure: some addresses may legitimately resolve "
                 "outside the README table. Each one still needs an explanation.")
        L.append("")

        # legal differs from postal
        L.append("## Addresses where legal_city differs from postal_city")
        L.append("")
        diffs = []
        for aid, row in csv_by_id.items():
            lc = legal_of(aid)
            if lc is None or aid not in records:
                continue
            if legal_name(lc) != norm_text(row["postal_city"]):
                diffs.append((aid, row, lc))
        L.append("%d of %d resolved addresses." % (len(diffs), sum(1 for a in csv_by_id if legal_of(a))))
        L.append("")
        if diffs:
            pairs = Counter((row["postal_city"], row["state"], lc) for _, row, lc in diffs)
            L.extend(table(["postal_city", "state", "legal_city", "rows"],
                           [[p, s, l, n] for (p, s, l), n in sorted(pairs.items())]))
            L.append("")
            L.extend(table(["address_id", "street_address", "postal_city", "legal_city", "method"],
                           [[a, r["street_address"], r["postal_city"], lc, rec_of(records, a).get("method", "")]
                            for a, r, lc in diffs]))
            L.append("")

        # unresolved
        L.append("## Unresolved addresses")
        L.append("")
        unres = [a for a in records if a in csv_by_id and rec_of(records, a).get("method") == "unresolved"]
        if unres:
            urows = []
            for a in unres:
                rec = rec_of(records, a)
                fl = rec.get("flags")
                reason = ", ".join(map(str, fl)) if isinstance(fl, list) and fl else "(no reason given)"
                urows.append([a, csv_by_id[a]["street_address"], csv_by_id[a]["postal_city"],
                              csv_by_id[a]["state"], reason, rec.get("match_quality", "")])
            L.extend(table(["address_id", "street_address", "postal_city", "state", "reason (flags)", "match_quality"], urows))
        else:
            L.append("None.")
        L.append("")

        # NJ out-of-range zips
        L.append("## NJ rows whose supplied zip is outside the NJ range")
        L.append("")
        nj = [(a, r) for a, r in csv_by_id.items()
              if r["state"] == "NJ" and r["zip"] and not r["zip"].startswith(("07", "08"))]
        nrows, flagged = [], 0
        for a, r in nj:
            rec = rec_of(records, a)
            fl = rec.get("flags") if isinstance(rec.get("flags"), list) else []
            zf = [f for f in fl if "zip" in str(f).casefold()]
            if zf:
                flagged += 1
            nrows.append([a, r["street_address"], r["postal_city"], r["zip"], legal_of(a) or "(null)",
                          "flagged: " + ", ".join(zf) if zf else "NOT FLAGGED"])
        L.append("NJ rows with out-of-range zip: %d. flagged: %d. not flagged: %d." % (len(nj), flagged, len(nj) - flagged))
        L.append("")
        if nrows:
            L.extend(table(["address_id", "street_address", "postal_city", "supplied zip", "legal_city", "zip flag"], nrows))
            L.append("")
        blank = Counter(r["state"] for r in csv_rows if not r["zip"])
        L.append("Blank supplied zips in the CSV: %d (%s). These cannot be padded; "
                 "street, city and state carry the match." % (sum(blank.values()),
                                                               ", ".join("%s=%d" % kv for kv in sorted(blank.items()))))
        L.append("")

        # missing facts per legal city (CSV truth)
        L.append("## Missing year_built and units by legal_city")
        L.append("")
        L.append("Counted from the CSV for the ids in the output, grouped by the record's legal_city.")
        L.append("")
        grp = defaultdict(lambda: [0, 0, 0])
        for a in records:
            if a not in csv_by_id:
                continue
            key = legal_of(a) or "(null legal_city)"
            g = grp[key]
            g[0] += 1
            g[1] += 0 if csv_by_id[a]["year_built"].strip() else 1
            g[2] += 0 if csv_by_id[a]["units"].strip() else 1
        mrows = [[k, v[0], v[1], v[2]] for k, v in sorted(grp.items())]
        mrows.append(["TOTAL", sum(v[0] for v in grp.values()), sum(v[1] for v in grp.values()),
                      sum(v[2] for v in grp.values())])
        L.extend(table(["legal_city", "rows", "missing year_built", "missing units"], mrows))
        L.append("")

    # batch input cross-check
    L.append("## census_batch_input.csv cross-check")
    L.append("")
    if batch_info is None:
        L.append("Not written in this run.")
    else:
        L.append("File: `%s`. Status: **%s**. Rows expected: %d, five columns, no header, "
                 "blank zips left blank, nothing padded." % (batch_info["path"], batch_info["status"], batch_info["rows"]))
        if batch_info["status"] == "differs":
            L.append("")
            L.append("An existing file differs from the validator's version, which was written to `%s` "
                     "and left the existing file untouched." % batch_info.get("alt", "?"))
            for d in batch_info["detail"]:
                L.append("- " + ascii_cell(d, 240))
        elif batch_info["detail"]:
            for d in batch_info["detail"]:
                L.append("- " + ascii_cell(d, 240))
    L.append("")
    text = "\n".join(L) + "\n"
    return text.encode("ascii", "backslashreplace").decode("ascii")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def run(jurisdictions, addresses, report, batch_input):
    jurisdictions, addresses, report = Path(jurisdictions), Path(addresses), Path(report)
    if not addresses.exists():
        print("ERROR: %s not found" % rel(addresses), file=sys.stderr)
        return 2, None
    csv_rows = load_csv_rows(addresses)

    batch_info = None
    if batch_input:
        batch_info = write_batch_input(csv_rows, batch_input)

    meta = {"jurisdictions": rel(jurisdictions), "addresses": rel(addresses)}
    if jurisdictions.exists():
        records, sha, load_failures = load_json_strict(jurisdictions)
        meta["jur_detail"] = "sha256 %s, %d bytes" % (sha, jurisdictions.stat().st_size) if sha else "unreadable"
    else:
        records, load_failures = None, ["%s does not exist yet" % rel(jurisdictions)]
        meta["jur_detail"] = "NOT FOUND"

    res = validate(records, csv_rows, load_failures)
    text = render_report(res, records, csv_rows, meta, batch_info)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(text, encoding="ascii")

    print("geocode validation: %s" % ("FAIL" if res.failed else "PASS"))
    for c in CLASSES:
        n = len(res.failures[c])
        if n:
            print("  %s: %d failure(s)  %s" % (c, n, CLASSES[c]))
    print("  warnings: %d" % len(res.warnings))
    if batch_info:
        print("  census_batch_input.csv: %s" % batch_info["status"])
    print("  report: %s" % rel(report))
    return (1 if res.failed else 0), res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--jurisdictions", default=str(DEFAULT_JURISDICTIONS))
    ap.add_argument("--addresses", default=str(DEFAULT_ADDRESSES))
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    ap.add_argument("--batch-input", default=str(DEFAULT_BATCH_INPUT),
                    help="where to write the Census batch input cross-check file")
    ap.add_argument("--no-batch-input", action="store_true", help="skip writing the batch input file")
    args = ap.parse_args(argv)
    code, _ = run(args.jurisdictions, args.addresses, args.report,
                  None if args.no_batch_input else args.batch_input)
    return code


if __name__ == "__main__":
    sys.exit(main())
