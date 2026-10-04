# Item 7: Dataset (form answer)

The project used organizer-supplied data and generated derived datasets. All
of it is public in the GitHub repository, so paste this into the form:

---

Used: the organizer-distributed corpus of 54 legal source texts plus fetched
supplements (corpus/ in the repo) and the 500-row sample address file
(data/sample_addresses.csv, assessor-derived).

Generated, all in the repo under navigator/out/:

- jurisdictions.json: 500 addresses resolved to their incorporated place by a
  live Census Geocoder run (batch plus per-row coordinates lookup), 479
  resolved, 21 flagged unresolved; validated by navigator/geocode/validate.py.
- rules.json and rules_withheld.json: structured rule records with verified
  quoted spans and per-field epistemics.
- lookups.json: engine results for all 500 addresses (798 entries).
- changes.json: as-of change tracking results for the five test scenarios.

Repository: <https://github.com/Sheru01/rental-law-navigator>

---

If the form insists on a dedicated dataset link rather than a repo link, the
folders navigator/out/ and corpus/ in that repository are the dataset.
