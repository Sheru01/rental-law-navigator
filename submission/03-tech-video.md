# Item 3: Tech Video (60 seconds max)

Deliverable: a link to a video. `submission/tech-video.mp4` is **58.0 seconds**,
1440x900, with captions burned in, so it can be uploaded as is. The
voiceover below is optional and timed to the slides.

Built from real artifacts: the architecture diagram, the frozen contract text,
the actual engine diff, and the pipeline's own `is_real_extraction` flag.

## Structure, as recorded (matches the guide)

| Section | Time | Length | On screen |
|---|---|---|---|
| 1. Tech stack overview | 0:00 to 0:13 | 13 s | Architecture diagram: corpus to LLM extraction to rules.json; addresses to Census geocoder to jurisdictions.json; both into the deterministic engine, out to a static page |
| 2. Implementation highlights | 0:13 to 0:32 | 19 s | CONTRACT.md section 7 (span rule), then the unknown versus indeterminate split with the three-valued AND |
| 3. Challenges and limitations | 0:32 to 0:52 | 20 s | The real `legal_city` / `jurisdiction` diff plus the test count, then the limitations the product states about itself |
| 4. Reflection | 0:52 to 0:58 | 6 s | Closing line and the URL |

## Transcript (about 155 words, optional voiceover)

**0:00, Tech stack.**
"Python standard library, end to end. A model reads the corpus and emits
structured rules. The Census geocoder resolves every address to its
incorporated place, live, in two passes, because the batch endpoint does not
return it. Then a deterministic engine decides coverage. No language model
decides whether a rule applies."

**0:13, Highlight: spans.**
"Every quoted span has to exact-match its source document after whitespace
normalization, or the record is dropped. We check it at extraction, at build,
and again in the browser at render time. Seven of seven pass."

**0:22, Highlight: two silences.**
"And we never collapse the two kinds of silence. Unknown means a building fact
is missing. Indeterminate means the legal record cannot establish the rule is
operative. Different gaps, different fixes."

**0:32, Challenge.**
"The hard bug: the geocoder wrote legal_city, the engine read jurisdiction.
Nobody had wired them, because the engine had only run against a fixture using
the other spelling. Every one of the five hundred addresses read unresolved.
Only real data exposed it. Fixed with 273 tests green."

**0:42, Limitations.**
"The demo rules replay recorded fixtures rather than a live model run, and the
header says so. A system whose whole argument is that it does not overclaim
cannot overclaim about itself."

**0:52, Reflection.**
"Refusing to guess turned out to be the product."

## Rebuilding it

Slides are `/tmp/demo/tech.html` (committed as `submission/tech-slides.html`);
the video is assembled from six rendered frames at fixed durations, so the
section timings are exact rather than recording-dependent.
