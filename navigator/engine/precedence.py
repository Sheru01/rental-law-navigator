"""Precedence between rules (CONTRACT.md section 5).

A rule's "overrides" list names other team_rule_ids and its "interaction"
says what the relationship is:

  yields_to        this rule becomes "superseded" where a named rule applies
  stacks           both apply, no supersession
  preempts_pending the named rules get conflict_flag true for human review;
                   they are never silently removed

build_plan resolves rules in dependency order (dominant rules first) and
raises PrecedenceCycleError on a cycle instead of looping forever.
"""
from navigator.engine.coverage import RuleInputError

YIELDS_TO = "yields_to"
STACKS = "stacks"
PREEMPTS_PENDING = "preempts_pending"
INTERACTIONS = (YIELDS_TO, STACKS, PREEMPTS_PENDING)


class PrecedenceCycleError(ValueError):
    """The overrides graph contains a cycle."""


class Plan(object):
    def __init__(self):
        self.rules = {}        # team_rule_id -> rule, in input order
        self.order = []        # team_rule_ids, dominant rules before the rules that yield to them
        self.yields_to = {}    # id -> [dominant ids]
        self.stacks = {}       # id -> set of partner ids (symmetric)
        self.preempts = []     # (preemptor id, preempted id)
        self.warnings = []


def build_plan(rules):
    plan = Plan()
    for r in rules:
        rid = r.get("team_rule_id")
        if not isinstance(rid, str) or not rid:
            raise RuleInputError("a rule record has no team_rule_id")
        if rid in plan.rules:
            raise RuleInputError("duplicate team_rule_id %s" % rid)
        plan.rules[rid] = r

    edges = {rid: [] for rid in plan.rules}   # (target, interaction); dependency graph
    for rid, r in plan.rules.items():
        targets = r.get("overrides") or []
        if not targets:
            continue
        inter = r.get("interaction")
        if inter not in INTERACTIONS:
            plan.warnings.append(
                "%s lists overrides %s but interaction %r is not one of %s; "
                "no precedence applied" % (rid, targets, inter, ", ".join(INTERACTIONS)))
            continue
        for t in targets:
            if t not in plan.rules:
                plan.warnings.append(
                    "%s %s %s, which is not in the rule set; ignored" % (rid, inter, t))
                continue
            if inter == YIELDS_TO:
                plan.yields_to.setdefault(rid, []).append(t)
                edges[rid].append((t, inter))
            elif inter == STACKS:
                plan.stacks.setdefault(rid, set()).add(t)
                plan.stacks.setdefault(t, set()).add(rid)
            else:
                plan.preempts.append((rid, t))
                edges[rid].append((t, inter))

    plan.order = _dependency_order(plan, edges)
    return plan


def _dependency_order(plan, edges):
    """Depth-first postorder: every target is placed before the rule that points at it."""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {rid: WHITE for rid in plan.rules}
    order = []

    def visit(rid, path):
        color[rid] = GREY
        for target, inter in edges[rid]:
            if color[target] == GREY:
                # target is GREY, so it is on the current path
                cyc = path[path.index(target):] + [target]
                chain = _label_chain(cyc, edges)
                raise PrecedenceCycleError(
                    "cycle in rule overrides, refusing to resolve: " + chain)
            if color[target] == WHITE:
                visit(target, path + [target])
        color[rid] = BLACK
        order.append(rid)

    for rid in plan.rules:
        if color[rid] == WHITE:
            visit(rid, [rid])
    return order


def _label_chain(ids, edges):
    out = [ids[0]]
    for a, b in zip(ids, ids[1:]):
        label = next((i for t, i in edges[a] if t == b), "overrides")
        out.append("-[%s]-> %s" % (label, b))
    return " ".join(out)


def dominants_that_apply(plan, rule_id, results):
    """Dominant rules (named in this rule's yields_to) whose result here is 'applies'.

    results maps team_rule_id -> result string for rules already resolved at
    this address. Rules are resolved in plan.order, so dominants are present.
    """
    return [d for d in plan.yields_to.get(rule_id, []) if results.get(d) == "applies"]


def dominants_unsettled(plan, rule_id, results):
    """Dominant rules whose coverage is 'unknown' here, so supersession is undecided."""
    return [d for d in plan.yields_to.get(rule_id, []) if results.get(d) == "unknown"]


def stack_partners(plan, rule_id):
    return sorted(plan.stacks.get(rule_id, ()))


def preemption_pairs_present(plan, present_ids):
    """(preemptor, preempted) pairs where both rules appear at this address."""
    present = set(present_ids)
    return [(a, b) for a, b in plan.preempts if a in present and b in present]
