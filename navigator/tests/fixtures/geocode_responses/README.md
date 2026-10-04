# Geocode response fixtures (SYNTHETIC)

Every file here is hand-authored. Nothing was fetched from the Census Geocoder
(the host is blocked from the build environment). The values are plausible
shapes copied from the documented formats, NOT real geocoder output. Tract,
block, TIGER line ids and place codes are placeholders. Do not treat any value
here as a fact about the real address.

Batch responses (geographies addressbatch, CSV, no header):
  Match     12 fields: id, input address, "Match", "Exact"|"Non_Exact",
            matched address, "lon,lat", TIGER line id, side, state FIPS,
            county FIPS, tract, block
  No_Match  3 fields, Tie  3 fields

  batch_exact.csv                exact match (CRLF line endings on purpose)
  batch_non_exact.csv            non-exact match
  batch_tie.csv                  two ties (one with trailing empty fields)
  batch_no_match.csv             no match
  batch_unincorporated.csv       match whose place layer is empty
  batch_boston_neighborhood.csv  Dorchester mailing name, place is Boston
  batch_san_ysidro.csv           San Ysidro mailing name, place is San Diego
  batch_zip_differs.csv          supplied zip 78746, geocoded zip 07307
  batch_truncated.csv            two good lines, then a line cut off mid field,
                                 and no line at all for every other id
  batch_duplicate.csv            the same id twice with conflicting answers
  batch_malformed.csv            short Match line, garbage, HTML, bad
                                 coordinates, unknown indicator, unterminated
                                 quote, blank line, plus one good line

places/<address_id>.json: the raw body of a geographies/coordinates lookup for
that address. The tests assemble these into the JSONL that fetch.py writes.
