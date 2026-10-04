# Rental Housing Law Navigator — Demo Script

Production copy for the judge demo. Pulled from the frozen design canvas ("Rental Law Navigator: Judge Demo Design") boards Main and Storyboard. This is a documentation artifact only: no UI, data binding, or design change is made by writing this file.

Solo presenter, one laptop. Venue wifi is assumed dead. The judge has seen several demos already.

Every spoken factual claim is evidence-conditioned: say "in force," "enacted," or "the record does not contain [fact]" only when the validated output on screen actually supports it on the day of the demo. If the backend has not cleared the three gates (Codex commit, Sonnet audit, Sol's validated outputs) by showtime, these scripts are rehearsed against whatever the tier-2 build can honestly show, not recited from memory.

---

## 1. The one line to say out loud

> "Every other system here will tell you what the law says. This one also tells you what it cannot tell you, and exactly why."

## 2. The design problem, in one breath (context for the presenter, not spoken verbatim)

Most teams will hide their uncertainty. The scoring rewards the opposite, and so does the law: a missed applicable rule costs double, an honest unknown earns partial credit. So the interface has to make an unknown feel like the system working, not the system breaking.

Three rules follow, and they're why the unknown card gets equal visual weight:
- **R1 — Equal finish.** An unknown card has the same border, padding, and provenance chain as an "applies" card. Never thinner, greyer, or apologetic.
- **R2 — Name the gap.** Every uncertain state names the specific missing fact and what would resolve it.
- **R3 — No alarm colors.** Uncertainty is ochre and typographic, never red. Red means the system failed; ochre means the record is thin.

---

## 3. Sixty-second script (booth) — 142 words, clicks 1–5

Target: 0:58–1:02. Timed against a stopwatch, not against the 90-second flow below — they are different clocks.

| Time | Click | Line |
|---|---|---|
| 0:00 | 1 — paste address | "Which housing rules apply at this address, on this date? Both halves matter. The answer changes by city and by day." |
| 0:08 | 2 — look up | "Mailing city, one line. Legal city, the next. We geocode to the incorporated place, because the post office does not set jurisdiction." |
| 0:22 | — | "A model read the law and produced structured rules. Code, not the model, decides whether a rule covers a building." |
| 0:30 | 3 — hover unknown card | **"This one says unknown. That is the feature.** The rule is in force. Coverage turns on a fact the public record does not hold for this building. So we name the missing fact instead of guessing." |
| 0:44 | 4 — see exact source | "Every claim traces to the exact text, machine-checked against the source. If the quote is not there, the rule never ships." |
| 0:54 | 5 — second as-of date | "Move the date. Same engine, different moment. That is change tracking: a parameter, not a second product." |

**If running long, cut in this order:** the 0:22 architecture line first, then the second sentence of 0:10 ("because the post office..."), then any remaining explanatory prose. **Never cut 0:30** (the unknown moment) or **0:44** (exact-source evidence) — those two beats are the demo.

---

## 4. Ninety-second judge flow — eight beats, clicks 1–5 plus two narrated beats

Target: 1:28–1:32. This is the fuller walkthrough for a judge standing at the screen rather than a booth crowd.

| # | Time | Beat | What it shows the judge |
|---|---|---|---|
| 1 | 0:00–0:08 | **Ask** | A street address and an as-of date, nothing else. Time is a first-class input from the first frame. |
| 2 | 0:08–0:18 | **Resolve jurisdiction** | Mailing city and legal city as two separate lines, the second from the geocoder. When they differ, that's the headline. |
| 3 | 0:18–0:28 | **Retrieve controlling evidence** | The jurisdiction stack as layers — state, then city — each naming how many rules and documents backed it. |
| 4 | 0:28–0:40 | **Show the extracted rule** | One rule card: plain-language requirement on top, machine-readable coverage test underneath. A model read it; code decided it. |
| 5 | 0:40–0:52 | **Stated versus derived** *(narrated, no click)* | Every field carries a provenance tag. A stated date and a derived date never look alike, and the derived one names the rule it came from. |
| 6 | 0:52–1:05 | **Applicability, including unknown** | An unknown rendered at full weight, naming the one missing fact that would resolve it. This beat wins or loses the room. |
| 7 | 1:05–1:18 | **What changed** | Drag the as-of date; the same answer re-renders as a diff against the earlier date. One control, no second mode. |
| 8 | 1:18–1:30 | **Exact source** | Click any claim, land on the verbatim span in the source document with its retrieval date. The last thing the judge sees is the law itself. |

**Persistent footer, every screen, all 90 seconds:** "Not legal advice," with the as-of date beside it. Never a modal to dismiss.

**Do not say:** that this is legal advice; that the system is confident; that jurisdiction coverage extends beyond the corpus; that the manifest hashes verify the text (their scope is unknown — say so if asked).

---

## 5. Three-minute panel script — spoken lines, all six clicks

| Time | Beat | Line |
|---|---|---|
| 0:00 | Problem | "Housing law is layered: state, then city, each with its own coverage tests and dates. The right answer depends on the exact address and the exact day. In this scoring a missed rule costs double and an honest unknown earns partial credit, so the incentive and the ethics point the same way." |
| 0:25 | Architecture (clicks 1–2) | "The model extracts. Deterministic code decides. Here is the rule card: plain language on top, the machine-readable coverage test underneath. Nothing a judge sees was decided by a language model." |
| 0:55 | The unknown (clicks 3–4) | "This rule is in force. It turns on a fact the assessor record does not contain for this building. We say which fact, and what would resolve it. Click through and the exact sentence of law is highlighted where it sits in the document. The match is verified at render time." |
| 1:40 | Change tracking (click 5) | "One parameter moves. Three shapes come out of it: a law whose date arrives, a bill that is not yet law and can never resolve to applies, and a measure that failed and must never appear in any lookup." |
| 2:10 | What we could not do (click 6) | "Two sources we could not obtain. The system reports those rules as indeterminate rather than filling the hole. Two independent readers, one Claude and one GPT, extracted the same documents blind. We published their disagreements instead of reconciling them quietly. None was a fabrication." |
| 2:40 | Evidence Debt | "Every unknown you saw is a row in a ledger over these [N] addresses. Grouped by city, it shows which single missing field blocks the most buildings. We set out to answer questions about housing law. We also measured where the public record cannot answer them." |
| 2:55 | Close | "It tells you what it cannot tell you, and exactly why." *Stop.* |

`[N]` is the evaluated-address-set count and is a placeholder until Sol's validated output computes it. Do not fill it in by hand before then.

---

## 6. Anticipated questions, and the honest answer

- **"How do you know the extraction is right?"** Two independent readers, one Claude and one GPT, extracted the same documents blind. Disagreements are published, not reconciled away. None was fabrication; all were about how to represent uncertainty.
- **"Is extraction really automated?"** One generic prompt, one command, no per-document code. Show the audit log, not the source.
- **"Why so many unknowns?"** Because the assessor data lacks unit counts and build years for whole cities. Naming that is the honest result; filling it would be the failure.
- **"Can you verify the corpus is untampered?"** No. The manifest hashes match nothing in the distributed text and their scope is unknown. We record our own hash and say so on screen.

---

## 7. Fallback tiers — decide at T-minus-10, only ever move down

1. **Tier 2 (primary, on stage).** Real UI over frozen, validated output files and shipped source documents, served from local disk. No network calls of any kind. Drop this tier if any checkpoint takes over two seconds, or any card shows a value not in the validated files.
2. **Tier 1 (deployed link).** Same files, hosted. For the submission link and a judge who wants to open it themselves. Not used on stage.
3. **Tier 3 (recording).** A 90-second local MP4 of the six clicks, 1440×900, reduced motion on, on the laptop and a USB stick. Narrate the 60-second script over it live.
4. **Tier 4 (stills).** An eight-frame PDF. The 3-minute script was written to the frames and runs unchanged against them.

**Recovery budget: five seconds.** The line is "that is the live one, here is the recording," then play. Never debug in front of a judge.

---

*Source boards: Main.dc.html ("Judge flow, 8 beats"), Storyboard.dc.html ("Storyboard, scripts, fallback"), EvidenceDebt.dc.html, from the frozen design canvas "Rental Law Navigator: Judge Demo Design." Field-level data binding — what each line binds to once the backend clears — lives separately in the project doc "Demo run sheet and data-binding map." This script does not bind, alter, or assume any backend output; `[N]` and any other bracketed value remain placeholders until that document's reconciliation checklist (section 7) is run.*
