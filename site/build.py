#!/usr/bin/env python3
"""Build site/dist/index.html from engine output.

Every value the page can display is read from the navigator pipeline's own
output files. Nothing about a rule, a result or a jurisdiction is authored
here: this script transports validated output into a single self-contained
page and fails loudly when something it expects is missing.

Run from the project root:

    python3 site/build.py --rules navigator/out/echo --as-of 2026-10-01 \
        --as-of-b 2027-07-01

Design follows the frozen eight-board canvas in design/ (light theme, IBM
Plex, equal-weight uncertainty, ochre never red).
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from navigator.engine import run as engine_run  # noqa: E402

WS = re.compile(r"\s+")


def norm_ws(s):
    """Collapse whitespace runs to single spaces, per CONTRACT.md section 7."""
    return WS.sub(" ", s or "").strip()


class Interner:
    """Collapse repeated strings to integer ids so the payload stays small."""

    def __init__(self):
        self.items = []
        self._index = {}

    def intern(self, s):
        if s is None:
            return None
        key = s
        got = self._index.get(key)
        if got is None:
            got = len(self.items)
            self._index[key] = got
            self.items.append(s)
        return got


def load_addresses_csv(path):
    rows = engine_run.load_sample_addresses(str(path))
    if not rows:
        raise SystemExit("no sample addresses at %s" % path)
    return rows


def read_source_docs(doc_ids):
    """Return {doc_id: {text, sha256, path}} for every referenced document."""
    out = {}
    for doc_id in sorted(d for d in doc_ids if d):
        found = None
        for sub in ("text", "fetched"):
            cand = ROOT / "corpus" / sub / ("%s.txt" % doc_id)
            if cand.exists():
                found = cand
                break
        if not found:
            out[doc_id] = {"missing": True}
            continue
        raw = found.read_text(encoding="utf-8", errors="replace")
        out[doc_id] = {
            "text": raw,
            "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "path": str(found.relative_to(ROOT)),
            "summary_sourced": "[SUMMARY, NOT SOURCE TEXT]" in raw,
        }
    return out


def read_manifest():
    """Retrieval dates per doc id, from the organizer manifest."""
    path = ROOT / "corpus" / "corpus_manifest.csv"
    if not path.exists():
        return {}
    import csv

    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            doc = row.get("doc_id") or row.get("id")
            if doc:
                out[doc] = {
                    "retrieved_at": row.get("retrieved_at") or row.get("retrieved"),
                    "url": row.get("url") or row.get("source_url"),
                }
    return out


def verify_spans(rules, docs):
    """Exact-substring check per CONTRACT.md section 7. Returns per-rule status."""
    report = {}
    for rid, rule in rules.items():
        span = rule.get("quoted_span")
        doc_id = rule.get("source_doc_id")
        doc = docs.get(doc_id) or {}
        if not span:
            report[rid] = {"ok": False, "why": "no quoted_span on the record"}
            continue
        if doc.get("missing") or "text" not in doc:
            report[rid] = {"ok": False, "why": "source document %s not in corpus" % doc_id}
            continue
        if norm_ws(span) and norm_ws(span) in norm_ws(doc["text"]):
            report[rid] = {"ok": True, "summary_sourced": bool(doc.get("summary_sourced"))}
        else:
            report[rid] = {"ok": False, "why": "span not found in %s after whitespace normalisation" % doc_id}
    return report


VARYING = ("result", "result_state", "conflict_flag", "explanation", "reason",
           "evidence_status", "reason_code", "temporal_result")
# Fields that are identical for every address a rule touches get hoisted once.
HOISTED = ("needs_fact", "date_derivation", "citation", "source_doc_id",
           "source_url", "evidence_basis")


def compact_lookups(lookups, interner, hoist_sink):
    """Split entries into per-address varying parts and per-rule hoisted parts."""
    out = {}
    for aid, entries in lookups.items():
        packed = []
        for e in entries:
            rid = e["team_rule_id"]
            # Hoist the invariant evidence, keyed by rule and result_state so a
            # rule that resolves differently elsewhere keeps its own record.
            hkey = "%s|%s" % (rid, e.get("result_state") or e.get("result"))
            if hkey not in hoist_sink:
                hoist_sink[hkey] = {k: e[k] for k in HOISTED if k in e}
            packed.append({
                "r": rid,
                "h": hkey,
                "s": e.get("result"),
                "rs": e.get("result_state"),
                "c": 1 if e.get("conflict_flag") else 0,
                "x": interner.intern(e.get("explanation")),
                "w": interner.intern(e.get("reason")),
                "es": e.get("evidence_status"),
                "tr": e.get("temporal_result"),
            })
        out[aid] = packed
    return out


def _resolve(p):
    """Accept paths given relative to the project root or absolute."""
    path = Path(p)
    return path if path.is_absolute() else (ROOT / path)


def build_payload(args):
    rules_dir = _resolve(args.rules)
    rules_path = rules_dir / "rules.json" if rules_dir.is_dir() else rules_dir
    withheld_path = rules_path.with_name("rules_withheld.json")

    rules = engine_run.load_rules(str(rules_path))
    withheld = engine_run.load_withheld(str(withheld_path))

    juris_path = _resolve(args.jurisdictions)
    juris_live = juris_path.exists()
    juris = engine_run.load_jurisdictions(str(juris_path)) if juris_live else {}

    sample = load_addresses_csv(ROOT / "data" / "sample_addresses.csv")
    addresses = engine_run.build_addresses(juris, sample)

    lk_a, stats_a = engine_run.run_lookups(addresses, rules, args.as_of, withheld)
    lk_b, stats_b = engine_run.run_lookups(addresses, rules, args.as_of_b, withheld)

    rule_map = {r["team_rule_id"]: r for r in rules} if isinstance(rules, list) else dict(rules)
    # A withheld projection still reaches lookups (as unknown/indeterminate), so
    # the card needs its title, requirement and citation. Merge it in, marked, so
    # the page can show what the rule says while never treating it as operative.
    for w in withheld:
        rid = w.get("team_rule_id")
        if rid and rid not in rule_map:
            entry = dict(w)
            entry["_withheld"] = True
            rule_map[rid] = entry
    docs = read_source_docs(
        [r.get("source_doc_id") for r in rule_map.values()]
        + [w.get("source_doc_id") for w in withheld]
    )
    spans = verify_spans(rule_map, docs)
    manifest = read_manifest()

    interner = Interner()
    hoist = {}
    packed_a = compact_lookups(lk_a, interner, hoist)
    packed_b = compact_lookups(lk_b, interner, hoist)

    # Address records: the assessor row plus whatever the geocoder established.
    addr_out = {}
    for aid, row in addresses.items():
        src = sample.get(aid, {})
        j = juris.get(aid, {}) if isinstance(juris, dict) else {}
        addr_out[aid] = {
            "id": aid,
            "street": src.get("street_address") or src.get("street") or "",
            "postal_city": src.get("postal_city") or src.get("city") or "",
            "state": row.get("state"),
            "zip": src.get("zip") or src.get("postal_code") or "",
            "use": src.get("use_description") or "",
            "legal_city": row.get("jurisdiction"),
            "year_built": row.get("year_built"),
            "units": row.get("units"),
            "match_status": j.get("match_status"),
            "method": j.get("method") or j.get("resolution_method"),
            "flags": j.get("flags") or [],
        }

    changes_path = ROOT / "navigator" / "out" / "changes.json"
    changes = json.loads(changes_path.read_text()) if changes_path.exists() else None

    return {
        "meta": {
            "built": date.today().isoformat(),
            "as_of_a": args.as_of,
            "as_of_b": args.as_of_b,
            "rules_source": str(rules_path.relative_to(ROOT)),
            "is_real_extraction": _is_real_extraction(rules_path),
            "jurisdictions_live": juris_live,
            "jurisdictions_source": str(juris_path.relative_to(ROOT)) if juris_live else None,
            "address_count": len(addr_out),
            "rule_count": len(rule_map),
            "withheld_count": len(withheld),
            "stats_a": {k: dict(v) if hasattr(v, "items") else v for k, v in stats_a.items()},
            "manifest_hash_scope": "unknown",
        },
        "addresses": addr_out,
        "rules": rule_map,
        "withheld": withheld,
        "spans": spans,
        "docs": {k: v for k, v in docs.items()},
        "manifest": manifest,
        "hoist": hoist,
        "strings": interner.items,
        "lookups": {"a": packed_a, "b": packed_b},
        "changes": changes,
    }


def _is_real_extraction(rules_path):
    report = rules_path.with_name("extract_report.json")
    if report.exists():
        try:
            return bool(json.loads(report.read_text()).get("is_real_extraction"))
        except Exception:
            return False
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", default="navigator/out/echo")
    ap.add_argument("--jurisdictions", default="navigator/out/jurisdictions.json")
    ap.add_argument("--as-of", default="2026-10-01")
    ap.add_argument("--as-of-b", default="2027-07-01")
    ap.add_argument("--out", default="site/dist/index.html")
    args = ap.parse_args()

    payload = build_payload(args)
    template = (ROOT / "site" / "template.html").read_text(encoding="utf-8")
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # </script> inside data would close the tag early.
    blob = blob.replace("</", "<\\/")
    html = template.replace("/*__DATA__*/null", blob)

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    bad = [r for r, v in payload["spans"].items() if not v.get("ok")]
    print("build: %d addresses, %d rules, %d withheld, %d strings interned"
          % (payload["meta"]["address_count"], payload["meta"]["rule_count"],
             payload["meta"]["withheld_count"], len(payload["strings"])))
    print("build: real_extraction=%s jurisdictions_live=%s"
          % (payload["meta"]["is_real_extraction"], payload["meta"]["jurisdictions_live"]))
    print("build: spans verified ok for %d rules; failed: %s"
          % (len(payload["spans"]) - len(bad), bad or "none"))
    print("build: wrote %s (%.0f KB)" % (args.out, out.stat().st_size / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
