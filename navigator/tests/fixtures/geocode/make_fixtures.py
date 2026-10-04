#!/usr/bin/env python3
"""Author the geocode validator fixtures (deterministic, offline).

    python3 navigator/tests/fixtures/geocode/make_fixtures.py

SYNTHETIC DATA. The clean file maps each CSV row to a legal city by a fixed
rule (Boston neighborhoods to Boston, San Ysidro to San Diego, everything else
to its postal city) and places it near a city centre with a deterministic
offset. It exists only to exercise navigator/geocode/validate.py. It is NOT
geocoder output and must never be used as jurisdiction data.

Files written next to this script:
  clean.json            passes every check
  broken_<class>_<what>.json   one deliberate defect each, everything else clean
"""
import csv
import hashlib
import json
import sys
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
CSV_PATH = ROOT / "data" / "sample_addresses.csv"

BOSTON_NAMES = {"Boston", "Dorchester", "Roxbury", "Allston", "Brighton", "East Boston",
                "South Boston", "Hyde Park", "Jamaica Plain", "Mattapan"}
# legal_city -> (lat, lon, county)
CENTERS = {
    "Los Angeles, CA": (34.05, -118.25, "Los Angeles County"),
    "San Francisco, CA": (37.77, -122.42, "San Francisco County"),
    "San Diego, CA": (32.72, -117.16, "San Diego County"),
    "Berkeley, CA": (37.87, -122.27, "Alameda County"),
    "Jersey City, NJ": (40.72, -74.06, "Hudson County"),
    "Hoboken, NJ": (40.745, -74.03, "Hudson County"),
    "Newark, NJ": (40.735, -74.17, "Essex County"),
    "Boston, MA": (42.33, -71.06, "Suffolk County"),
    "Cambridge, MA": (42.375, -71.11, "Middlesex County"),
}
MAILING_NAMES = ["Dorchester", "Roxbury", "Allston", "Brighton", "East Boston",
                 "South Boston", "Hyde Park", "Jamaica Plain", "Mattapan", "San Ysidro"]


def load_rows():
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def legal_for(row):
    if row["postal_city"] in BOSTON_NAMES:
        return "Boston, MA"
    if row["postal_city"] == "San Ysidro":
        return "San Diego, CA"
    return "%s, %s" % (row["postal_city"], row["state"])


def jitter(aid, salt):
    h = hashlib.md5((aid + salt).encode("ascii")).digest()
    return round((h[0] / 255.0 - 0.5) * 0.008, 6)


def fact(value):
    value = value.strip()
    return int(value) if value else None


def clean_records(rows):
    recs = OrderedDict()
    nj_bad_seen = False
    for i, row in enumerate(rows):
        aid = row["address_id"]
        legal = legal_for(row)
        lat0, lon0, county = CENTERS[legal]
        rec = OrderedDict([
            ("street_address", row["street_address"]),
            ("postal_city", row["postal_city"]),
            ("state", row["state"]),
            ("zip", row["zip"]),
            ("legal_city", legal),
            ("county", county),
            ("state_code", row["state"]),
            ("method", "census_oneline" if i % 50 == 7 else "census_batch"),
            ("match_quality", "Exact"),
            ("lat", round(lat0 + jitter(aid, "lat"), 6)),
            ("lon", round(lon0 + jitter(aid, "lon"), 6)),
            ("year_built", fact(row["year_built"])),
            ("units", fact(row["units"])),
            ("flags", []),
        ])
        if row["state"] == "NJ" and row["zip"] and not row["zip"].startswith(("07", "08")):
            if nj_bad_seen:
                rec["flags"].append("zip_mismatch_supplied_zip_unreliable")
            nj_bad_seen = True  # the first one is left unflagged on purpose
        if i in (10, 20, 30):  # legitimate unresolved rows: everything null
            rec.update(legal_city=None, county=None, state_code=None, method="unresolved",
                       match_quality="No_Match", lat=None, lon=None)
            rec["flags"].append("unresolved_no_census_match")
        if i == 40:  # resolved but outside any incorporated place
            rec["legal_city"] = None
            rec["flags"].append("unincorporated_no_place_layer")
        recs[aid] = rec
    return recs


def clone(recs):
    return json.loads(json.dumps(recs), object_pairs_hook=OrderedDict)


def dumps(recs):
    lines = ["%s: %s" % (json.dumps(k), json.dumps(v)) for k, v in recs.items()]
    return "{\n" + ",\n".join(lines) + "\n}\n"


def first(rows, pred):
    for r in rows:
        if pred(r):
            return r["address_id"]
    raise KeyError("no row matches")


def build_all(rows):
    """Return OrderedDict filename -> text."""
    base = clean_records(rows)
    out = OrderedDict()
    out["clean.json"] = dumps(base)

    def variant(name, mutate):
        recs = clone(base)
        mutate(recs)
        out[name] = dumps(recs)

    ids = [r["address_id"] for r in rows]
    # ---- C1 id set
    variant("broken_c1_count_499.json", lambda d: d.pop(ids[-1]))
    variant("broken_c1_extra_id_501.json", lambda d: d.__setitem__("A0501", clone(d)[ids[0]]))

    def foreign(d):
        items = list(d.items())
        d.clear()
        for k, v in items:
            d["A9999" if k == ids[99] else k] = v
    variant("broken_c1_foreign_id.json", foreign)
    text = dumps(base)
    dup_line = "%s: %s" % (json.dumps(ids[0]), json.dumps(base[ids[0]]))
    out["broken_c1_duplicate_key.json"] = text.rstrip("\n")[:-1].rstrip("\n") + ",\n" + dup_line + "\n}\n"
    out["broken_c1_malformed_json.json"] = text[: len(text) // 2]
    out["broken_c1_top_level_list.json"] = json.dumps(list(base.values())[:3]) + "\n"

    # ---- C2 required keys
    def missing_keys(d):
        del d[ids[0]]["county"]
        del d[ids[1]]["flags"]
        del d[ids[2]]["year_built"]
        del d[ids[3]]["lat"]
    variant("broken_c2_missing_keys.json", missing_keys)

    # ---- C3 postal-city fallback
    def unresolved_with_city(d):
        r = d[ids[0]]  # Los Angeles row
        r.update(method="unresolved", match_quality="No_Match", lat=None, lon=None,
                 county=None, state_code=None)
        r["flags"] = ["unresolved_no_census_match"]
        r["legal_city"] = "%s, %s" % (r["postal_city"], r["state"])  # silent copy of postal_city
    variant("broken_c3_unresolved_with_legal_city.json", unresolved_with_city)

    def mailing(d):
        for name in MAILING_NAMES:
            aid = first(rows, lambda r, n=name: r["postal_city"] == n)
            st = d[aid]["state"]
            d[aid]["legal_city"] = "%s, %s" % (name, st)
    variant("broken_c3_mailing_name_as_legal_city.json", mailing)

    # ---- C4 form
    def suffix(d):
        d[ids[0]]["legal_city"] = "Los Angeles city, CA"
        aid = first(rows, lambda r: r["postal_city"] == "Hoboken")
        d[aid]["legal_city"] = "Hoboken city, NJ"
        aid = first(rows, lambda r: r["postal_city"] == "Jersey City")
        d[aid]["legal_city"] = "Jersey City city, NJ"
    variant("broken_c4_census_suffix.json", suffix)

    def bad_form(d):
        berk = [r["address_id"] for r in rows if r["postal_city"] == "Berkeley"]
        d[berk[0]]["legal_city"] = "Berkeley"
        d[berk[1]]["legal_city"] = "Berkeley, California"
        d[berk[2]]["legal_city"] = "BERKELEY CA"
        d[berk[3]]["legal_city"] = "Berkeley, NJ"  # state suffix disagrees with state_code
        d[berk[4]]["legal_city"] = 42
    variant("broken_c4_bad_form.json", bad_form)

    # ---- C5 facts
    ybid = first(rows, lambda r: r["year_built"].strip())
    variant("broken_c5_year_changed.json", lambda d: d[ybid].__setitem__("year_built", d[ybid]["year_built"] + 1))
    ublank = first(rows, lambda r: not r["units"].strip())
    variant("broken_c5_units_invented.json", lambda d: d[ublank].__setitem__("units", 12))
    yblank = first(rows, lambda r: not r["year_built"].strip())
    variant("broken_c5_year_invented.json", lambda d: d[yblank].__setitem__("year_built", 1950))
    variant("broken_c5_year_dropped.json", lambda d: d[ybid].__setitem__("year_built", None))
    variant("broken_c5_year_float.json", lambda d: d[ybid].__setitem__("year_built", float(d[ybid]["year_built"])))

    # ---- C6 coordinates
    njid = first(rows, lambda r: r["state"] == "NJ" and r["postal_city"] == "Newark")
    caid = first(rows, lambda r: r["state"] == "CA")

    def out_of_range(d):
        d[njid]["lat"], d[njid]["lon"] = 34.05, -118.25  # Newark NJ row placed in Los Angeles
        d[caid]["lat"], d[caid]["lon"] = 0.0, 0.0
    variant("broken_c6_out_of_range.json", out_of_range)

    def swapped(d):
        r = d[caid]
        r["lat"], r["lon"] = r["lon"], r["lat"]
    variant("broken_c6_lat_lon_swapped.json", swapped)
    variant("broken_c6_resolved_null_coords.json", lambda d: d[caid].__setitem__("lat", None))

    # ---- C7 integrity
    zid = first(rows, lambda r: r["zip"] == "07030")
    variant("broken_c7_zip_float.json", lambda d: d[zid].__setitem__("zip", 7030.0))
    variant("broken_c7_zip_int_lost_zero.json", lambda d: d[zid].__setitem__("zip", 7030))
    variant("broken_c7_zip_short_string.json", lambda d: d[zid].__setitem__("zip", "7030"))
    variant("broken_c7_bad_method.json", lambda d: d[ids[0]].__setitem__("method", "census_geocoded"))

    def passthrough(d):
        d[ids[0]]["postal_city"] = "Elsewhere"
        d[ids[1]]["street_address"] = "1 MADE UP RD"
    variant("broken_c7_passthrough_changed.json", passthrough)
    variant("broken_c7_flags_not_list.json", lambda d: d[ids[0]].__setitem__("flags", "none"))
    return out


def write_all(outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    files = build_all(load_rows())
    for name, text in files.items():
        (outdir / name).write_text(text, encoding="ascii", newline="\n")
    return list(files)


if __name__ == "__main__":
    names = write_all(HERE)
    print("wrote %d fixture files" % len(names))
    for n in names:
        print("  " + n)
