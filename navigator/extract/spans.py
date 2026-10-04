"""Quoted-span verification (CONTRACT.md section 7), with summary-block refusal.

A span verifies only if, after collapsing whitespace runs to single spaces, it is an
exact substring of the document's QUOTABLE text. Matching is case-sensitive and does
not fold quotes, dashes or ligatures: a model that straightens a curly quote is
caught and asked to re-copy.

Results:
  verified                exact substring of quotable text
  refused_summary_block   found only inside a [SUMMARY, NOT SOURCE TEXT] region
  refused_header          found only in the SOURCE:/RETRIEVED: header
  not_found               found nowhere
  too_short / empty / not_a_string
"""
from navigator.extract.corpus import collapse

MIN_PRIMARY = 20
MIN_SUPPORT = 8

VERIFIED = "verified"
REFUSED_SUMMARY = "refused_summary_block"
REFUSED_HEADER = "refused_header"
NOT_FOUND = "not_found"


def verify_span(text, doc, minimum):
    """Return {"result": ..., "normalized": ...}."""
    if not isinstance(text, str):
        return {"result": "not_a_string", "normalized": None}
    norm = collapse(text)
    if not norm:
        return {"result": "empty", "normalized": norm}
    if len(norm) < minimum:
        return {"result": "too_short", "normalized": norm}
    if norm in doc.quotable_norm:
        return {"result": VERIFIED, "normalized": norm}
    if doc.summary_norm and norm in doc.summary_norm:
        return {"result": REFUSED_SUMMARY, "normalized": norm}
    if doc.header_norm and norm in doc.header_norm:
        return {"result": REFUSED_HEADER, "normalized": norm}
    return {"result": NOT_FOUND, "normalized": norm}


def collect_spans(cand):
    """All spans a candidate offers, as a list of {"id","text","supports","minimum"}.
    The primary span has the id "quoted_span". Missing or duplicate ids are made unique."""
    out = [{"id": "quoted_span", "text": cand.get("quoted_span"), "supports": ["requirement"],
            "minimum": MIN_PRIMARY}]
    seen = {"quoted_span"}
    sup = cand.get("supporting_spans")
    if isinstance(sup, list):
        for n, s in enumerate(sup, 1):
            if not isinstance(s, dict):
                continue
            sid = str(s.get("id") or "s%d" % n)
            base, k = sid, 1
            while sid in seen:
                k += 1
                sid = "%s_dup%d" % (base, k)
            seen.add(sid)
            sup_for = s.get("supports")
            if isinstance(sup_for, str):
                sup_for = [sup_for]
            if not isinstance(sup_for, list):
                sup_for = []
            out.append({"id": sid, "text": s.get("text"), "supports": [str(x) for x in sup_for],
                        "minimum": MIN_SUPPORT})
    return out
