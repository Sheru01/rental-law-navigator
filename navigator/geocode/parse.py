#!/usr/bin/env python3
"""Stage 3 of the geocoding pipeline: raw Census responses to jurisdictions.json.

No network. Pure functions over bytes. Fully fixture-tested.

    python3 navigator/geocode/parse.py [--response CSV] [--places JSONL]
                                       [--meta JSON] [--addresses CSV] [--out JSON]

WHAT THE CENSUS RETURNS (two raw files, both saved verbatim by fetch.py)

1. The geographies addressbatch response: CSV, no header, one line per
   request id, quoted fields.
     Match     12 fields: id, input address, "Match", "Exact"|"Non_Exact",
               matched address, "lon,lat", TIGER line id, side,
               state FIPS, county FIPS, tract, block
     No_Match  3 fields:  id, input address, "No_Match"
     Tie       3 fields:  id, input address, "Tie"
   The batch endpoint returns state, county, tract and block only. It does
   NOT return the incorporated place. That is why there is a second file.

2. The place lookup, one JSON object per line (JSONL), one line per matched
   address, produced by fetch.py from the batch coordinates through the
   Census geographies/coordinates endpoint:
     {"address_id", "x", "y", "url", "http_status", "body"}
   where body is the raw response text. Its JSON carries
   result.geographies["Incorporated Places"], ["County Subdivisions"],
   ["Counties"], ["States"] and more.

CORRECTNESS RULES (non-negotiable, each one tested)

* legal_city is the INCORPORATED PLACE, as "City, ST", with the Census
  suffix stripped ("Berkeley city" becomes "Berkeley"). A county
  subdivision is never the legal city.
* postal_city is NEVER read when building legal_city. There is no fallback.
  A mailing name (Dorchester, Roxbury, ...) coming out as legal_city is
  rejected by a last-line guard.
* no_match, tie, truncated, malformed, state-mismatch, implausible-coordinate
  and failed place-lookup rows keep legal_city null and method "unresolved",
  and a flag says why. Nothing is guessed.
* An empty incorporated-place layer on a trusted lookup is flagged as
  "probably unincorporated". No city is inferred. The county subdivision is
  kept in geography_layers for a human to look at.
* A supplied zip that disagrees with the geocoded zip is flagged, never
  corrected. A supplied zip outside its state's range is flagged.
* year_built and units come from the CSV only.

Plain ASCII output. Standard library only.
"""
import argparse
import csv
import json
import math
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import normalize  # noqa: E402

ROOT = _HERE.parents[1]
DEFAULT_OUT_DIR = ROOT / "navigator" / "out"
DEFAULT_RESPONSE = DEFAULT_OUT_DIR / "census_batch_output.csv"
DEFAULT_PLACES = DEFAULT_OUT_DIR / "census_places_output.jsonl"
DEFAULT_META = DEFAULT_OUT_DIR / "census_fetch_meta.json"
DEFAULT_JURISDICTIONS = DEFAULT_OUT_DIR / "jurisdictions.json"

PROVIDER = "census_geographies"
BENCHMARK = "Public_AR_Current"
VINTAGE = "Current_Current"

MATCH_FIELDS = 12
STATUSES = ("match_exact", "match_non_exact", "tie", "no_match", "not_attempted")

# Never allowed as a legal_city. Mailing names, not incorporated places.
MAILING_NAMES = frozenset(n.casefold() for n in [
    "Dorchester", "Roxbury", "Allston", "Brighton", "East Boston",
    "South Boston", "Hyde Park", "Jamaica Plain", "Mattapan", "San Ysidro",
    "Van Nuys",
])

# Generous state boxes: (lat_min, lat_max, lon_min, lon_max).
STATE_BOX = {
    "CA": (32.0, 42.1, -124.6, -114.0),
    "NJ": (38.8, 41.4, -75.6, -73.8),
    "MA": (41.2, 43.0, -73.6, -69.8),
}

# Supplied-zip plausibility per state (inclusive 5 digit string ranges).
ZIP_RANGES = {
    "NJ": [("07000", "08999")],
    "CA": [("90000", "96199")],
    "MA": [("01000", "02799"), ("05501", "05544")],
}

FIPS_STATE = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO",
    "09": "CT", "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI",
    "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
    "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
    "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
    "54": "WV", "55": "WI", "56": "WY", "72": "PR",
}

# County names for the counties the nine target cities sit in. Used only when
# the place lookup did not return a county name. Anything else stays null.
COUNTY_FALLBACK = {
    ("CA", "037"): "Los Angeles County",
    ("CA", "075"): "San Francisco County",
    ("CA", "073"): "San Diego County",
    ("CA", "001"): "Alameda County",
    ("NJ", "017"): "Hudson County",
    ("NJ", "013"): "Essex County",
    ("MA", "025"): "Suffolk County",
    ("MA", "017"): "Middlesex County",
}

_SUFFIX_RE = re.compile(
    r"\s+(?:city and borough|metropolitan government|consolidated government|"
    r"charter township|municipality|township|borough|village|town|city|"
    r"plantation|CDP)$")
_ZIP5_RE = re.compile(r"^(\d{5})(?:-\d{4})?$")


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def strip_place_suffix(name):
    """'Berkeley city' -> 'Berkeley'. One trailing Census suffix only."""
    return _SUFFIX_RE.sub("", name.strip(), count=1).strip()


def _finite(x):
    return isinstance(x, float) and math.isfinite(x)


def _decode(raw):
    return raw.decode("utf-8-sig", "replace")


def _split_lines(raw):
    """Yield (1-based line number, text) for every line, CR and LF stripped."""
    text = _decode(raw)
    for i, line in enumerate(text.split("\n"), start=1):
        yield i, line.rstrip("\r")


def zip_in_state_range(state, zip5):
    ranges = ZIP_RANGES.get(state)
    if not ranges or not re.match(r"^\d{5}$", zip5 or ""):
        return None  # cannot tell
    return any(lo <= zip5 <= hi for lo, hi in ranges)


# --------------------------------------------------------------------------
# batch response lines
# --------------------------------------------------------------------------

def parse_batch_lines(raw):
    """Parse a raw addressbatch response.

    Returns (entries, malformed).
      entries   list of dicts, one per well-formed or attributable line, in
                file order, each with line_no, id and kind in
                {"match", "no_match", "tie", "malformed"}
      malformed list of {line_no, id (or None), reason, text} for every line
                that could not be used, whether or not an id was recovered
    """
    entries, malformed = [], []
    for line_no, line in _split_lines(raw):
        if not line.strip():
            continue
        try:
            fields = next(csv.reader([line], strict=True))
        except (csv.Error, StopIteration) as exc:
            # A cut-off line: recover the leading field as the id if we can,
            # so the row is flagged as malformed rather than just missing.
            lead = re.match(r'^\s*"?([^",]+)', line)
            rid = lead.group(1).strip() if lead else None
            malformed.append({"line_no": line_no, "id": rid,
                              "reason": "csv_error:" + exc.__class__.__name__,
                              "text": line[:200]})
            if rid:
                entries.append({"line_no": line_no, "id": rid, "kind": "malformed"})
            continue
        rid = fields[0].strip() if fields else ""
        if len(fields) < 3 or not rid:
            malformed.append({"line_no": line_no, "id": rid or None,
                              "reason": "too_few_fields:%d" % len(fields),
                              "text": line[:200]})
            if rid:
                entries.append({"line_no": line_no, "id": rid, "kind": "malformed"})
            continue
        indicator = fields[2].strip()
        if indicator == "No_Match":
            entries.append({"line_no": line_no, "id": rid, "kind": "no_match",
                            "input_address": fields[1]})
        elif indicator == "Tie":
            entries.append({"line_no": line_no, "id": rid, "kind": "tie",
                            "input_address": fields[1]})
        elif indicator == "Match":
            reason = None
            lon = lat = None
            if len(fields) != MATCH_FIELDS:
                reason = "match_line_has_%d_fields_expected_%d" % (len(fields), MATCH_FIELDS)
            elif fields[3].strip() not in ("Exact", "Non_Exact"):
                reason = "unknown_match_type:" + fields[3].strip()[:20]
            else:
                coords = fields[5].strip().split(",")
                try:
                    if len(coords) != 2:
                        raise ValueError("coordinates")
                    lon, lat = float(coords[0]), float(coords[1])
                    if not (_finite(lon) and _finite(lat)):
                        raise ValueError("coordinates")
                except ValueError:
                    reason = "unparseable_coordinates"
            if reason:
                malformed.append({"line_no": line_no, "id": rid, "reason": reason,
                                  "text": line[:200]})
                entries.append({"line_no": line_no, "id": rid, "kind": "malformed"})
                continue
            entries.append({
                "line_no": line_no, "id": rid, "kind": "match",
                "input_address": fields[1],
                "match_type": fields[3].strip(),
                "matched_address": fields[4],
                "coordinates": fields[5].strip(),
                "lon": lon, "lat": lat,
                "tiger_line_id": fields[6].strip(),
                "side": fields[7].strip(),
                "state_fips": fields[8].strip(),
                "county_fips": fields[9].strip(),
                "tract": fields[10].strip(),
                "block": fields[11].strip(),
            })
        else:
            malformed.append({"line_no": line_no, "id": rid,
                              "reason": "unknown_match_indicator:" + indicator[:20],
                              "text": line[:200]})
            entries.append({"line_no": line_no, "id": rid, "kind": "malformed"})
    return entries, malformed


# --------------------------------------------------------------------------
# place lookup lines
# --------------------------------------------------------------------------

def parse_places(raw):
    """Parse the place lookup JSONL.

    Returns (by_id, malformed). by_id maps address_id to
      {"line_no", "http_status", "geographies" (dict or None), "error" (or None)}
    """
    by_id, malformed = {}, []
    if raw is None:
        return by_id, malformed
    for line_no, line in _split_lines(raw):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict) or not isinstance(obj.get("address_id"), str):
                raise ValueError("no address_id")
        except ValueError:
            malformed.append({"line_no": line_no, "reason": "bad_json_line", "text": line[:200]})
            continue
        aid = obj["address_id"]
        geos, error = None, None
        status = obj.get("http_status")
        body = obj.get("body")
        if status != 200:
            error = "http_status_%s" % (status,)
        else:
            try:
                parsed = json.loads(body) if isinstance(body, str) else body
                geos = parsed["result"]["geographies"]
                if not isinstance(geos, dict):
                    raise ValueError("geographies")
            except (ValueError, KeyError, TypeError):
                geos, error = None, "no_geographies_in_body"
        if aid in by_id:
            by_id[aid] = {"line_no": line_no, "http_status": status, "geographies": None,
                          "error": "duplicate_place_lines"}
        else:
            by_id[aid] = {"line_no": line_no, "http_status": status,
                          "geographies": geos, "error": error}
    return by_id, malformed


def _layer(geos, needle, exact=False):
    """Return the list under the first layer whose name matches, else None."""
    for key, value in geos.items():
        k = str(key).casefold()
        hit = (k == needle) if exact else (needle in k)
        if hit:
            return value if isinstance(value, list) else None
    return None


# --------------------------------------------------------------------------
# record construction
# --------------------------------------------------------------------------

def _base_record(req, meta):
    batch_meta = (meta or {}).get("batch") or {}
    stamp = batch_meta.get("retrieved_at")
    return OrderedDict([
        ("address_id", req["address_id"]),
        ("original_address", req["original_address"]),
        ("normalized_address", req["normalized_address"]),
        ("normalizations", list(req["normalizations"])),
        ("street_address", req["original_address"]),
        ("postal_city", req["postal_city"]),
        ("state", req["state"]),
        ("zip", req["zip"]),
        ("provider", PROVIDER),
        ("provider_benchmark", BENCHMARK),
        ("provider_vintage", VINTAGE),
        ("request_timestamp", stamp),
        ("match_status", "not_attempted"),
        ("matched_address", None),
        ("geocoded_zip", None),
        ("legal_city", None),
        ("county", None),
        ("state_code", None),
        ("lat", None),
        ("lon", None),
        ("match_quality", None),
        ("geography_layers", OrderedDict()),
        ("method", "unresolved"),
        ("year_built", req["year_built"]),
        ("units", req["units"]),
        ("flags", []),
        ("provenance", OrderedDict([
            ("source_file", req["source_file"]),
            ("source_row", req["source_row"]),
            ("provider", PROVIDER),
            ("retrieved_at", stamp),
            ("raw_response_line", None),
        ])),
    ])


def _zip_flags(req, geocoded_zip):
    flags = []
    supplied = req["zip"]
    if supplied:
        inside = zip_in_state_range(req["state"], supplied)
        if inside is False:
            flags.append("zip_outside_state_range:supplied=%s" % supplied)
        if geocoded_zip and geocoded_zip != supplied:
            flags.append("zip_mismatch_supplied_vs_geocoded:supplied=%s,geocoded=%s"
                         % (supplied, geocoded_zip))
    return flags


def _resolve_place(rec, req, entry, state_code, place, meta):
    """Fill legal_city, county, method from the place lookup. Mutates rec."""
    flags = rec["flags"]
    if place is None:
        flags.append("place_layer_not_fetched")
        return
    places_meta = (meta or {}).get("places") or {}
    rec["geography_layers"]["place_lookup"] = OrderedDict([
        ("http_status", place["http_status"]),
        ("retrieved_at", places_meta.get("retrieved_at")),
        ("geographies", place["geographies"]),
    ])
    if place["error"] or place["geographies"] is None:
        flags.append("place_lookup_failed:%s" % (place["error"] or "no_geographies"))
        return
    geos = place["geographies"]
    states = _layer(geos, "states", exact=True)
    counties = _layer(geos, "counties", exact=True)
    if not states and not counties:
        # An empty place layer is only meaningful when the rest of the
        # lookup is real. Without that it is a failed lookup, not "rural".
        flags.append("place_lookup_failed:empty_geographies")
        return

    if counties and isinstance(counties[0], dict) and counties[0].get("NAME"):
        rec["county"] = str(counties[0]["NAME"])
    else:
        rec["county"] = COUNTY_FALLBACK.get((state_code, entry["county_fips"]))
        if rec["county"] is None:
            flags.append("county_name_unavailable")

    places = _layer(geos, "incorporated place") or []
    if len(places) == 0:
        flags.append("no_incorporated_place_probably_unincorporated")
        rec["method"] = "census_batch"
        return
    if len(places) > 1:
        flags.append("multiple_incorporated_places_ambiguous")
        return
    p = places[0]
    raw_name = p.get("NAME") if isinstance(p, dict) else None
    if not isinstance(raw_name, str) or not strip_place_suffix(raw_name):
        flags.append("place_lookup_failed:bad_place_name")
        return
    place_state = FIPS_STATE.get(str(p.get("STATE", "")).strip())
    if place_state != state_code:
        flags.append("place_state_mismatch:batch=%s,place=%s" % (state_code, place_state))
        return
    name = strip_place_suffix(raw_name)
    if name.casefold() in MAILING_NAMES:
        flags.append("legal_city_is_mailing_name_rejected:%s" % name)
        return
    rec["legal_city"] = "%s, %s" % (name, state_code)
    rec["method"] = "census_batch"


def build_record(req, entry, place, meta):
    """Build one output record. `entry` is None when the id was not in the
    response, or a dict from parse_batch_lines, or the string "duplicate"."""
    rec = _base_record(req, meta)
    flags = rec["flags"]
    prov = rec["provenance"]

    if entry is None:
        flags.append("missing_from_response_truncated")
        flags.extend(_zip_flags(req, None))
        return rec
    if entry == "duplicate":
        flags.append("duplicate_response_lines_ambiguous")
        flags.extend(_zip_flags(req, None))
        return rec
    prov["raw_response_line"] = entry["line_no"]
    kind = entry["kind"]
    if kind == "malformed":
        flags.append("malformed_response_line")
        flags.extend(_zip_flags(req, None))
        return rec
    if kind == "no_match":
        rec["match_status"] = "no_match"
        flags.append("no_match")
        flags.extend(_zip_flags(req, None))
        return rec
    if kind == "tie":
        rec["match_status"] = "tie"
        flags.append("tie_ambiguous")
        flags.extend(_zip_flags(req, None))
        return rec

    # A match.
    rec["match_status"] = "match_exact" if entry["match_type"] == "Exact" else "match_non_exact"
    rec["match_quality"] = entry["match_type"]
    rec["matched_address"] = entry["matched_address"]
    parts = [p.strip() for p in entry["matched_address"].split(",")]
    zm = _ZIP5_RE.match(parts[-1]) if parts else None
    rec["geocoded_zip"] = zm.group(1) if zm else None
    rec["geography_layers"]["batch"] = OrderedDict([
        ("coordinates", entry["coordinates"]),
        ("tiger_line_id", entry["tiger_line_id"]),
        ("side", entry["side"]),
        ("state_fips", entry["state_fips"]),
        ("county_fips", entry["county_fips"]),
        ("tract", entry["tract"]),
        ("block", entry["block"]),
    ])
    state_code = FIPS_STATE.get(entry["state_fips"])
    rec["state_code"] = state_code
    if entry["match_type"] == "Non_Exact":
        flags.append("non_exact_match")
    flags.extend(_zip_flags(req, rec["geocoded_zip"]))

    if state_code is None:
        flags.append("geocoded_state_unknown_fips:%s" % entry["state_fips"])
        return rec
    if state_code != req["state"]:
        flags.append("geocoded_state_mismatch:csv=%s,geocoded=%s" % (req["state"], state_code))
        return rec
    box = STATE_BOX.get(req["state"])
    if box is None or not (box[0] <= entry["lat"] <= box[1] and box[2] <= entry["lon"] <= box[3]):
        flags.append("coordinates_outside_state_box")
        return rec

    _resolve_place(rec, req, entry, state_code, place, meta)
    if rec["method"] == "census_batch":
        # Coordinates are only published for rows we trust.
        rec["lat"] = entry["lat"]
        rec["lon"] = entry["lon"]
    return rec


def parse_response(batch_bytes, request_rows, places_bytes=None, meta=None):
    """Raw responses to (records, report). Pure: bytes in, data out.

    records is an OrderedDict address_id -> record, in request order.
    report is a plain dict of counts and every problem line, for printing.
    """
    entries, malformed = parse_batch_lines(batch_bytes)
    places, places_malformed = parse_places(places_bytes)

    by_id = {}
    for e in entries:
        if e["id"] in by_id:
            by_id[e["id"]] = "duplicate"
        else:
            by_id[e["id"]] = e
    known = set(r["address_id"] for r in request_rows)
    unknown_ids = sorted(i for i in by_id if i not in known)

    records = OrderedDict()
    for req in request_rows:
        aid = req["address_id"]
        place = places.get(aid) if places_bytes is not None else None
        records[aid] = build_record(req, by_id.get(aid), place, meta)

    status = Counter(r["match_status"] for r in records.values())
    method = Counter(r["method"] for r in records.values())
    flag_counts = Counter(f.split(":", 1)[0] for r in records.values() for f in r["flags"])
    report = OrderedDict([
        ("rows", len(records)),
        ("response_lines_malformed", malformed),
        ("place_lines_malformed", places_malformed),
        ("unknown_ids_in_response", unknown_ids),
        ("missing_ids", sorted(a for a in records
                                 if by_id.get(a) is None)),
        ("match_status", dict(sorted(status.items()))),
        ("method", dict(sorted(method.items()))),
        ("flags", dict(sorted(flag_counts.items()))),
        ("with_legal_city", sum(1 for r in records.values() if r["legal_city"])),
    ])
    return records, report


def dumps(records):
    """Deterministic JSON text for jurisdictions.json (ASCII, LF, trailing newline)."""
    return json.dumps(records, indent=2, ensure_ascii=True) + "\n"


def write_jurisdictions(records, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(records), encoding="ascii")


def load_meta(path):
    """Read the fetch sidecar. Returns None when absent or unreadable."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def print_report(report, out=None):
    out = out or sys.stdout
    w = lambda s: print(s, file=out)
    w("parse: %d rows, %d with a legal_city" % (report["rows"], report["with_legal_city"]))
    w("  match_status: " + ", ".join("%s=%d" % kv for kv in report["match_status"].items()))
    w("  method: " + ", ".join("%s=%d" % kv for kv in report["method"].items()))
    if report["flags"]:
        w("  flags: " + ", ".join("%s=%d" % kv for kv in report["flags"].items()))
    if report["response_lines_malformed"]:
        w("  MALFORMED response lines: %d (first: line %s, %s)" % (
            len(report["response_lines_malformed"]),
            report["response_lines_malformed"][0]["line_no"],
            report["response_lines_malformed"][0]["reason"]))
    if report["place_lines_malformed"]:
        w("  MALFORMED place lines: %d" % len(report["place_lines_malformed"]))
    if report["missing_ids"]:
        w("  ids with no response line (truncated?): %d" % len(report["missing_ids"]))
    if report["unknown_ids_in_response"]:
        w("  ids in the response that are not in the CSV: %d" % len(report["unknown_ids_in_response"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Parse raw Census responses into jurisdictions.json (no network).")
    ap.add_argument("--response", default=str(DEFAULT_RESPONSE))
    ap.add_argument("--places", default=str(DEFAULT_PLACES))
    ap.add_argument("--meta", default=None, help="fetch sidecar JSON; omit for null timestamps")
    ap.add_argument("--addresses", default=str(normalize.DEFAULT_ADDRESSES))
    ap.add_argument("--out", default=str(DEFAULT_JURISDICTIONS))
    args = ap.parse_args(argv)

    resp = Path(args.response)
    if not resp.exists():
        print("ERROR: response file not found: %s" % resp.name, file=sys.stderr)
        return 2
    places_path = Path(args.places)
    places_bytes = places_path.read_bytes() if places_path.exists() else None
    if places_bytes is None:
        print("note: no place lookup file (%s); legal_city stays null and rows are "
              "flagged place_layer_not_fetched" % places_path.name, file=sys.stderr)
    meta = load_meta(args.meta) if args.meta else None
    rows = normalize.normalize_rows(normalize.load_addresses(args.addresses))
    records, report = parse_response(resp.read_bytes(), rows, places_bytes, meta)
    write_jurisdictions(records, args.out)
    print_report(report)
    print("wrote %s" % Path(args.out).name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
