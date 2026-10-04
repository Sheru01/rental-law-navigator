#!/usr/bin/env python3
"""Orchestrates the geocoding pipeline: normalize, fetch, parse.

    python3 navigator/geocode/run.py                 normalize, fetch, parse (needs the network)
    python3 navigator/geocode/run.py --skip-fetch    normalize, then parse the existing raw files
                                                     in navigator/out/ (no network)
    python3 navigator/geocode/run.py --offline --response FILE --out-dir DIR [--places FILE]
                                                     normalize and parse a saved response into DIR.
                                                     Never imports the network module. Both
                                                     --response and --out-dir are REQUIRED so a
                                                     fixture can never be written over real output.

After a run, validate with:

    python3 navigator/geocode/validate.py --jurisdictions <out-dir>/jurisdictions.json

Plain ASCII output. Standard library only.
"""
import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import normalize  # noqa: E402
import parse  # noqa: E402

ROOT = _HERE.parents[1]
DEFAULT_OUT_DIR = ROOT / "navigator" / "out"


def _shown(path):
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return Path(path).name


def main(argv=None):
    ap = argparse.ArgumentParser(description="Geocoding pipeline: normalize, fetch, parse.")
    ap.add_argument("--addresses", default=str(normalize.DEFAULT_ADDRESSES))
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--skip-fetch", action="store_true", help="parse the existing raw files, no network")
    ap.add_argument("--offline", action="store_true",
                    help="no network, parse --response (and --places) into --out-dir")
    ap.add_argument("--response", default=None, help="raw batch response (required with --offline)")
    ap.add_argument("--places", default=None, help="raw place lookup JSONL (optional)")
    ap.add_argument("--meta", default=None, help="fetch sidecar (offline: only if given)")
    ap.add_argument("--overwrite", action="store_true", help="allow fetch to replace existing raw files")
    args = ap.parse_args(argv)

    if args.offline and (not args.response or not args.out_dir):
        print("ERROR: --offline requires both --response and --out-dir "
              "(so a fixture cannot overwrite real output)", file=sys.stderr)
        return 2
    if args.offline and args.skip_fetch:
        print("ERROR: choose --offline or --skip-fetch, not both", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # Stage 1: normalize (no network).
    rows = normalize.normalize_rows(normalize.load_addresses(args.addresses))
    batch_path, status = normalize.write_batch_input(rows, out_dir)
    changed, _counts = normalize.summarize(rows)
    print("normalize: %d rows, %d normalized, batch input %s (%s)"
          % (len(rows), changed, _shown(batch_path), status))

    response = Path(args.response) if args.response else out_dir / "census_batch_output.csv"
    places = Path(args.places) if args.places else out_dir / "census_places_output.jsonl"
    meta_path = Path(args.meta) if args.meta else (
        None if args.offline else out_dir / "census_fetch_meta.json")

    # Stage 2: fetch (network). Imported only here, never on the offline path.
    if not (args.skip_fetch or args.offline):
        import fetch  # noqa: WPS433
        try:
            fetch.fetch_batch(batch_path, response, overwrite=args.overwrite)
            print("fetch: batch response saved to %s" % _shown(response))
            fetch.fetch_places(response, places, overwrite=args.overwrite)
            print("fetch: place lookups saved to %s" % _shown(places))
        except fetch.FetchError as exc:
            print("ERROR: %s" % exc, file=sys.stderr)
            return exc.exit_code

    # Stage 3: parse (no network).
    if not response.exists():
        print("ERROR: raw response not found: %s. Run the fetch step first." % _shown(response),
              file=sys.stderr)
        return 2
    places_bytes = places.read_bytes() if places.exists() else None
    if places_bytes is None:
        print("note: no place lookup file at %s; legal_city stays null and matched rows are "
              "flagged place_layer_not_fetched" % _shown(places))
    meta = parse.load_meta(meta_path) if meta_path else None
    records, report = parse.parse_response(response.read_bytes(), rows, places_bytes, meta)
    out_json = out_dir / "jurisdictions.json"
    parse.write_jurisdictions(records, out_json)
    parse.print_report(report)
    print("wrote %s" % _shown(out_json))
    print("validate with: python3 navigator/geocode/validate.py --jurisdictions %s" % _shown(out_json))
    return 0


if __name__ == "__main__":
    sys.exit(main())
