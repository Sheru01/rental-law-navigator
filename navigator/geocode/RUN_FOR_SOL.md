# Run the Census geocoding from your own terminal

The build environment cannot reach `geocoding.geo.census.gov` (egress allowlist, 403 from
the proxy). Everything here was built and tested offline against hand-made fixtures. No live
geocoding has happened yet. You run the live part from a normal terminal.

Run every command from the project root. Standard library only (tested on Python 3.10).

## What the Census gives us, and the one thing to know

The batch endpoint returns, per address: match status, matched address, coordinates, TIGER
line id, and state, county, tract and block codes. It does NOT return the incorporated place,
and the incorporated place is the whole point (legal_city). So there are two live calls:

1. The address batch (all 500 rows). Raw response is saved verbatim.
2. A place lookup per matched row, from the batch coordinates, through the Census
   `geographies/coordinates` endpoint. One JSON line per lookup, raw body saved verbatim.

Both raw files are never discarded and never overwritten by the scripts.

## Step 0: build the request file (no network)

    python3 navigator/geocode/normalize.py

Expected: `normalize: 500 rows, ... with at least one normalization` and a line ending
`(differs_written_beside)`. That is correct: `navigator/out/census_batch_input.csv` belongs to
the validator and holds the raw CSV streets, so the normalized file is written beside it as
`navigator/out/census_batch_input.pipeline.csv`. That pipeline file is the one to send. It is five
columns, no header, 500 rows. Zips are never padded or changed, and 130 blank zips stay blank.
Each row's original street is kept verbatim in the output alongside what was sent.

## Option A (recommended): one command, with retries and a timestamp

    python3 navigator/geocode/run.py

This normalizes, runs both live calls (100 rows per batch request, retry with backoff on
truncated responses, 0.2 s between place lookups), then parses to
`navigator/out/jurisdictions.json`. It also writes `navigator/out/census_fetch_meta.json`, which
carries the time of the live call so each record gets a real `request_timestamp`. If the host is
blocked it prints a message naming the host, exits non-zero, and writes nothing. If raw output
files already exist it stops rather than replace them (move them aside, or add `--overwrite`).

Then skip to "Validate".

## Option B: the curl commands, step by step

### B1. The address batch

    curl --fail --show-error --silent \
      --form addressFile=@navigator/out/census_batch_input.pipeline.csv \
      --form benchmark=Public_AR_Current \
      --form vintage=Current_Current \
      --output navigator/out/census_batch_output.csv.part \
      https://geocoding.geo.census.gov/geocoder/geographies/addressbatch

Endpoint: geographies addressbatch. Form fields: `addressFile` (the file), `benchmark`
`Public_AR_Current`, `vintage` `Current_Current`. All 500 rows in one request is within the
Census limit of 10,000. Save the raw response as `navigator/out/census_batch_output.csv`, but only
after the truncation check below passes:

    mv navigator/out/census_batch_output.csv.part navigator/out/census_batch_output.csv

Record when the live call was made, right after it finishes (optional but wanted):

    printf '{"batch": {"retrieved_at": "%s"}}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > navigator/out/census_fetch_meta.json

### How to spot a truncated or bad batch response

Run these on the `.part` file before the `mv`:

    wc -l < navigator/out/census_batch_output.csv.part
    awk -F'","' '{print NF}' navigator/out/census_batch_output.csv.part | sort | uniq -c
    tail -n 1 navigator/out/census_batch_output.csv.part

* Line count must be exactly 500. Fewer lines means truncated. The Census returns a line for
  every id, including No_Match and Tie rows, so a short file is never "just unmatched rows".
* The field-count tally must show only `3` (No_Match and Tie lines) and `12` (Match lines). Any
  other number means a cut-off or malformed line. If EVERY match line shows some other number,
  the Census changed its output format: stop and tell the lead.
* The last line must end with a closing double quote and have 3 or 12 fields. A last line that
  stops mid field is the classic truncation.
* An HTML page or error text instead of CSV is a failure, not data. Do not use it.

If any check fails, rerun B1 (or use Option A, which retries by itself). Never hand-edit the file.

### B2. The place lookups

    python3 navigator/geocode/fetch.py places

Reads `navigator/out/census_batch_output.csv`, makes one GET per matched row to
`https://geocoding.geo.census.gov/geocoder/geographies/coordinates` with `x`, `y`,
`benchmark=Public_AR_Current`, `vintage=Current_Current`, `layers=all`, `format=json`, and saves
`navigator/out/census_places_output.jsonl`. All or nothing: if any lookup fails after retries,
nothing is written. Allow a few minutes (about 500 requests).

A single lookup by hand, to eyeball the shape (downtown Berkeley):

    curl -s 'https://geocoding.geo.census.gov/geocoder/geographies/coordinates?x=-122.2730&y=37.8715&benchmark=Public_AR_Current&vintage=Current_Current&layers=all&format=json'

Look for `Incorporated Places` with a `NAME` ending in " city" under `result.geographies`. If the
key is named differently, or absent for a city address, stop and tell the lead.

### B3. Parse

    python3 navigator/geocode/run.py --skip-fetch

Parses the existing raw files (no network), applies the timestamp from the meta file if present,
and writes `navigator/out/jurisdictions.json`. It prints counts by match_status, method and
flag, and lists any malformed lines or ids with no response line. Zero malformed and zero
missing means the response was complete.

## Validate

    python3 navigator/geocode/validate.py

Reads `navigator/out/jurisdictions.json` and `data/sample_addresses.csv`, rewrites
`navigator/out/geocode_validation.md`, and exits 0 only when there are no hard failures. The
report also shows the counts per legal_city against the README table, every address whose legal
city differs from its postal city, every unresolved address with its reason, and the 27 NJ rows
whose supplied zip is outside the NJ range.

## What to send back

* The exit code of `validate.py`, and the `geocode validation:` line.
* The `parse:` summary printed in B3 (or Option A).
* `navigator/out/geocode_validation.md`.
* Do not edit `jurisdictions.json` or the raw files. Unmatched, tie and unincorporated rows are
  meant to stay explicit. They carry flags that say why, and legal_city is null for them.
