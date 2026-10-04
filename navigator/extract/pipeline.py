"""Extraction pipeline.

    python3 -m navigator.extract --transport openai

One generic prompt (navigator/extract/prompt.md), one model call per document, bounded
concurrency. The model proposes; code verifies every quoted span, enforces the four
epistemic categories, withholds projections whose status cannot be established, and
writes the audit log. There is no per-document branching anywhere in this package.

Outputs (default navigator/out/):
    rules.json            {"rules": [...]}  schema-valid records with an establishable status
    rules_withheld.json   withheld projections: status null, NOT consumable by the engine
    audit_log.jsonl       one line per document and per candidate rule
    extract_report.json   counts, drop reasons, acceptance checks
    cache/                raw model output per document (never credentials)

Exit codes: 0 ok, 1 some documents errored, 2 setup problem (package, key, bad flag),
3 acceptance check failed or a hard invariant was violated.
"""
import argparse
import concurrent.futures
import datetime
import hashlib
import json
import os
import re
import sys
import threading
from pathlib import Path

from navigator.extract import acceptance, assemble as asm
from navigator.extract.corpus import list_documents
from navigator.extract.records import build_record
from navigator.extract.redact import Redactor
from navigator.extract.spans import VERIFIED, collect_spans, verify_span
from navigator.extract.transports import (DEFAULT_MODELS, FatalTransportError, SetupError,
                                          TransportError, make_transport)

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = Path(__file__).resolve().parent
PROMPT_PATH = PACKAGE_DIR / "prompt.md"
EXTERNAL_PATH = PACKAGE_DIR / "external_claims.json"
CACHE_FORMAT = "cache-v1"
REPAIRABLE = ("not_found", "refused_summary_block", "refused_header", "too_short", "empty", "not_a_string")


# ------------------------------------------------------------------- prompt

def load_prompt(path):
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8")
    m = re.match(r"PROMPT_VERSION:\s*(\S+)", text)
    version = m.group(1) if m else "unversioned"
    return text, version, hashlib.sha256(raw).hexdigest()


def build_user_message(doc, as_of):
    return "AS_OF: %s\n\nTEXT_FILE_BEGIN\n%s\nTEXT_FILE_END\n" % (as_of.isoformat(), doc.raw_text.rstrip("\n"))


def repair_message(failures):
    lines = [
        "Some quoted spans you returned were rejected by an exact-match check against the document text.",
        "For each item below, copy the passage again, character for character, from the document body.",
        "Never copy from the SOURCE:/RETRIEVED: header lines and never from text after a line that reads "
        "[SUMMARY, NOT SOURCE TEXT]. Do not paraphrase. If no exact passage exists, use null for text.",
        "Items:"]
    why = {"not_found": "not found in the document body",
           "refused_summary_block": "it lies inside a [SUMMARY, NOT SOURCE TEXT] region, which is not source text",
           "refused_header": "it is from the header lines, not the document body",
           "too_short": "too short", "empty": "empty", "not_a_string": "not a string"}
    for f in failures:
        lines.append("- record_index %d, span_id %s, rejected because: %s. You wrote: %s" % (
            f["index"], f["id"], why.get(f["result"], f["result"]), json.dumps(f["text"], ensure_ascii=True)))
    lines.append('Return JSON only: {"repairs": [{"record_index": 0, "span_id": "quoted_span", "text": "..."}]}')
    return "\n".join(lines)


# ------------------------------------------------------------------ parsing

def parse_json_text(text):
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except ValueError:
        i, j = s.find("{"), s.rfind("}")
        if i != -1 and j > i:
            return json.loads(s[i:j + 1])
        raise


def parse_extraction(text):
    """Return (document_meta, candidate list). Raises ValueError if the shape is unusable."""
    obj = parse_json_text(text)
    if isinstance(obj, list):
        return {}, obj
    if isinstance(obj, dict) and isinstance(obj.get("rules"), list):
        meta = obj.get("document") if isinstance(obj.get("document"), dict) else {}
        return meta, obj["rules"]
    raise ValueError("top-level JSON has no 'rules' array")


# -------------------------------------------------------------------- cache

class DocCache(object):
    def __init__(self, directory, key, doc_id, enabled, redactor=None):
        self.enabled = enabled
        self.redactor = redactor or Redactor()
        self.path = Path(directory) / ("%s.%s.json" % (doc_id, key[:16])) if enabled else None
        self.key = key
        self.steps = {}
        self.hit = False
        if enabled and self.path.is_file():
            try:
                with open(self.path, encoding="utf-8") as fh:
                    data = json.load(fh)
                if data.get("key") == key and isinstance(data.get("steps"), dict):
                    self.steps = data["steps"]
            except (OSError, ValueError):
                self.steps = {}

    def get(self, step):
        if step in self.steps:
            self.hit = True
            return self.steps[step]
        return None

    def put(self, step, raw):
        raw = self.redactor.text(raw)          # a credential must never reach the cache either
        self.steps[step] = raw
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"key": self.key, "format": CACHE_FORMAT, "steps": self.steps}, fh,
                          ensure_ascii=True, indent=1)
            os.replace(tmp, self.path)


def cache_key(prompt_sha, doc, transport, as_of):
    blob = json.dumps([CACHE_FORMAT, prompt_sha, doc.text_file_sha256_local, transport.name,
                       transport.model, as_of.isoformat()])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ------------------------------------------------------------ per-document

class DocResult(object):
    def __init__(self, doc):
        self.doc = doc
        self.status = "ok"
        self.error = None
        self.cache_hit = False
        self.raw = {}
        self.meta = {}
        self.cands = []
        self.repair_error = None


def _verify_all(cand_dicts, doc):
    """Return {index: {span_id: {...}}} for every dict candidate."""
    out = {}
    for i, c in enumerate(cand_dicts):
        if not isinstance(c, dict):
            continue
        table = {}
        for s in asm_collect(c):
            v = verify_span(s["text"], doc, s["minimum"])
            table[s["id"]] = {"id": s["id"], "text": s["text"], "supports": s["supports"],
                              "result": v["result"], "normalized": v["normalized"],
                              "attempts": [{"attempt": 1, "result": v["result"]}]}
        out[i] = table
    return out


def asm_collect(c):
    return collect_spans(c)


def process_document(doc, transport, system, prompt_meta, as_of, cache, summary_policy, external_map):
    res = DocResult(doc)
    user = build_user_message(doc, as_of)
    base = [{"role": "user", "content": user}]

    def call(step, messages):
        raw = cache.get(step)
        if raw is None:
            raw = transport.complete(system, messages, doc.doc_id, step)
            cache.put(step, raw)
        res.raw[step] = raw
        return raw

    try:
        raw = call("initial", base)
        try:
            meta, cands = parse_extraction(raw)
        except ValueError:
            raw2 = call("json_retry", base + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": "That reply was not a valid JSON object with a \"rules\" array. "
                                            "Return only the JSON object described in the instructions."}])
            meta, cands = parse_extraction(raw2)
    except FatalTransportError:
        raise
    except TransportError as exc:
        res.status, res.error = "error", "transport: %s" % exc
        res.cache_hit = cache.hit
        return res
    except ValueError as exc:
        res.status, res.error = "error", "model_output_not_json: %s" % exc
        res.cache_hit = cache.hit
        return res
    res.meta = meta
    res.cache_hit = cache.hit

    tables = _verify_all(cands, doc)
    failures = []
    for i, table in tables.items():
        for sid, s in table.items():
            if s["result"] != VERIFIED and s["result"] in REPAIRABLE:
                failures.append({"index": i, "id": sid, "result": s["result"], "text": s["text"]})
    if failures:
        try:
            raw_r = call("repair", base + [{"role": "assistant", "content": res.raw["initial"]},
                                           {"role": "user", "content": repair_message(failures)}])
            try:
                repairs = parse_json_text(raw_r).get("repairs") or []
            except (ValueError, AttributeError):
                repairs = []
                res.repair_error = "repair reply was not valid JSON"
        except FatalTransportError:
            raise
        except TransportError as exc:
            repairs, res.repair_error = [], "transport: %s" % exc
        wanted = {(f["index"], f["id"]) for f in failures}
        done = set()
        for rp in repairs if isinstance(repairs, list) else []:
            if not isinstance(rp, dict):
                continue
            key = (rp.get("record_index"), rp.get("span_id"))
            if key not in wanted or key in done:
                continue
            done.add(key)
            s = tables[key[0]][key[1]]
            text = rp.get("text")
            minimum = next(x["minimum"] for x in asm_collect(cands[key[0]]) if x["id"] == key[1])
            v = verify_span(text, doc, minimum) if isinstance(text, str) else {"result": "not_found", "normalized": None}
            s["attempts"].append({"attempt": 2, "result": v["result"], "text": text if isinstance(text, str) else None})
            if v["result"] == VERIFIED:
                s["result"], s["normalized"], s["text"] = VERIFIED, v["normalized"], text
            else:
                s["result"] = v["result"] if v["result"] != "not_a_string" else s["result"]
        for key in wanted - done:
            tables[key[0]][key[1]]["attempts"].append({"attempt": 2, "result": "repair_not_supplied"})

    ctx_base = {"as_of": as_of, "doc_source_kind": (meta or {}).get("source_kind"),
                "prompt_version": prompt_meta["version"], "prompt_sha256": prompt_meta["sha256"],
                "transport": transport.name, "model": transport.model,
                "external": external_map.get(doc.doc_id, []), "summary_policy": summary_policy}
    for i, cand in enumerate(cands):
        c = asm.Cand(doc.doc_id, i, cand)
        if isinstance(cand, dict):
            table = tables[i]
            c.span_report = [{"id": s["id"], "supports": s["supports"], "text": s["text"],
                              "attempts": s["attempts"], "final_result": s["result"]} for s in table.values()]
            c.built = build_record(cand, i, doc, table, ctx_base)
        else:
            from navigator.extract.records import Built
            c.built = Built()
            c.built.drops.append("candidate_not_an_object")
        res.cands.append(c)
    return res


# --------------------------------------------------------------------- run

def _load_external(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, list)}


def _write_json(path, obj, red):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(red.obj(obj), fh, ensure_ascii=True, indent=2, sort_keys=False)
        fh.write("\n")


def _drop_code(reason):
    return re.split(r"[:( ]", reason, 1)[0]


def run(argv=None, out=None, err=None):
    out = out or sys.stdout
    err = err or sys.stderr
    ap = argparse.ArgumentParser(prog="python3 -m navigator.extract",
                                 description="Extract rule records from the corpus with one generic prompt.")
    ap.add_argument("--transport", choices=("openai", "anthropic", "echo"), default="openai")
    ap.add_argument("--model", default=None, help="default: %s" % DEFAULT_MODELS)
    ap.add_argument("--as-of", default="2026-10-01")
    ap.add_argument("--corpus-dir", default=str(ROOT / "corpus"))
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--cache-dir", default=str(ROOT / "navigator" / "out" / "cache"))
    ap.add_argument("--docs", default=None, help="comma-separated doc ids; output goes to out/partial/")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--summary-policy", choices=("drop", "flag"), default="drop")
    ap.add_argument("--echo-fixtures", default=None)
    ap.add_argument("--prompt", default=str(PROMPT_PATH))
    args = ap.parse_args(argv)

    red = Redactor()
    try:
        as_of = datetime.date.fromisoformat(args.as_of)
    except ValueError:
        err.write("--as-of must be YYYY-MM-DD\n")
        return 2
    if not 1 <= args.concurrency <= 8:
        err.write("--concurrency must be between 1 and 8\n")
        return 2
    only = [x for x in args.docs.split(",")] if args.docs else None
    try:
        docs = list_documents(args.corpus_dir, only)
    except ValueError as exc:
        err.write("%s\n" % exc)
        return 2
    if not docs:
        err.write("no documents found under %s\n" % args.corpus_dir)
        return 2
    try:
        transport = make_transport(args.transport, args.model, args.echo_fixtures)
    except SetupError as exc:
        err.write(red.text(str(exc)) + "\n")
        return 2

    if args.out_dir:
        out_dir = Path(args.out_dir)
    elif args.transport == "echo":
        out_dir = ROOT / "navigator" / "out" / "echo"
    elif only:
        out_dir = ROOT / "navigator" / "out" / "partial"
    else:
        out_dir = ROOT / "navigator" / "out"

    system, version, psha = load_prompt(args.prompt)
    prompt_meta = {"version": version, "sha256": psha}
    external_map = _load_external(EXTERNAL_PATH)
    use_cache = (args.transport != "echo") and not args.no_cache
    started = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    results = {}
    lock = threading.Lock()
    fatal = []

    def work(doc):
        cache = DocCache(args.cache_dir, cache_key(psha, doc, transport, as_of), doc.doc_id, use_cache, red)
        return process_document(doc, transport, system, prompt_meta, as_of, cache,
                                args.summary_policy, external_map)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futs = {pool.submit(work, d): d for d in docs}
        for f in concurrent.futures.as_completed(futs):
            d = futs[f]
            try:
                r = f.result()
            except FatalTransportError as exc:
                fatal.append(str(exc))
                for g in futs:
                    g.cancel()
                break
            with lock:
                results[d.doc_id] = r
    if fatal:
        err.write("Stopping: the provider rejected the credentials (%s). Check that the key is valid "
                  "and exported in this shell. Nothing was written.\n" % red.text(fatal[0]))
        return 2

    ordered = [results[d.doc_id] for d in docs]
    all_cands = [c for r in ordered for c in r.cands]
    ares = asm.assemble(all_cands, forbidden=acceptance.FORBIDDEN_RECORDS)

    doc_outcomes = {}
    for r in ordered:
        doc_outcomes[r.doc.doc_id] = {"status": r.status, "error": r.error, "n_candidates": len(r.cands)}
    acc = acceptance.run_acceptance(doc_outcomes, ares.rules, ares.withheld, transport.name)

    # ---- audit log
    events = [{"event": "run", "started_utc": started, "transport": transport.name, "model": transport.model,
               "as_of": as_of.isoformat(), "prompt_version": version, "prompt_sha256": psha,
               "summary_policy": args.summary_policy, "documents": len(docs), "concurrency": args.concurrency,
               "is_real_extraction": transport.name != "echo",
               "manifest_hash_scope": "unknown",
               "provenance_note": "text_file_sha256_local is the hash of the text file as distributed. "
                                  "The manifest sha256 is carried as unverified with scope unknown; it does "
                                  "not validate the text (navigator/out/provenance_hash_finding.json)."}]
    for g in acceptance.KNOWN_GAPS:
        events.append({"event": "known_gap", "name": g["name"], "detail": g["detail"]})
    for r in ordered:
        d = r.doc
        events.append({
            "event": "document", "doc_id": d.doc_id, "url": d.url, "retrieved_at": d.retrieved_at,
            "text_file": d.rel_path, "text_file_sha256_local": d.text_file_sha256_local,
            "manifest_hash_scope": "unknown", "manifest_sha256_reported_unverified": d.manifest_sha256,
            "has_quotable_text": d.has_quotable_text, "has_summary_region": d.has_summary_region,
            "prompt_version": version, "prompt_sha256": psha, "transport": transport.name,
            "model": transport.model, "cache_hit": r.cache_hit, "status": r.status, "error": r.error,
            "repair_error": r.repair_error, "document_meta": r.meta, "n_candidates": len(r.cands),
            "raw_model_output": r.raw})
        for c in r.cands:
            events.append({
                "event": "rule", "doc_id": d.doc_id, "candidate_index": c.index, "outcome": c.outcome,
                "team_rule_id": c.final_id, "url": d.url, "retrieved_at": d.retrieved_at,
                "text_file_sha256_local": d.text_file_sha256_local, "manifest_hash_scope": "unknown",
                "prompt_version": version, "raw_model_output": c.raw,
                "span_verification": c.span_report,
                "normalizations": (c.built.norms if c.built else []),
                "drop_reasons": c.drop_reasons})
    events.append({"event": "assembly", "duplicates": ares.duplicates, "invariant_violations": ares.violations,
                   "relations": ares.relation_log})
    events.append({"event": "acceptance", **acc})

    # ---- report
    drop_hist = {}
    per_doc = []
    for r in ordered:
        k = w = x = 0
        for c in r.cands:
            if c.outcome == "kept":
                k += 1
            elif c.outcome == "withheld":
                w += 1
            else:
                x += 1
                for reason in c.drop_reasons:
                    drop_hist[_drop_code(reason)] = drop_hist.get(_drop_code(reason), 0) + 1
        per_doc.append({"doc_id": r.doc.doc_id, "status": r.status, "candidates": len(r.cands),
                        "kept": k, "withheld": w, "dropped": x, "error": r.error})
    report = {
        "started_utc": started, "transport": transport.name, "model": transport.model,
        "is_real_extraction": transport.name != "echo", "as_of": as_of.isoformat(),
        "prompt_version": version, "prompt_sha256": psha, "summary_policy": args.summary_policy,
        "documents": len(docs), "documents_errored": [p["doc_id"] for p in per_doc if p["status"] != "ok"],
        "candidates": sum(p["candidates"] for p in per_doc), "rules_written": len(ares.rules),
        "withheld_projections": len(ares.withheld),
        "dropped": sum(p["dropped"] for p in per_doc), "drop_reasons": drop_hist,
        "cache_hits": sum(1 for r in ordered if r.cache_hit),
        "invariant_violations": ares.violations, "duplicates": ares.duplicates,
        "acceptance": acc, "per_document": per_doc,
        "known_gaps": [g["name"] for g in acceptance.KNOWN_GAPS],
        "engine_wiring_required": "The engine must call navigator.normalize.effective_date.derive_effective_date "
                                  "for withheld projections; extraction never applies it.",
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "rules.json", {"rules": ares.rules}, red)
    _write_json(out_dir / "rules_withheld.json", {
        "note": "Withheld projections: operative status is not establishable from the source, so status is "
                "null and these records are NOT valid input for the engine. They are kept so the evidence "
                "and the reason are not lost.",
        "withheld_projections": ares.withheld}, red)
    with open(out_dir / "audit_log.jsonl", "w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(red.obj(ev), ensure_ascii=True) + "\n")
    report["redactions_applied"] = red.count
    _write_json(out_dir / "extract_report.json", report, red)

    out.write("transport=%s model=%s real_extraction=%s\n" % (transport.name, transport.model, transport.name != "echo"))
    out.write("documents=%d errored=%d candidates=%d rules=%d withheld=%d dropped=%d\n" % (
        len(docs), len(report["documents_errored"]), report["candidates"], len(ares.rules),
        len(ares.withheld), report["dropped"]))
    out.write("drop reasons: %s\n" % (json.dumps(drop_hist, sort_keys=True) if drop_hist else "none"))
    out.write("acceptance: %s (failed: %s)\n" % ("PASS" if acc["all_passed"] else "FAIL", acc["failed"] or "none"))
    if ares.violations:
        out.write("INVARIANT VIOLATIONS: %s\n" % red.text(json.dumps(ares.violations)))
    out.write("wrote %s\n" % _rel(out_dir))
    if not report["is_real_extraction"]:
        out.write("NOTE: echo transport uses canned fixtures. This is NOT a real extraction.\n")
    if ares.violations or not acc["all_passed"]:
        return 3
    if report["documents_errored"]:
        return 1
    return 0


def _rel(p):
    try:
        return str(Path(p).resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def main(argv=None):
    try:
        return run(argv)
    except SetupError as exc:
        sys.stderr.write(Redactor().text(str(exc)) + "\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
