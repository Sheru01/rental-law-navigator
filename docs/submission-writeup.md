# Rental Housing Law Navigator — Project Write-up

Challenge 2, RealPage x Hack-Nation. Submission documentation only: this write-up describes the design intent and architecture already established on the frozen design canvas and the demo run sheet; it introduces no new UI, binds no data, and makes no claim about current build status beyond what those documents already state.

## The question

Which housing rules apply at this address, on this date? Both halves matter: the answer changes by city and by day, because housing law is layered (state, then city), each layer with its own coverage tests and effective dates. In this challenge's scoring, a missed applicable rule costs double and an honest unknown earns partial credit — so the incentive and the ethics point the same way.

## The design problem, stated plainly

Most systems will hide their uncertainty, because an "I don't know" looks like a failure. The scoring here rewards the opposite, and so does the underlying legal reality: a jurisdiction's public record frequently does not contain the one fact (a building's unit count, its year built) needed to decide whether a rule covers a given property. The interface has to make an unknown read as the system working, not the system breaking.

Three rules follow from that, and they govern the whole design:

- **Equal finish.** An unknown result gets the same border, padding, and provenance chain as a confident one — never a thinner, greyer, or apologetic treatment.
- **Name the gap.** Every uncertain state names the specific missing fact and what would resolve it. Vague uncertainty reads as failure; specific uncertainty reads as rigor.
- **No alarm colors.** Uncertainty is rendered in typographic ochre, never red. Red means the system failed; ochre means the record is thin.

## Architecture: extract, then decide

A model reads the source legal text and produces structured rules — plain-language requirement, plus a machine-readable coverage test. From that point on, a language model never decides whether a given rule applies to a given building: deterministic code evaluates the structured test against the address and building facts. The judge-facing rule card shows both halves side by side, so it's visible that a model extracted and code decided.

Every claim on screen traces back to an exact span of source text, located by exact substring match (whitespace-normalized) at render time against the shipped source document. If the quoted span isn't found verbatim in the document, the rule does not ship.

## The flow, in eight beats

One address, one as-of date, nothing else to configure:

1. **Ask** — address and date; time is a first-class input from the first frame.
2. **Resolve jurisdiction** — mailing city and legal city shown as two separate lines; the legal city comes from geocoding to the incorporated place, because the post office does not set jurisdiction. When the two differ, that's the headline.
3. **Retrieve controlling evidence** — the jurisdiction stack as layers (state, then city), each naming how many rules and source documents backed it.
4. **Show the extracted rule** — one card, plain language over the coverage test.
5. **Stated versus derived** — every field carries a provenance tag; a date the statute states and a date computed from a rule of construction never look alike, and the derived one names the rule it came from.
6. **Applicability, including unknown** — an unknown rendered at full weight, naming the one missing fact that would resolve it.
7. **What changed** — moving the as-of date re-renders the same answer as a diff against the earlier date; one control, not a second mode or screen.
8. **Exact source** — any claim click lands on the verbatim span in the source document, with its retrieval date.

A persistent footer ("not legal advice," with the as-of date) stays on screen throughout. It is never a modal to dismiss.

## Evidence Debt: the idea meant to outlast the hackathon

A system that refuses to guess produces an unintended by-product: a precise, per-jurisdiction ledger of which housing-law questions *cannot* be answered from the public record, and exactly which missing field is the reason.

Every other team's unknowns are a scoring loss to minimize. Here, they're structured output — each one already carries the rule, the jurisdiction, the specific absent fact, and the source document proving the rule is real. Aggregated across the evaluated address set, that isn't a list of failures; it's a civic-data audit that required reading the law first to produce.

**The reframe, in one line:** an unanswerable question is a finding about the record, not a failure of the reader.

Who that's useful to, and what it buys them:

| Audience | What they get |
|---|---|
| Housing agencies | Which single field, published once, would make thousands of buildings automatically adjudicable — a budget line, not a research project |
| Tenant advocates | Which cases turn on a fact no public record holds, before intake, so scarce legal hours go where the record can support a claim |
| Housing providers | Where obligations are genuinely ambiguous versus merely unread — the difference between a compliance question and a legal one |
| Anyone deploying AI on law | A measurement of which answers the record cannot support, which has to come before you can trust a model's legal answer at all |

This isn't a roadmap slide: the ledger is a grouping query over output the engine already produces for the main flow, with no additional backend logic requested or assumed.

## What we do not claim

- Not legal advice, and not a confidence claim about any individual result.
- No jurisdiction coverage outside the assembled corpus.
- No claim that the source-document manifest hashes verify text integrity — their scope is unknown, and the demo states a locally-recorded hash instead.
- No claim that the data is *wrong* where a field is missing — only that the published record does not contain it. Those are different accusations, and only the second is supportable.
- No generalization past the evaluated sample, and no implication that a jurisdiction's incomplete schema reflects negligence — schemas differ for ordinary historical reasons.

## The one line

> "Every other system here will tell you what the law says. This one also tells you what it cannot tell you, and exactly why."

---

*Companion documents: "Demo run sheet and data-binding map" (rehearsal protocol, screenshot framing, keyboard/focus spec, fallback tiers, and the full field-by-field binding map against README section 5 and the rule record schema) and "Rental Housing Law Navigator — Demo Script" (the spoken 60-second, 90-second, and 3-minute scripts). This write-up and its companions describe design intent; they do not bind to or assert the state of any backend output, which remains gated on the three steps recorded in the run sheet: an immutable backend commit, a read-only audit with no unresolved P0/P1, and validated frozen output files.*
