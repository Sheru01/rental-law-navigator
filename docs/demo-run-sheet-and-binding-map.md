# Demo run sheet and data-binding map

Rental Housing Law Navigator, Challenge 2. Production-readiness companion to the eight-board design canvas "Rental Law Navigator: Judge Demo Design". Conceptual scope is frozen at eight boards; this document only operationalizes them. DESIGN FREEZE in effect.

Status of inputs: the UI binds to NOTHING yet. Binding happens only after, in order: (1) an immutable backend commit from Codex, (2) the Sonnet read-only audit passes with no unresolved P0 or P1, (3) Sol produces validated frozen output files. Until then every value on screen is a placeholder slot.

---

## 0. Two guardrails that govern everything below

### 0.1 FROZEN means contract, not validation

In the binding map, FROZEN means the interface or schema contract is fixed (organizer-specified in README section 5 or the rule record schema). It does NOT mean that any particular output value has been evidentially validated. A FROZEN field name is safe to code against; a value in that field is safe to show only after Sol validation. The organizer-defined schema is never changed merely to support the presentation.

### 0.2 All explanatory prose is evidence-conditioned

Static-looking UI copy makes factual claims. Each of the following renders only when its predicate is supported by validated output, and is otherwise omitted (not reworded, not softened):

| Sentence or label | Renders only when |
|---|---|
| "This rule is in force." | operative legal status is independently established in validated output |
| "Enacted" | enactment is supported by a verified span or validated status |
| "the public record does not contain [fact]" | the named building or address fact is null in the validated address data |
| "The mailing city is not the governing jurisdiction." | the address is resolved AND legal city differs from mailing city |
| "Resolved by Census geocoder to the incorporated place." | the address is resolved by that method |
| Any effective-date derivation description | a validated derivation record exists naming the clause read and the rule applied |
| STATED chip | a verified span supports that field |
| DERIVED chip | a validated derivation record supports that field |
| INFERRED chip | the validated record labels that field as inferred with a basis |
| UNKNOWN chip on a field | the validated record marks the field as not supplied |

No evidentiary conclusion is hard-coded because it makes the mockup read well.

### 0.3 The UNKNOWN-card sentence, gated

"This rule is in force. Whether it covers this building turns on a fact the public record does not contain." renders ONLY when both hold:
(a) operative legal status is independently established in validated output; AND
(b) the remaining uncertainty is a missing property or address fact.

It is never generic UNKNOWN copy. If (a) fails, the card uses the INDETERMINATE legal-evidence presentation where validated output supports that distinction. If the distinction is unavailable after audit, degrade per section 5 and section 6.2: render as UNKNOWN with the validated reason text only, no manufactured sentence.

---

## 1. Rehearsal protocol, two distinct timings

There are two scripts with two clocks. Never run one against the other's target.

| Script | Clicks | Target | Source |
|---|---|---|---|
| 60-second script | 1 to 5 | 0:58 to 1:02 | Storyboard board, left column, 142 words |
| 90-second judge flow | 1 to 5 plus the stated-versus-derived beat and the audit glance | 1:28 to 1:32 | Main board, eight beats |
| 3-minute panel script | 1 to 6 | 2:55 to 3:00 | Storyboard board, right column |

Run three timed passes of each from the tier-2 build (local files, no network), stopwatch visible.

| Click | 60s time | Action | Checkpoint that gates the next spoken line | Drop-tier trigger |
|---|---|---|---|---|
| 1 | 0:00 | Paste address from clipboard. Date already set. | Both fields filled. | n/a |
| 2 | 0:08 | Look up. Hands off the mouse. | Legal city visible in left panel. | Panel not landed by +2s |
| 3 | 0:30 | Hover the unknown card. Do not click. Point at MISSING. | MISSING and RESOLVES IF legible. | Card missing, or shows prose where fields are expected |
| 4 | 0:44 | Click "See exact source text". | Span highlighted in place, two context lines each side. | Highlight absent, or drawer reports no match |
| 5 | 0:54 | Escape to close drawer, then second as-of date. | Diff banner visible, at least one striped card. | Toggle does not re-render, or renders a value not in validated files |
| 6 | 3-min only | Open audit log, point once, close. | One record expanded end to end. | Log missing |

Any checkpoint over two seconds is a tier-2 defect to fix before the session, not a line to talk over.

Also run one full pass with the mouse unplugged (keyboard only), and one with reduced motion enabled at the OS level, because that is how the tier-3 recording is captured.

Cut order if the 60-second script runs long: the 0:22 architecture line first, then the second sentence of 0:10, then any remaining explanatory prose. Never cut 0:30 (the unknown moment). Never cut 0:44 (exact-source evidence).

Spoken claims follow guardrail 0.2: say "in force", "enacted" or "the record does not contain" only when the validated output on screen supports it.

## 2. T minus 10 minutes: tier decision

Full six-click smoke test on the presenting laptop. Decide the tier, then change it only downward.

- Tier 2 (primary stage demo): real UI over frozen validated files and shipped source documents, served from local disk. No model, no geocoder, no fetch of any kind during the demo.
- Tier 1: the deployed link, same files. For the submission and for a judge who wants to open it on their own device. Not used on stage.
- Tier 3: 90-second local MP4 of the six clicks, 1440 by 900, reduced motion on, on laptop and USB stick. Narrate the 60-second script over it.
- Tier 4: eight-frame PDF. The 3-minute script was written to the frames and runs unchanged.

Recovery budget is five seconds. The line is "that is the live one, here is the recording," then play. Never debug in front of a judge.

## 3. Screenshot framing spec

Common: viewport 1440 by 900, zoom 100%, light mode, cursor hidden, URL bar and browser chrome cropped out, same address and same as-of date throughout, PNG, captured from the tier-2 build the night before.

| Frame | Crop | Must be in frame | Must not be in frame |
|---|---|---|---|
| 01 Empty state | Full viewport | Address and date fields, date visibly filled, footer disclaimer | Any result |
| 02 Jurisdiction | Left 320px plus 60px margin | Mailing city and legal city both legible, differing | Right column |
| 03 Result list | Full viewport | At least three distinct result states | Scrollbar mid-position |
| 04 Unknown card (hero, submission image) | Card plus 24px | MISSING and RESOLVES IF lines legible at thumbnail size | Neighbouring cards |
| 05 Evidence drawer | Drawer plus the card that opened it | Highlighted span centred, two context lines above and below, integrity footer | Scroll position mid-span |
| 06 Diff view | Full viewport | Second date active, banner, at least one striped card | First-date state |
| 07 Empty affected set | One change case block | Count zero and its full reason sentence | Other test blocks |
| 08 Audit log | One record | Retrieval date and local hash line legible | Any secret-shaped string |

## 4. Keyboard and focus behavior, final

- Tab order: address field, date A, date B, Look up, then each result card's "See exact source text" link in reading order, then "Show ruled-out rules".
- Enter on a source link opens the evidence drawer as a modal dialog (role dialog, aria-modal, labelled by its heading). Initial focus lands on the drawer heading. Tab cycles inside the drawer. Escape closes it and returns focus to the exact link that opened it.
- The as-of toggle is a radio group of two buttons. Arrow keys switch; the change is announced via a live region reading the new as-of date.
- Focus rings: 2px, high contrast, never suppressed, including inside the drawer.
- Every icon-only button carries an aria-label. The close control in the drawer is a real button.
- Nothing is reachable by mouse that is not reachable by keyboard. Test by running the six clicks with the mouse unplugged.

## 5. Offline and fallback states, final

| Condition | Rendering | Never |
|---|---|---|
| Address geocoded, rules found | Normal result list | n/a |
| Address geocoded, no rule covers it | Statement of which jurisdictions were searched, how many rules were tested, and why each was ruled out, with the ruled-out list one click away | A blank panel |
| Address not geocoded (unresolved) | Stop at the jurisdiction panel and say so | Fall back to the mailing city and continue |
| Result is unknown, operative status established, gap is a building fact | Full-weight card with the gated sentence (0.3), naming the missing fact and what would resolve it | A thinner or greyer card; generic copy |
| Result is unknown, operative status NOT established, distinction validated | INDETERMINATE presentation: full-weight card stating the legal record is incomplete, with the specific gap | Collapsing into unknown; the gated sentence |
| Result is unknown, distinction NOT available after audit | UNKNOWN chip with the validated reason text only | The gated sentence; any manufactured distinction |
| Span fails exact match | Drawer opens and states the text could not be located in the distributed source, naming the document | Showing a nearby paragraph as if it matched |
| Evidence is summary-sourced | Hatched pane, labelled as summary, lowered confidence on the claim | A paraphrase styled as a quotation |
| Rule status failed | Visible in rule search, absent from every lookup | Appearing in any address result |
| Rule status pending | Reported as pending with last action | Resolving to applies |
| Manifest integrity | Footer states local text hash recorded, manifest hash scope unknown | Any claim the manifest validates the text |
| Change test affected set empty | Count zero with its reason: either no address in the sample is reached, or the evidence to decide was missing, stated as which | A bare empty list |
| Any network call attempted | None exist in tier 2 by construction | A spinner |

## 6. Data-binding map by semantic role

Written by role, not field name, because Codex's output schema is not frozen. The only field names used are those the organizers froze in README section 5 and the rule record schema. Everything else is marked PROVISIONAL or NEEDED and must be reconciled against the actual commit before any binding.

Legend. FROZEN: the contract is organizer-specified and fixed; the field name is safe to code against; the VALUE is still unvalidated until Sol validation (guardrail 0.1). PROVISIONAL: Codex reports it exists; bind only after audit. NEEDED: requested priority P1 to P3; degrade if absent.

### 6.1 Jurisdiction panel

| UI element | Semantic needed | Expected artifact | Status | Degrade if absent |
|---|---|---|---|---|
| Mailing city | postal city as supplied | sample_addresses.csv postal_city | FROZEN | n/a |
| Legal city | incorporated place from geocoder, "City, ST" or null | jurisdictions output, legal city role | PROVISIONAL (Sol-produced, validator-checked) | Show "not resolved" and stop |
| Resolution method and flags | how it was resolved, and warnings | jurisdictions output | PROVISIONAL | Hide the explanatory line |
| Explanatory sentences | see guardrail 0.2 | derived from the above | conditioned | Omit the sentence |
| Building facts | year built, units, as int or null | sample_addresses.csv | FROZEN | n/a |
| Missing-fact badges | which facts are null | derived client-side from the above | n/a | n/a |

### 6.2 Result cards

| UI element | Semantic needed | Expected artifact | Status | Degrade if absent |
|---|---|---|---|---|
| Card presence | one entry per rule that is not omitted | lookups.json: lookups[address_id][] | FROZEN | n/a |
| State chip | one of applies, unknown, superseded, not_yet_effective, pending | lookups.json: result | FROZEN | n/a |
| Indeterminate vs unknown | a flag distinguishing legal-record gap from building-record gap | NEEDED (P1) | NEEDED | Render both as UNKNOWN with the validated reason text; lose the double-rule glyph; never render the gated sentence |
| Why line | the deciding fact named | lookups.json: explanation | FROZEN | n/a |
| MISSING / RESOLVES IF fields | structured missing fact plus reason | NEEDED (P2); reason and needs_fact reported PROVISIONAL | NEEDED | Render explanation as a single prose line |
| The gated UNKNOWN sentence | operative status established AND gap is a building fact | P1 distinction plus validated status | conditioned | Omit |
| Conflict stripe | conflict flag | lookups.json: conflict_flag | FROZEN | n/a |
| Rule title, citation, requirement | rule metadata | rules.json by team_rule_id: title, citation, requirement | FROZEN (schema) | n/a |
| Epistemic chip (stated / derived / inferred / unknown) | per-field category | rules.json extension: inferences list, status_establishable | PROVISIONAL | Show STATED only where a verified span exists; otherwise show no chip |
| Effective date and its basis | date plus stated / derived / not establishable | rules.json: effective_date; derivation record from the normalize module | PROVISIONAL | Show the date with no basis label and no derivation sentence |

### 6.3 Evidence drawer

| UI element | Semantic needed | Expected artifact | Status | Degrade if absent |
|---|---|---|---|---|
| Document identity | doc id, url, retrieval date | rules.json: source_doc_id, source_url; corpus_manifest.csv: retrieved_at | FROZEN | n/a |
| Source text | the distributed text file | corpus/text and corpus/fetched, shipped with the app | FROZEN | n/a |
| Highlighted span | exact span text | rules.json: quoted_span | FROZEN | n/a |
| Highlight position | located by exact substring match, whitespace normalized, at render time | computed client-side | n/a | If no match, state so; never approximate |
| READ pane vs APPLIED pane | for derived values, the clause read and the rule applied | derivation record | PROVISIONAL | Show READ pane only, with a line that the derivation record is unavailable |
| Summary-sourced marker | whether the span comes from a summary block | fetched file marker lines | FROZEN (Worker D format) | n/a |
| Integrity footer | local text hash, manifest scope unknown | audit log local hash; provenance_hash_finding.json | FROZEN (lead-produced) | n/a |

### 6.4 Change tracking

| UI element | Semantic needed | Expected artifact | Status | Degrade if absent |
|---|---|---|---|---|
| Per-test affected count | affected address ids | changes.json: affected_address_ids | FROZEN | n/a |
| Per-test conflict count | conflict-flagged ids | changes.json: conflict_flag_address_ids | FROZEN | n/a |
| Per-test notes | prose for a human reader | changes.json: notes | FROZEN | n/a |
| Struck-through prior state on a card | per-address prior and current result | NEEDED (P3) | NEEDED | Show only the per-test counts and notes; drop the per-card stripe |
| Empty set reason (no reach vs evidence insufficient) | which kind of zero it is | changes.json: notes, and any indeterminate marker Codex emits | PROVISIONAL | Render the notes verbatim and let the prose carry the distinction |

### 6.5 Evidence Debt ledger (closing insight)

Client-side grouping only. No backend logic is requested or assumed, and none will be.

| UI element | Semantic needed | Source | Constraint |
|---|---|---|---|
| Rows | group of (legal city, missing fact) for unknown results; group of (legal city, gap) for indeterminate results | lookups.json results joined to rules.json and jurisdictions, over the evaluated addresses only | Counts are computed from validated output and nothing else |
| Count per row | number of distinct address ids in the group | same | Never estimated, never extrapolated past the evaluated set |
| [N] in the 3-minute script | the evaluated-set count | Sol's validated output | Placeholder until Sol computes it |
| Drill-down | the rule, the predicate not evaluable, the source doc | same records already on the card | Inherits the card's provenance; adds none |

If P1 is absent, the ledger shows one column (unknown) and says the indeterminate column is unavailable. If P2 is absent, the ledger groups by rule only and says the missing-fact grouping is unavailable. It never silently merges or invents a grouping.

## 7. Reconciliation checklist, run only after all three gates

Gates, in order: (1) immutable backend commit; (2) Sonnet read-only audit with no unresolved P0 or P1; (3) Sol validated frozen outputs.

1. Confirm lookups.json, changes.json and rules.json still match README section 5 shapes. Anything else is a defect, not a binding target.
2. Locate the actual field that distinguishes indeterminate from unknown, if any. Record its name here. If none, P1 degrade applies and the gated sentence never renders.
3. Locate the actual structured missing-fact and reason fields. Record names. If prose only, P2 degrade applies.
4. Locate per-address prior and current state in the change output, if any. If absent, P3 degrade applies.
5. Confirm withheld records reach lookups as unknown with a reason, as reported, by checking a known withheld rule id in a known address.
6. Confirm every quoted_span in rules.json exact-matches its source file with whitespace normalized. Any failure blocks the demo until dropped.
7. For every evidence-conditioned sentence in guardrail 0.2, identify the validated field that supports its predicate. A sentence with no supporting field is removed from the build, not left as default copy.
8. Confirm no value rendered in the UI exists outside the validated files. Spot check by grepping three on-screen strings.
9. Only then replace placeholder slots with bindings, and re-run the timed rehearsal passes for all three scripts.
