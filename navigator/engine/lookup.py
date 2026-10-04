"""Resolve which rules apply at an address on an as-of date.

resolve(address, rules, as_of) -> list of {team_rule_id, result, explanation, conflict_flag}

Pure code, no model calls. as_of is required; nothing here reads the clock.
"""
from navigator.engine import coverage, precedence, withheld as withheld_engine
from navigator.engine.coverage import FALSE, TRUE, UNKNOWN, RuleInputError

RESULTS = ("applies", "unknown", "superseded", "not_yet_effective", "pending")
ENTRY_KEYS = ("team_rule_id", "result", "explanation", "conflict_flag")
WITHHELD_KEYS = ENTRY_KEYS + ("evidence_status", "reason_code", "needs_fact",
                              "temporal_result", "date_derivation", "result_state",
                              "reason", "as_of", "source_doc_id", "source_url",
                              "citation", "evidence_basis")
ADDRESS_FACTS = ("state", "jurisdiction", "year_built", "units")

# Named guard. M.G.L. c.40P s.4 bars local rent control in Massachusetts
# (CONTRACT.md section 1, D048). This is the in-force basis for never
# reporting a rent cap for an MA address, independent of any rule record.
MA_RENT_CONTROL_GUARD = (
    "MA rent-control guard: M.G.L. c.40P s.4 bars any city or town from enacting, "
    "maintaining or enforcing rent control, so no rent_increase_limits rule is "
    "reported for an address in MA")


class InvariantError(RuntimeError):
    """A hard invariant of the engine was violated. This is a bug, never a data issue."""


# ---------------------------------------------------------------- addresses

def normalize_address(address):
    """Blank strings become None, state is upper-cased. If state is absent but the
    jurisdiction ends in ', ST', the state is taken from it."""
    out = {"address_id": address.get("address_id")}
    for f in ADDRESS_FACTS:
        v = address.get(f)
        if isinstance(v, str):
            v = v.strip() or None
        out[f] = v
    if isinstance(out["state"], str):
        out["state"] = out["state"].upper()
    if out["state"] is None and isinstance(out["jurisdiction"], str) and "," in out["jurisdiction"]:
        out["state"] = out["jurisdiction"].rsplit(",", 1)[1].strip().upper() or None
    return out


# -------------------------------------------------------------------- guard

def _rule_state(rule):
    """State code a rule belongs to: 'MA' or the ST of 'City, ST'. None if unreadable."""
    j = rule.get("jurisdiction")
    if not isinstance(j, str) or not j.strip():
        return None
    return j.rsplit(",", 1)[-1].strip().upper() or None


def guard_ma_rent_control(rule, address):
    """Return the named reason if the MA rent-control bar forbids reporting this rule here.

    Fires on EITHER side: the address is in MA, or the rule itself is an MA rule.
    The second case matters when an address has no resolved state: an MA rent cap
    must still never surface as 'unknown' for it.
    """
    if rule.get("category") != "rent_increase_limits":
        return None
    state = address.get("state")
    addr_ma = isinstance(state, str) and state.strip().upper() == "MA"
    if addr_ma or _rule_state(rule) == "MA":
        return MA_RENT_CONTROL_GUARD
    return None


# ------------------------------------------------------------- explanations

def _join(parts):
    return "; ".join(parts)


def _depends_on(unknown):
    return "; and ".join(unknown)


def _checked(met):
    return (" Checked: %s." % _join(met)) if met else ""


def _cov_clause(cov, lead_true, lead_unknown):
    if cov.value == TRUE:
        return " %s the address facts show coverage: %s." % (lead_true, _join(cov.met))
    return " %s it depends on %s.%s" % (lead_unknown, _depends_on(cov.unknown), _checked(cov.met))


class _Outcome(object):
    def __init__(self, result=None, explanation="", omit=None, why_unknown=None, cov=None):
        self.cov = cov
        self.result = result
        self.explanation = explanation
        self.omit = omit
        self.why_unknown = why_unknown


def _evaluate_one(rule, addr, as_of):
    rid = rule["team_rule_id"]
    title = rule.get("title") or rid
    status = rule.get("status")
    if status not in coverage.STATUSES:
        raise RuleInputError("rule %s has status %r, expected one of %s"
                             % (rid, status, ", ".join(coverage.STATUSES)))
    if status == "failed":
        return _Outcome(omit="status failed: struck, defeated or withdrawn measures are never reported")
    barred = guard_ma_rent_control(rule, addr)
    if barred:
        return _Outcome(omit=barred)
    cov = coverage.evaluate_rule(rule, addr)
    if cov.value == FALSE:
        return _Outcome(omit="coverage false: " + _join(cov.unmet))

    if status == "pending":
        text = ("Pending: %s is a bill or proposal, not law, so it is not in force." % title)
        text += _cov_clause(cov, "If enacted as written,",
                            "Whether it would cover this address is unknown:")
        return _Outcome("pending", text)

    if status == "in_force" and not rule.get("effective_date") and \
            rule.get("status_basis") in ("derived", "inferred", "unknown"):
        text = ("Unknown: %s has no source-established effective date and its in-force "
                "status is only %s; operation on %s is not established." % (
                    title, rule["status_basis"], as_of.isoformat()))
        text += _cov_clause(cov, "Address coverage is otherwise supported:",
                            "Address coverage also depends on:")
        return _Outcome("unknown", text, why_unknown="source-established effective date")

    eff = coverage.effective_state(rule, as_of)
    when = rule.get("effective_date")
    if eff == "not_yet_effective":
        if when:
            text = ("Not yet effective: %s takes effect %s, after the as-of date %s."
                    % (title, when, as_of.isoformat()))
        else:
            text = ("Not yet effective: %s is recorded as enacted but not yet effective, "
                    "and no effective date is recorded." % title)
        text += _cov_clause(cov, "Once effective,",
                            "Whether it would cover this address is unknown:")
        return _Outcome("not_yet_effective", text)
    if eff == "indeterminate":
        text = ("Unknown: %s has an effective date recorded only as %s, and the as-of date %s "
                "falls inside that period, so it cannot be said whether the rule is in effect."
                % (title, when, as_of.isoformat()))
        text += _cov_clause(cov, "On current facts,", "Coverage also depends on:")
        return _Outcome("unknown", text, why_unknown="effective date %s is only a month or year" % when)

    if cov.value == UNKNOWN:
        text = "Unknown: coverage of %s depends on %s.%s" % (
            title, _depends_on(cov.unknown), _checked(cov.met))
        return _Outcome("unknown", text, why_unknown=_depends_on(cov.unknown))

    text = "Applies: %s covers this address (%s)." % (title, _join(cov.met))
    if rule.get("exemptions"):
        ex = str(rule["exemptions"]).strip()
        if len(ex) > 240:
            ex = ex[:237] + "..."
        text += " The source lists exemptions this engine does not test: %s." % ex.rstrip(".")
    if not when and status == "in_force":
        text += " No effective date is recorded, so the as-of date was not checked against one."
    return _Outcome("applies", text, cov=cov)


# ------------------------------------------------------------------ resolve

def resolve(address, rules, as_of, plan=None, withheld=()):
    """The lookup entries for one address. as_of is required."""
    return resolve_with_trace(address, rules, as_of, plan, withheld)[0]


def detail(address, rule, entry, as_of):
    """Structured evidence view of an emitted entry, without changing its contract."""
    date = coverage.parse_as_of(as_of).isoformat()
    state = entry["result"]
    legal_gap = (entry.get("evidence_status") == "not_established" or
                 (state == "unknown" and
                  ("source-established effective date" in entry["explanation"] or
                   "effective date recorded only as" in entry["explanation"])))
    if legal_gap:
        state = "indeterminate"
    needs = (list(entry.get("needs_fact") or []) or
             (withheld_engine.needed_facts(rule, normalize_address(address))
              if entry["result"] in ("unknown", "pending", "not_yet_effective") else []))
    return {
        "rule_id": rule["team_rule_id"],
        "result_state": state,
        "reason": entry.get("reason") or entry["explanation"],
        "needs_fact": needs,
        "needs_evidence": ["operative_status_or_date"] if legal_gap else [],
        "prior_state": None,
        "current_state": entry["result"],
        "as_of": date,
        "source_doc_id": rule.get("source_doc_id"),
        "source_url": rule.get("source_url"),
        "citation": rule.get("citation"),
        "evidence_basis": entry.get("evidence_basis") or {
            "status_basis": rule.get("status_basis"),
            "provenance_grade": rule.get("provenance_grade"),
            "source_kind": rule.get("source_kind"),
        },
        "temporal_result": entry.get("temporal_result"),
        "date_derivation": entry.get("date_derivation"),
    }


def resolve_detailed(address, rules, as_of, plan=None, withheld=()):
    """Lookup results with provenance and distinct legal/property uncertainty."""
    established, uncertain = withheld_engine.partition(rules, withheld)
    inventory = {r["team_rule_id"]: r for r in established + uncertain}
    entries = resolve(address, established, as_of, plan, uncertain)
    return [detail(address, inventory[e["team_rule_id"]], e, as_of) for e in entries]


def resolve_with_trace(address, rules, as_of, plan=None, withheld=()):
    """Return (entries, omitted). omitted lists {team_rule_id, reason} for every
    rule left out, so guard decisions and non-coverage are auditable."""
    as_of_d = coverage.parse_as_of(as_of)
    rules, withheld_rules = withheld_engine.partition(rules, withheld)
    if plan is None:
        plan = precedence.build_plan(rules)
    elif set(plan.rules) != {r["team_rule_id"] for r in rules}:
        raise RuleInputError("precedence plan does not match established rules")
    addr = normalize_address(address)

    final = {}      # id -> {"entry": dict, "why": str|None}
    omitted = []
    for rid in plan.order:                       # dominant rules first
        rule = plan.rules[rid]
        out = _evaluate_one(rule, addr, as_of_d)
        if out.omit:
            omitted.append({"team_rule_id": rid, "reason": out.omit})
            continue
        result, text = out.result, out.explanation
        if result == "applies":
            results_so_far = {k: v["entry"]["result"] for k, v in final.items()}
            doms = precedence.dominants_that_apply(plan, rid, results_so_far)
            if doms:
                d = doms[0]
                result = "superseded"
                text = ("Superseded: %s covers this address (%s), but %s (%s) applies here "
                        "and takes precedence." % (
                            rule.get("title") or rid, _join(out.cov.met),
                            plan.rules[d].get("title") or d, d))
            else:
                for d in precedence.dominants_unsettled(plan, rid, results_so_far):
                    text += (" Caveat: it is unknown whether %s (%s) covers this address "
                             "(%s); if it does, this rule is superseded." % (
                                 plan.rules[d].get("title") or d, d, final[d]["why"]))
        elif result == "unknown":
            results_so_far = {k: v["entry"]["result"] for k, v in final.items()}
            doms = precedence.dominants_that_apply(plan, rid, results_so_far)
            if doms:
                d = doms[0]
                text += (" Note: %s (%s) applies here and takes precedence over this rule, "
                         "so if this rule does cover the address it would be superseded."
                         % (plan.rules[d].get("title") or d, d))
        # A preemption concern is address-specific. The post-pass marks it only
        # where the named local rule also appears; a state-level flag must not
        # spill onto unrelated municipalities such as Newark.
        flag = bool(rule.get("conflict_flag")) and rule.get("interaction") != "preempts_pending"
        if flag:
            note = (rule.get("conflict_note") or "").strip() or "no note recorded"
            text += " Conflict flagged for human review: %s" % note
            if not text.endswith("."):
                text += "."
        final[rid] = {
            "entry": {"team_rule_id": rid, "result": result, "explanation": text,
                      "conflict_flag": flag},
            "why": out.why_unknown,
        }

    # Post-pass: stacking notes and preemption conflicts (need every result in hand).
    for rid, rec in final.items():
        e = rec["entry"]
        if e["result"] != "applies":
            continue
        for p in precedence.stack_partners(plan, rid):
            if p in final and final[p]["entry"]["result"] == "applies":
                e["explanation"] += (" Stacks with %s (%s), which also applies here; "
                                     "neither supersedes the other." % (
                                         plan.rules[p].get("title") or p, p))
    for pre, sub in precedence.preemption_pairs_present(plan, final.keys()):
        pre_r, sub_r = plan.rules[pre], plan.rules[sub]
        note = ("Conflict flagged for human review: potential preemption issue between "
                "%s (%s) and %s (%s); whether the existing local rule is affected is not "
                "established. Both records are kept and neither is removed." % (
                    pre_r.get("title") or pre, pre, sub_r.get("title") or sub, sub))
        src = (pre_r.get("conflict_note") or "").strip()
        if src:
            note += " Source note: %s" % src
            if not note.endswith("."):
                note += "."
        for k in (pre, sub):
            final[k]["entry"]["conflict_flag"] = True
            final[k]["entry"]["explanation"] += " " + note

    entries = [final[r["team_rule_id"]]["entry"] for r in rules if r["team_rule_id"] in final]
    for rule in withheld_rules:
        rid = rule["team_rule_id"]
        barred = guard_ma_rent_control(rule, addr)
        if barred:
            omitted.append({"team_rule_id": rid, "reason": barred})
            continue
        entry, reason = withheld_engine.evaluate(rule, addr, as_of_d)
        if reason:
            omitted.append({"team_rule_id": rid, "reason": reason})
        else:
            entries.append(entry)
    check_invariants(addr, plan, entries, {r["team_rule_id"]: r for r in withheld_rules})
    return entries, omitted


def check_invariants(address, plan, entries, withheld_rules=None):
    """Hard invariants. Raise InvariantError rather than ever emit a violating entry."""
    withheld_rules = withheld_rules or {}
    seen = set()
    for e in entries:
        rid = e.get("team_rule_id")
        rule = plan.rules.get(rid) or withheld_rules.get(rid)
        if rule is None:
            raise InvariantError("entry for unknown rule %r" % (rid,))
        is_withheld = rid in withheld_rules
        expected = WITHHELD_KEYS if is_withheld else ENTRY_KEYS
        if tuple(e.keys()) != expected:
            raise InvariantError("entry for %s has keys %s, expected %s" % (rid, list(e), list(expected)))
        if rid in seen:
            raise InvariantError("rule %s appears twice at one address" % rid)
        seen.add(rid)
        if e["result"] not in RESULTS:
            raise InvariantError("rule %s has result %r" % (rid, e["result"]))
        if not isinstance(e["conflict_flag"], bool):
            raise InvariantError("rule %s conflict_flag is not a bool" % rid)
        if not isinstance(e["explanation"], str) or not e["explanation"].strip():
            raise InvariantError("rule %s has an empty explanation" % rid)
        if rule.get("status") == "failed":
            raise InvariantError("failed rule %s appeared in a lookup" % rid)
        if rule.get("status") == "pending" and e["result"] != "pending":
            raise InvariantError("pending rule %s resolved to %r, never allowed" % (rid, e["result"]))
        if is_withheld:
            if e["result"] != "unknown" or e["evidence_status"] != "not_established":
                raise InvariantError("withheld rule %s must remain unknown/not_established" % rid)
            if e["reason_code"] != "projection_withheld" or not isinstance(e["needs_fact"], list):
                raise InvariantError("withheld rule %s lacks a reason or needs_fact list" % rid)
            if e["result_state"] != "indeterminate" or not e["reason"]:
                raise InvariantError("withheld rule %s lacks explicit legal uncertainty" % rid)
            if e["source_doc_id"] != rule.get("source_doc_id") or \
                    e["citation"] != rule.get("citation"):
                raise InvariantError("withheld rule %s lost its source reference" % rid)
        if guard_ma_rent_control(rule, address):
            raise InvariantError(
                "%s: rent_increase_limits rule %s reported as %r for MA address %s"
                % (MA_RENT_CONTROL_GUARD, rid, e["result"], address.get("address_id")))
