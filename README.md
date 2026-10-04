# Rental Housing Law Navigator

Challenge 2, RealPage x Hack-Nation.

**Live: [navigator.mdsrana.com](https://navigator.mdsrana.com)**

"Which housing rules apply at this address, on this date?" A model extracts structured rules from source legal text. Deterministic code, never the model, decides whether a rule covers a given building. An uncertain result is rendered at full weight, naming the specific missing fact that would resolve it.

The system distinguishes two kinds of silence, and that distinction is the point:

- **unknown**: the rule is operative, but a *building* fact the public record does not hold decides coverage.
- **indeterminate**: the *legal* record itself does not establish that the rule is operative, so it is never reported as applying.

## What is real in the current build

| Layer | Source | State |
|---|---|---|
| Jurisdictions | Live Census geocoder run over the 500 sample addresses | 479 resolved to an incorporated place, 21 left explicitly unresolved. `navigator/geocode/validate.py` passes with 0 warnings |
| Rules | Extraction pipeline over replayed echo fixtures | Real corpus, real span verification, but **not** a live model run. The page says so in its header |
| Lookups | `navigator/engine` over the above | Exactly 500 address ids, 798 entries |
| Change tracking | `navigator/changes` | T4 confirmed, T3 failed, T1, T2 and T5 not established on this rule subset |

Every one of the 7 quoted spans exact-matches its corpus document after whitespace normalisation. No verdict, title, citation or span is authored in the page itself.

A live extraction run needs an API key and costs money, so it has not been done. Until it is, `extract_report.json` reports `is_real_extraction: false` and the site header says "rules from replayed extraction fixtures".

## Layout

- `navigator/` the pipeline: `extract`, `geocode`, `normalize`, `engine`, `changes`, and its tests.
- `corpus/`, `data/`, `schema/` the organizer-supplied corpus, sample addresses and rule record schema.
- `navigator/out/` generated output, including the submission artifacts `lookups.json` and `changes.json`.
- `site/` the demo page: `build.py` generates `dist/index.html` from engine output, `smoke.mjs` checks it.
- `docs/` the write-up, the demo scripts, and the run sheet with the data-binding map.
- `design/` the frozen eight-board design canvas the page is built to.

## Running it

```sh
python3 -m pytest navigator/tests          # 273 passed, 2 skipped
python3 navigator/geocode/run.py           # live Census calls, writes jurisdictions.json
python3 navigator/geocode/validate.py      # gate: exits non-zero on a hard failure
python3 -m navigator.extract --transport echo
python3 -m navigator.engine.run --as-of 2026-10-01 --rules navigator/out/echo/rules.json
python3 site/build.py --rules navigator/out/echo
cd site && npm install && node smoke.mjs   # 11 browser checks, exits non-zero on failure
```

`site/smoke.mjs` takes `SITE_URL` to check the deployed copy instead of the local build, and `CHROMIUM_PATH` if Playwright cannot find a browser.

Pushes to `main` redeploy the site automatically.

## The one line

> "Every other system here will tell you what the law says. This one also tells you what it cannot tell you, and exactly why."

Not legal advice. A prototype over a fixed corpus of public law.
