# Item 3: Tech Video (60 seconds max)

Deliverable: a link to a video of you explaining how it was built. Record
yourself (Loom, OBS, Zoom) reading this script, about 150 words, timed to the
guide's suggested structure. Show the architecture line from the README or
scroll the repo while talking.

## Script

**Stack, 0:00 to 0:14.**
"The pipeline is plain Python, standard library. An extraction module turns
corpus legal text into structured rule records through swappable model
transports. The Census geocoder resolves each address to its incorporated
place, live, in two passes, because the batch endpoint does not return the
place. A deterministic three-valued engine evaluates coverage, and a build
script compiles everything into one static page, deployed on Vercel."

**Highlights, 0:14 to 0:34.**
"Two things I would defend in review. Every quoted span must exact-match its
source document after whitespace normalization, or the rule is dropped. And
rules whose operative status cannot be established are withheld: they reach
the UI as indeterminate, which is a different state from unknown, because a
gap in the legal record and a gap in the building record have different
fixes."

**Challenges, 0:34 to 0:52.**
"The geocoder and the engine disagreed on a key name, legal_city against
jurisdiction, so every address read as unresolved the first time real data
flowed. Found it by binding real output, fixed it with the 273-test suite
green. And the demo rules come from replayed extraction fixtures, not a live
model run; the page says so in its header, because the whole product is about
not overclaiming."

**Takeaway, 0:52 to 1:00.**
"Refusing to guess turned out to be the product. The unknowns aggregate into
a ledger of what the public record cannot answer."
