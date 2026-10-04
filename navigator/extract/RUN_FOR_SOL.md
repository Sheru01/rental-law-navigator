# Running the extraction (for Sol)

Nothing in this folder has called a real model. Everything below was tested offline with
scripted transports and canned echo fixtures. The first real run is yours.

## DO NOT PASTE THE KEY INTO ANY FILE

- The key lives only in the OPENAI_API_KEY environment variable of the terminal you run in.
- Do not write it into a file, a config, a script, a notebook, a commit, a chat message or
  a prompt. Do not type it inline on the command line (`OPENAI_API_KEY=sk-... python3 ...`
  puts it in your shell history). Use the hidden prompt below instead.
- The pipeline never reads it into a variable it keeps, never logs it and never writes it.
  Provider error text and model output are scrubbed before they reach any file, and the
  tests plant a fake key and check that no output or cache file contains it. That scrubbing
  is a safety net, not permission to be careless.

## Steps (from the project root)

1. Install the SDK, once:

       python3 -m pip install openai

2. Supply the key for this terminal session only.

   bash:

       read -rsp "OpenAI API key: " OPENAI_API_KEY; export OPENAI_API_KEY; echo

   zsh (macOS default):

       read -rs "OPENAI_API_KEY?OpenAI API key: "; export OPENAI_API_KEY; echo

3. Optional smoke test on seven documents (writes to navigator/out/partial/, not to rules.json):

       python3 -m navigator.extract --transport openai --docs D022,D001,D069,D076,D081,D039,D078

4. The full run (74 documents: 54 in corpus/text, 20 in corpus/fetched):

       python3 -m navigator.extract --transport openai

   That is the one-line command. It writes navigator/out/rules.json, rules_withheld.json,
   audit_log.jsonl and extract_report.json, and caches raw model output in
   navigator/out/cache/ (rerunning is nearly free; add --no-cache to force fresh calls).

The default model id is `gpt-5`. That id was never exercised from here. If your account uses
a different id, add `--model <id>`. Other flags: `--concurrency N` (default 4, max 8),
`--as-of YYYY-MM-DD` (default 2026-10-01), `--summary-policy drop|flag` (see below),
`--transport anthropic` (needs ANTHROPIC_API_KEY and `python3 -m pip install anthropic`).

Exit codes: 0 ok, 1 some document errored, 2 setup problem (package, key, rejected
credentials), 3 an acceptance check failed or a hard invariant was violated. A nonzero exit
still writes the output files so you can inspect them.

## Expected runtime and cost (estimates, not quotes)

- Calls: 74, one per document, plus at most one re-copy call per document whose quotes fail
  the exact-match check, plus at most one JSON re-ask.
- Input: roughly 0.42 million tokens (177 thousand for the documents, about 3.2 thousand for
  the prompt on each call), more if many re-copy calls occur. D067 alone is about 40 thousand tokens.
- Output: unknown until it runs. Each rule record carries per-field epistemic entries, so
  expect on the order of 1.5 to 2.5 thousand tokens per rule. A reasoning model adds hidden tokens.
- Time: with 4 concurrent calls, plan for roughly 10 to 45 minutes.
- Money: on the order of a few dollars to a few tens of dollars at typical frontier-model
  rates. Check current pricing for the model you choose. Per standing rules, get Orion's
  approval before spending.

## What to check in the output

1. `navigator/out/extract_report.json`
   - `is_real_extraction` is true; `documents_errored` is empty (an errored document is NOT an empty one).
   - `acceptance.all_passed` is true. Read every failed check; each names what went wrong.
   - `acceptance.model_confirmed` is true, so the eight navigation shells (D078, D082, D012,
     D013, D014, D029, D031, D036) were really returned empty by the model, not by fixture.
   - `invariant_violations` is empty (Los Angeles must have zero algorithmic_rent_setting rules).
   - `drop_reasons`: expect `quoted_span_refused_summary_block` for the corpus/fetched documents
     (see the next section), some `quoted_span_not_found` if the model mis-copied after its one retry.
2. `navigator/out/rules_withheld.json` should hold BERK-ALG-01 (Berkeley) and CA-ALG-01 (AB 325):
   status null, effective_date null, conflict_flag true. If either appears in rules.json with a
   substantive status, or is absent entirely, the model over-read or under-read: report it, do not patch it.
3. `navigator/out/rules.json`: spot-check that every rule has `status_basis`, per-field `epistemics`,
   and a `quoted_span` that you can find in the source file. D081 (San Francisco) must say
   "official summary, not the legal text" in its title, requirement and citation. NJ-ALG-01 should
   carry effective_date 2027-07-01 as DERIVED, and its conflict_note must say "enacting" only.
4. `navigator/out/audit_log.jsonl`: one `document` line per file and one `rule` line per candidate,
   with raw model output, every normalization (before and after), span verification attempts and
   drop reasons. The key `text_file_sha256_local` is the hash of the text file as distributed.
   `manifest_hash_scope` is `unknown` everywhere: the manifest hash does NOT validate the text.
5. Search the output folder for your key before sharing anything:

       grep -rIl "sk-" navigator/out || echo "no key-like strings found"

## Decisions you should know about

- **Summary blocks.** Every file in corpus/fetched is a paraphrase end to end (each block starts
  with the line `[SUMMARY, NOT SOURCE TEXT]` and there is no end marker), so they contain no
  quotable text. The default policy DROPS any record whose quoted span lies in such a region and
  logs it, which means no HOB-ALG-01 and no MA-RENT-P1 will appear in rules.json. The candidate
  record is still in audit_log.jsonl. `--summary-policy flag` instead keeps such records as
  summary_only: confidence at most 0.4, conflict_flag true, every field at most inferred, no
  effective date, and the title and requirement marked "fetch-tool summary, not source text". Their
  quoted_span is then NOT source evidence (quoted_span_kind says so). That is the lead's call.
- **Withheld projections.** A record whose operative status cannot be established is written to
  rules_withheld.json with status null, not to rules.json, because the engine rejects a null status.
- **The California default effective-date rule is not applied here.** It lives in
  navigator/normalize/effective_date.py. The engine must be wired to call it for withheld
  projections (for example AB 325, which states approval on October 06, 2025 and no operative date).
- Cross-document precedence (overrides and interaction) is resolved only from relations the model
  declares, matched to other extracted rules by jurisdiction and category. Unresolved relations are
  kept in `relations_unresolved`. Missing or withheld targets are never silently linked.
