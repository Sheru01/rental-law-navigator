# Rental Housing Law Navigator

Challenge 2, RealPage x Hack-Nation. "Which housing rules apply at this address, on this date?" A model extracts structured rules from source legal text; deterministic code, never the model, decides whether a rule covers a given building. An unknown result is rendered at full weight, naming the specific missing fact that would resolve it.

**Status:** design and documentation only in this repository right now. The UI does not bind to any backend output yet — see `docs/demo-run-sheet-and-binding-map.md` for the three gates (immutable backend commit, read-only audit, validated frozen outputs) that have to clear before any value on screen is real.

## Contents

- `docs/submission-writeup.md` — the project write-up: the problem, the design rules, the extract-then-decide architecture, the eight-beat flow, and the Evidence Debt idea.
- `docs/demo-script.md` — the spoken 60-second, 90-second, and 3-minute demo scripts, anticipated Q&A, and fallback tiers.
- `docs/demo-run-sheet-and-binding-map.md` — the production-readiness companion to the design canvas: rehearsal protocol, screenshot framing, keyboard/focus spec, offline/fallback states, the full field-by-field data-binding map, and the post-gate reconciliation checklist.
- `design/` — the frozen eight-board design canvas ("Rental Law Navigator: Judge Demo Design"), as standalone HTML boards plus `canvas.json` for the canvas layout. Conceptual scope is frozen here; nothing in `design/` binds to live data.

## The one line

> "Every other system here will tell you what the law says. This one also tells you what it cannot tell you, and exactly why."
