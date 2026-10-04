"""Three-valued coverage evaluation (CONTRACT.md sections 3 and 4).

Pure code. No model calls. Nothing in this module reads the clock: as_of is
always passed in by the caller.

evaluate(predicate, address) returns TRUE, FALSE or UNKNOWN.
"""
import datetime
import math
import re
from collections import namedtuple

TRUE = "true"
FALSE = "false"
UNKNOWN = "unknown"

FACTS = ("jurisdiction", "state", "year_built", "units")
STRING_FACTS = ("jurisdiction", "state")
NUMERIC_FACTS = ("year_built", "units")
OPS = ("eq", "in", "lte", "gte", "lt", "gt")
CO_BASIS = "certificate_of_occupancy"
STATUSES = ("in_force", "not_yet_effective", "pending", "failed")

FACT_LABELS = {
    "jurisdiction": "legal jurisdiction",
    "state": "state",
    "year_built": "year built",
    "units": "unit count",
}

_OP_WORDS = {
    "eq": "equals",
    "lte": "is at most",
    "gte": "is at least",
    "lt": "is below",
    "gt": "is above",
    "in": "is one of",
}
_OP_NEG_WORDS = {
    "eq": "does not equal",
    "lte": "is not at most",
    "gte": "is not at least",
    "lt": "is not below",
    "gt": "is not above",
    "in": "is not one of",
}
_COMPARE = {
    "eq": lambda a, b: a == b,
    "lte": lambda a, b: a <= b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "gt": lambda a, b: a > b,
}

_DATE_RE = re.compile(r"\d{4}(-\d{2}(-\d{2})?)?")


class RuleInputError(ValueError):
    """A rule record is malformed in a way the engine cannot safely interpret."""


# kind is one of: met, not_met, missing, co_year, unusable, bad_predicate
Verdict = namedtuple("Verdict", "value kind fact phrase")
# value is TRUE/FALSE/UNKNOWN; met/unknown/unmet are lists of plain-English phrases
Coverage = namedtuple("Coverage", "value met unknown unmet")


# ---------------------------------------------------------------- helpers

def _is_missing(v):
    if v is None:
        return True
    if isinstance(v, str) and not v.strip():
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return False


def _norm(s):
    return " ".join(str(s).split()).casefold()


_PLACE_SUFFIXES = (" city", " town", " village", " borough", " township", " cdp")


def place_keys(s):
    """Comparable forms of an address-side place name. The Census Geocoder names
    places like 'Berkeley city'; rules say 'Berkeley, CA'. The suffix is stripped
    from the ADDRESS side only, and the unstripped form is kept, so 'Jersey City, NJ'
    still matches itself."""
    n = _norm(s)
    keys = {n}
    if "," in n:
        place, st = n.rsplit(",", 1)
        place, st = place.strip(), st.strip()
    else:
        place, st = n, None
    for suf in _PLACE_SUFFIXES:
        if place.endswith(suf) and len(place) > len(suf):
            stripped = place[: -len(suf)].strip()
            keys.add(stripped if st is None else "%s, %s" % (stripped, st))
    return keys


def _as_int(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if math.isfinite(v) and v == int(v):
            return int(v)
        return None
    if isinstance(v, str):
        s = v.strip()
        if re.fullmatch(r"-?\d+", s):
            return int(s)
        if re.fullmatch(r"-?\d+\.0+", s):
            return int(float(s))
    return None


def _cutoff_int(fact, v):
    """Cutoff as an integer. A date-valued year_built cutoff becomes its year."""
    if fact == "year_built" and isinstance(v, str) and _DATE_RE.fullmatch(v.strip()):
        return int(v.strip()[:4])
    return _as_int(v)


def _cut_display(orig, cut):
    if str(orig).strip() != str(cut):
        return "%s (cutoff %s)" % (cut, str(orig).strip())
    return str(cut)


def missing_phrase(fact):
    if fact == "jurisdiction":
        return "legal jurisdiction, which could not be resolved for this address"
    if fact == "state":
        return "state, which is not in the record for this address"
    return "%s, which is not in the assessor record for this address" % FACT_LABELS[fact]


def _bad(fact, why):
    return Verdict(
        UNKNOWN, "bad_predicate", fact,
        "a coverage test the engine could not interpret (%s)" % why)


# ---------------------------------------------------------- predicate eval

def evaluate_with_reason(predicate, address):
    """Return a Verdict(value, kind, fact, phrase) for one predicate."""
    if not isinstance(predicate, dict):
        raise RuleInputError("predicate must be an object, got %r" % (predicate,))
    fact = predicate.get("fact")
    op = predicate.get("op")
    value = predicate.get("value")
    basis = predicate.get("basis")
    if fact not in FACTS:
        return _bad(fact, "fact %r is not one of %s" % (fact, ", ".join(FACTS)))
    if op not in OPS:
        return _bad(fact, "op %r is not one of %s" % (op, ", ".join(OPS)))
    raw = address.get(fact)
    if _is_missing(raw):
        return Verdict(UNKNOWN, "missing", fact, missing_phrase(fact))
    if fact in STRING_FACTS:
        return _eval_string(fact, op, value, raw)
    return _eval_numeric(fact, op, value, basis, raw)


def _eval_string(fact, op, value, raw):
    label = FACT_LABELS[fact]
    if op not in ("eq", "in"):
        return _bad(fact, "op %s is not valid for %s" % (op, label))
    if op == "eq":
        if isinstance(value, (list, dict)) or value is None:
            return _bad(fact, "eq needs a single value for %s" % label)
        cuts = [value]
    else:
        cuts = list(value) if isinstance(value, (list, tuple)) else [value]
    have = str(raw).strip()
    if fact == "jurisdiction":
        keys = place_keys(have)
        matched = any(_norm(c) in keys for c in cuts)
    else:
        matched = _norm(have) in [_norm(c) for c in cuts]
    if op == "eq":
        phrase = ("%s is %s" % (label, have)) if matched else (
            "%s is %s, not %s" % (label, have, str(cuts[0]).strip()))
    else:
        word = _OP_WORDS["in"] if matched else _OP_NEG_WORDS["in"]
        phrase = "%s %s %s %s" % (label, have, word, "[" + ", ".join(str(c) for c in cuts) + "]")
    return Verdict(TRUE if matched else FALSE, "met" if matched else "not_met", fact, phrase)


def _eval_numeric(fact, op, value, basis, raw):
    label = FACT_LABELS[fact]
    have = _as_int(raw)
    if have is None:
        return Verdict(
            UNKNOWN, "unusable", fact,
            "%s, whose value %r in the record is not a usable number" % (label, raw))
    origs = list(value) if (op == "in" and isinstance(value, (list, tuple))) else [value]
    cuts = [_cutoff_int(fact, o) for o in origs]
    if any(c is None for c in cuts):
        return _bad(fact, "cutoff %r is not a number or date" % (value,))
    if op != "in" and len(cuts) != 1:
        return _bad(fact, "op %s needs a single cutoff" % op)
    # CONTRACT section 4: the assessor year is not the certificate-of-occupancy
    # date, so the cutoff year itself can never be resolved from our data.
    if basis == CO_BASIS and fact == "year_built" and have in cuts:
        shown = ", ".join(str(o).strip() for o in origs)
        return Verdict(
            UNKNOWN, "co_year", fact,
            "year built, which is %d in the assessor record, the same year as the %s "
            "certificate-of-occupancy cutoff, and the assessor year is not the "
            "certificate-of-occupancy date" % (have, shown))
    if op == "in":
        matched = have in cuts
        disp = "[" + ", ".join(_cut_display(o, c) for o, c in zip(origs, cuts)) + "]"
    else:
        matched = _COMPARE[op](have, cuts[0])
        disp = _cut_display(origs[0], cuts[0])
    word = _OP_WORDS[op] if matched else _OP_NEG_WORDS[op]
    phrase = "%s %d %s %s" % (label, have, word, disp)
    return Verdict(TRUE if matched else FALSE, "met" if matched else "not_met", fact, phrase)


def evaluate(predicate, address):
    """CONTRACT section 4: TRUE, FALSE or UNKNOWN for one predicate."""
    return evaluate_with_reason(predicate, address).value


def combine(values):
    """AND across predicates: any false -> false; else any unknown -> unknown; else true."""
    values = list(values)
    if FALSE in values:
        return FALSE
    if UNKNOWN in values:
        return UNKNOWN
    return TRUE


# ----------------------------------------------------------- rule coverage

def scope_predicates(rule):
    """The rule's own jurisdiction as predicates, so a state rule never covers
    another state and a city rule never covers another city."""
    j = rule.get("jurisdiction")
    level = rule.get("level")
    if not isinstance(j, str) or not j.strip():
        raise RuleInputError("rule %s has no jurisdiction" % rule.get("team_rule_id"))
    j = j.strip()
    if level == "state":
        return [{"fact": "state", "op": "eq", "value": j}]
    if level == "city":
        if "," not in j:
            raise RuleInputError(
                "rule %s: city jurisdiction %r must look like 'City, ST'"
                % (rule.get("team_rule_id"), j))
        st = j.rsplit(",", 1)[1].strip()
        return [
            {"fact": "state", "op": "eq", "value": st},
            {"fact": "jurisdiction", "op": "eq", "value": j},
        ]
    raise RuleInputError("rule %s has level %r, expected state or city" % (rule.get("team_rule_id"), level))


def explicit_predicates(rule):
    cc = rule.get("coverage_conditions")
    if not isinstance(cc, dict):
        return []
    preds = cc.get("predicates") or []
    if not isinstance(preds, list):
        raise RuleInputError("rule %s: predicates must be a list" % rule.get("team_rule_id"))
    return preds


def needs_facts(rule):
    cc = rule.get("coverage_conditions")
    if not isinstance(cc, dict):
        return []
    nf = cc.get("needs_fact") or []
    if isinstance(nf, str):
        nf = [nf]
    return [str(x) for x in nf]


def needs_phrase(needs):
    return "%s, which cannot be resolved from the supplied data" % ", ".join(
        n.replace("_", " ") for n in needs)


def evaluate_rule(rule, address):
    """Three-valued coverage of a rule at an address (CONTRACT sections 3 and 4).

    A non-empty needs_fact turns an otherwise-true coverage into unknown.
    """
    verdicts = [evaluate_with_reason(p, address)
                for p in scope_predicates(rule) + explicit_predicates(rule)]
    value = combine(v.value for v in verdicts)
    met = [v.phrase for v in verdicts if v.value == TRUE]
    unknown = [v.phrase for v in verdicts if v.value == UNKNOWN]
    unmet = [v.phrase for v in verdicts if v.value == FALSE]
    needs = needs_facts(rule)
    if needs:
        if value == TRUE:
            value = UNKNOWN
        if value == UNKNOWN:
            unknown.append(needs_phrase(needs))
    return Coverage(value, met, unknown, unmet)


# ------------------------------------------------------------------- dates

def parse_as_of(as_of):
    """as_of is required everywhere. There is deliberately no default."""
    if as_of is None:
        raise TypeError("as_of is required and has no default")
    if isinstance(as_of, datetime.datetime):
        return as_of.date()
    if isinstance(as_of, datetime.date):
        return as_of
    if isinstance(as_of, str):
        try:
            return datetime.date.fromisoformat(as_of.strip())
        except ValueError:
            pass
    raise ValueError("as_of must be a date or an ISO YYYY-MM-DD string, got %r" % (as_of,))


def parse_effective_date(text):
    """Return (first_day, last_day) for YYYY, YYYY-MM or YYYY-MM-DD, or None."""
    if text is None:
        return None
    s = str(text).strip()
    if not _DATE_RE.fullmatch(s):
        raise RuleInputError("effective_date %r is not YYYY, YYYY-MM or YYYY-MM-DD" % (text,))
    parts = [int(p) for p in s.split("-")]
    try:
        if len(parts) == 1:
            return datetime.date(parts[0], 1, 1), datetime.date(parts[0], 12, 31)
        if len(parts) == 2:
            first = datetime.date(parts[0], parts[1], 1)
            nxt = datetime.date(parts[0] + (parts[1] // 12), parts[1] % 12 + 1, 1)
            return first, nxt - datetime.timedelta(days=1)
        d = datetime.date(*parts)
        return d, d
    except ValueError:
        raise RuleInputError("effective_date %r is not a real calendar date" % (text,))


def effective_state(rule, as_of):
    """Return 'effective', 'not_yet_effective' or 'indeterminate'.

    The result comes from effective_date and as_of, not from the status field,
    because the status was stamped as of 2026-10-01 and the engine runs at any
    as_of. With no effective_date the status field is all we have.
    """
    as_of = parse_as_of(as_of)
    span = parse_effective_date(rule.get("effective_date"))
    if span is None:
        return "not_yet_effective" if rule.get("status") == "not_yet_effective" else "effective"
    first, last = span
    if as_of < first:
        return "not_yet_effective"
    if as_of > last:
        return "effective"
    if first == last:
        return "effective"
    return "indeterminate"
