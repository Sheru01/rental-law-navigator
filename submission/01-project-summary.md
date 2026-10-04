# Item 1: Project Summary (150 to 300 words)

Paste the text between the rules into the form. Word count is checked in
submission/check.sh and noted at the bottom.

---

Which housing rules apply at this address, on this date? Rental housing law is layered: state over city, each layer with its own coverage tests and effective dates. The right answer changes by address and by day, and the public record often lacks the one fact that decides coverage.

Rental Housing Law Navigator answers that question without guessing. A model reads source legal text and produces structured rules: a plain-language requirement plus a machine-readable coverage test. From there, deterministic code, never a language model, decides whether each rule reaches each building. Every claim traces to a quoted span located by exact match inside the source document at render time. If the sentence is not there, the rule does not ship.

The feature to look for is the unknown. When coverage turns on a fact the assessor record does not hold, the system says so at full weight and names the fact. When the legal record itself cannot establish that a rule is operative, that is reported separately as indeterminate: a different kind of silence, with a different fix. Aggregated over the sample, those gaps become the Evidence Debt Ledger, a sourced, per-jurisdiction map of which questions the public record cannot answer and which single missing field blocks the most buildings.

It is live at navigator.mdsrana.com: 500 addresses resolved through a live Census geocoder run (479 to an incorporated place, 21 reported as unresolved rather than guessed), every rule span verified against the corpus, and change tracking done by moving one as-of date. Housing agencies get a fix list, tenant advocates get triage, and anyone deploying AI on law gets a measurement of what the record can actually support.

---

Closing line if the form asks for one sentence: "It tells you what it cannot tell you, and exactly why."
