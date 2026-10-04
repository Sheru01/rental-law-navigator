#!/usr/bin/env python3
"""Stage 1 of the geocoding pipeline: addresses to normalized request rows.

No network. Pure text work, fully deterministic.

    python3 navigator/geocode/normalize.py [--addresses CSV] [--out-dir DIR]

Reads   data/sample_addresses.csv
Writes  navigator/out/census_batch_input.csv   (five columns, no header)
        or, when that file already exists with different content, writes
        navigator/out/census_batch_input.pipeline.csv beside it and leaves
        the existing file untouched (the validator owns the plain name).

Rules this module enforces:

* The ORIGINAL street address is preserved verbatim next to the normalized
  form. Both travel to the end of the pipeline. Nothing is overwritten.
* Zips are never padded, trimmed or altered. A blank zip stays blank. (The
  earlier "NJ zips lost a leading zero" claim was wrong and is retracted;
  07030 is stored correctly in the CSV.)
* postal_city and state are passed through untouched.
* Every change to the street text is recorded, per row, as a list of short
  codes. An empty list means the street was sent exactly as supplied.
* year_built and units are carried as int or None, never invented. A value
  that is not blank and not a plain integer raises, it is never repaired.

Street normalizations (applied in this order, each one recorded):

  collapse_whitespace       runs of spaces become one, ends trimmed
  strip_unit:<text>         trailing APT / UNIT / STE / # designator removed
  strip_trailing_period     "SUMMIT AVE." becomes "SUMMIT AVE"
  range_to_first_number:    "1031-1035 CLINTON ST" is sent as "1031 CLINTON ST"
  multi_number_to_first:    "238 & 242 GARFIELD AVE" is sent as "238 GARFIELD AVE"
  drop_fractional_house_number:  "14.5" is sent as "14"

The range rules are a request-shaping choice, not a fact claim: the geocoder
needs one house number, and every number in a parcel range lies on the same
street in the same jurisdiction. The original range survives in
original_address and in the recorded code.

Plain ASCII output. Standard library only.
"""
import argparse
import csv
import io
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ADDRESSES = ROOT / "data" / "sample_addresses.csv"
DEFAULT_OUT_DIR = ROOT / "navigator" / "out"

BATCH_FILE = "census_batch_input.csv"
PIPELINE_FILE = "census_batch_input.pipeline.csv"
BATCH_COLS = ["address_id", "street_address", "postal_city", "state", "zip"]
SOURCE_FILE = "data/sample_addresses.csv"

_WS_RE = re.compile(r"\s+")
_UNIT_RE = re.compile(r"\s+(?:APT|APARTMENT|UNIT|STE|SUITE|#)\.?\s*#?\s*[A-Za-z0-9-]+$", re.IGNORECASE)
_TRAILING_PERIOD_RE = re.compile(r"\.+$")
_NUM = r"\d+(?:\.\d+)?[A-Za-z]?"
_RANGE_RE = re.compile(r"^(?P<first>" + _NUM + r")\s*-\s*(?P<second>" + _NUM + r")\s*-?\s+(?P<rest>\S.*)$")
_MULTI_RE = re.compile(r"^(?P<first>" + _NUM + r")\s*&\s*(?P<second>" + _NUM + r")\s+(?P<rest>\S.*)$")
_FRACTION_RE = re.compile(r"^(?P<whole>\d+)\.\d+(?P<suffix>[A-Za-z]?)$")


def load_addresses(path=DEFAULT_ADDRESSES):
    """Return the CSV rows as dicts of verbatim strings (no stripping)."""
    with open(str(path), encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def normalize_street(street):
    """Return (normalized_street, [normalization codes]) for one street."""
    codes = []
    s = street

    collapsed = _WS_RE.sub(" ", s).strip()
    if collapsed != s:
        codes.append("collapse_whitespace")
    s = collapsed

    m = _UNIT_RE.search(s)
    if m:
        # Only strip when something that looks like a street remains.
        head = s[: m.start()].rstrip()
        if head:
            codes.append("strip_unit:" + m.group(0).strip())
            s = head

    stripped = _TRAILING_PERIOD_RE.sub("", s).rstrip()
    if stripped != s:
        codes.append("strip_trailing_period")
        s = stripped

    first = None
    m = _RANGE_RE.match(s)
    if m:
        first, kind = m.group("first"), "range_to_first_number"
        span = s[: m.start("rest")].strip().rstrip("-").strip()
    else:
        m = _MULTI_RE.match(s)
        if m:
            first, kind = m.group("first"), "multi_number_to_first"
            span = s[: m.start("rest")].strip()
    if first is not None:
        new_first = first
        frac = _FRACTION_RE.match(first)
        s = m.group("rest")
        if frac:
            new_first = frac.group("whole") + frac.group("suffix")
        codes.append("%s:%s->%s" % (kind, span, first))
        if new_first != first:
            codes.append("drop_fractional_house_number:%s->%s" % (first, new_first))
        s = new_first + " " + s
    return s, codes


def parse_fact(value, field, address_id):
    """CSV year_built or units to int or None. Never repairs a bad value."""
    value = (value or "").strip()
    if value == "":
        return None
    if re.match(r"^\d+$", value):
        return int(value)
    raise ValueError("%s: %s=%r is not blank and not a plain integer; "
                     "refusing to repair it" % (address_id, field, value))


def normalize_rows(rows):
    """CSV rows to request rows. Deterministic, order preserving.

    Each request row carries:
      address_id, original_address, normalized_address, normalizations,
      postal_city, state, zip, year_built, units, source_file, source_row
    source_row is the line number in the CSV (header is line 1).
    """
    out = []
    for idx, r in enumerate(rows):
        aid = r["address_id"]
        normalized, codes = normalize_street(r["street_address"])
        out.append({
            "address_id": aid,
            "original_address": r["street_address"],
            "normalized_address": normalized,
            "normalizations": codes,
            "postal_city": r["postal_city"],
            "state": r["state"],
            "zip": r["zip"],
            "year_built": parse_fact(r.get("year_built"), "year_built", aid),
            "units": parse_fact(r.get("units"), "units", aid),
            "source_file": SOURCE_FILE,
            "source_row": idx + 2,
        })
    return out


def build_batch_bytes(request_rows):
    """The exact bytes sent to Census: five columns, no header, LF endings."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    for r in request_rows:
        writer.writerow([r["address_id"], r["normalized_address"],
                         r["postal_city"], r["state"], r["zip"]])
    return buf.getvalue().encode("utf-8")


def write_batch_input(request_rows, out_dir=DEFAULT_OUT_DIR):
    """Write the batch input file. Returns (path, status).

    status is "written" (no file existed), "identical" (an existing file
    already has these exact bytes) or "differs_written_beside" (an existing
    file differs, so ours went to census_batch_input.pipeline.csv and the
    existing file was left alone).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = build_batch_bytes(request_rows)
    plain = out_dir / BATCH_FILE
    if not plain.exists():
        plain.write_bytes(data)
        return plain, "written"
    if plain.read_bytes() == data:
        return plain, "identical"
    beside = out_dir / PIPELINE_FILE
    beside.write_bytes(data)
    return beside, "differs_written_beside"


def send_path(out_dir=DEFAULT_OUT_DIR):
    """Which input file fetch.py should send: the pipeline file if present."""
    out_dir = Path(out_dir)
    beside = out_dir / PIPELINE_FILE
    return beside if beside.exists() else out_dir / BATCH_FILE


def summarize(request_rows):
    counts = Counter()
    changed = 0
    for r in request_rows:
        if r["normalizations"]:
            changed += 1
        for c in r["normalizations"]:
            counts[c.split(":", 1)[0]] += 1
    return changed, counts


def main(argv=None):
    ap = argparse.ArgumentParser(description="Normalize addresses for the Census batch geocoder (no network).")
    ap.add_argument("--addresses", default=str(DEFAULT_ADDRESSES))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    args = ap.parse_args(argv)
    rows = normalize_rows(load_addresses(args.addresses))
    path, status = write_batch_input(rows, args.out_dir)
    changed, counts = summarize(rows)
    try:
        shown = path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        shown = path.name
    print("normalize: %d rows, %d with at least one normalization" % (len(rows), changed))
    for k in sorted(counts):
        print("  %s: %d" % (k, counts[k]))
    print("batch input: %s (%s)" % (shown, status))
    if status == "differs_written_beside":
        print("  note: %s already exists with different content and was left untouched" % BATCH_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
