"""Effective-date derivation (CONTRACT.md section 6), as a pure function.

WHO CALLS THIS
  The rule engine calls it. The extraction pipeline (navigator/extract) does NOT:
  extraction records what the source says, and the source for a chaptered
  California statute usually states no operative date. Wiring the engine to call
  derive_effective_date() for records whose effective_date is null and whose
  status is withheld is the lead's job. See navigator/extract/RUN_FOR_SOL.md.

WHAT IT IMPLEMENTS
  CONTRACT.md section 6, exactly as written:
    "CA chaptered statutes with no stated operative date default to January 1
     of the year following enactment."

  Nothing else. In particular it does NOT model urgency clauses, special
  sessions, other states, or any 90-day clause. Those cases return
  (None, record) with record["applied"] false and a stated reason, so the caller
  can see that no derivation was made rather than receiving a guess.

PROVENANCE OF THE RULE
  The rule has NO corpus source. The first reader cited Cal. Const. art. IV sec.
  8(c)(1) and Cal. Gov. Code sec. 9600(a); neither is in the corpus and neither
  was verified. The derivation record says so in machine-readable form
  (corpus_support false, legal_basis_cited_unverified) so a downstream consumer
  cannot mistake a derived date for a stated one.

RETURN VALUE
  (date, record)
    date    ISO string YYYY, YYYY-MM or YYYY-MM-DD, or None
    record  machine-readable derivation record naming the rule
"""
import datetime
import re

RULE_STATED = "STATED_DATE_PASSTHROUGH"
RULE_CA_DEFAULT = "CA_CHAPTERED_STATUTE_DEFAULT_JAN_1_NEXT_YEAR"
RULE_NONE = "NO_RULE_APPLIED"

# Rule ids whose application belongs to the engine. The extraction pipeline
# refuses any model-supplied effective_date whose derivation names one of these.
ENGINE_OWNED_RULE_IDS = (RULE_CA_DEFAULT,)

RULE_SOURCE = "navigator/CONTRACT.md section 6"
LEGAL_BASIS_UNVERIFIED = (
    "Cal. Const. art. IV, sec. 8(c)(1)",
    "Cal. Gov. Code sec. 9600(a)",
)

_DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def _parse_iso_date(value, label):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise ValueError("%s must be a date or an ISO YYYY-MM-DD string, got %r" % (label, value))


def _check_stated(value):
    s = str(value).strip()
    if not _DATE_RE.fullmatch(s):
        raise ValueError("stated_effective_date %r is not YYYY, YYYY-MM or YYYY-MM-DD" % (value,))
    parts = [int(p) for p in s.split("-")]
    try:
        datetime.date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except ValueError:
        raise ValueError("stated_effective_date %r is not a real calendar date" % (value,))
    return s


def _record(rule_id, applied, derived, category, inputs, output, reason, extra=None):
    rec = {
        "rule_id": rule_id,
        "applied": applied,
        "derived": derived,
        "category": category,          # stated | derived | unknown
        "inputs": inputs,
        "output": output,
        "reason": reason,
        "rule_source": RULE_SOURCE,
        "corpus_support": False if rule_id == RULE_CA_DEFAULT else None,
    }
    if rule_id == RULE_CA_DEFAULT:
        rec["legal_basis_cited_unverified"] = list(LEGAL_BASIS_UNVERIFIED)
        rec["caveat"] = (
            "Implements CONTRACT.md section 6 as written. The legal provisions named above "
            "are not in the corpus and were not verified; how they treat the 90-day period is "
            "not modelled here. Treat the output as a derived date, never a stated one.")
    if extra:
        rec.update(extra)
    return rec


def derive_effective_date(enacted_on, stated_effective_date=None, jurisdiction="CA",
                          instrument="chaptered_statute", urgency=False, session="regular"):
    """Return (effective_date, derivation_record).

    enacted_on             date or ISO string: the approval/enactment date the source states
    stated_effective_date  a date the source itself states; returned unchanged when present
    jurisdiction           only "CA" has a rule
    instrument             only "chaptered_statute" has a rule
    urgency                True if the act carries an urgency clause: OUT OF SCOPE, returns None
    session                only "regular" has a rule; "special" is OUT OF SCOPE
    """
    inputs = {
        "enacted_on": str(enacted_on) if enacted_on is not None else None,
        "stated_effective_date": stated_effective_date,
        "jurisdiction": jurisdiction,
        "instrument": instrument,
        "urgency": bool(urgency),
        "session": session,
    }

    if stated_effective_date not in (None, ""):
        stated = _check_stated(stated_effective_date)
        return stated, _record(
            RULE_STATED, True, False, "stated", inputs, stated,
            "The source states an operative date; it is returned unchanged and nothing is derived.")

    if enacted_on is None:
        return None, _record(
            RULE_NONE, False, False, "unknown", inputs, None,
            "No enactment date was supplied, so no derivation is possible.")
    enacted = _parse_iso_date(enacted_on, "enacted_on")

    if jurisdiction != "CA":
        return None, _record(
            RULE_NONE, False, False, "unknown", inputs, None,
            "OUT OF SCOPE: no default effective-date rule is encoded for jurisdiction %r." % (jurisdiction,))
    if instrument != "chaptered_statute":
        return None, _record(
            RULE_NONE, False, False, "unknown", inputs, None,
            "OUT OF SCOPE: no default effective-date rule is encoded for instrument %r." % (instrument,))
    if urgency:
        return None, _record(
            RULE_NONE, False, False, "unknown", inputs, None,
            "OUT OF SCOPE: the act carries an urgency clause. CONTRACT.md section 6 does not "
            "cover urgency statutes and this function does not guess an operative date for one.")
    if session != "regular":
        return None, _record(
            RULE_NONE, False, False, "unknown", inputs, None,
            "OUT OF SCOPE: only regular-session statutes are covered, got session %r." % (session,))

    out = datetime.date(enacted.year + 1, 1, 1).isoformat()
    return out, _record(
        RULE_CA_DEFAULT, True, True, "derived", inputs, out,
        "CA chaptered statute with no stated operative date: January 1 of the year following "
        "enactment (%s), per CONTRACT.md section 6." % enacted.isoformat())
