"""The decision agent on top of the deterministic planner (owner: Saed). One LLM call per round (read + tactic).

The planner (tools.planner) builds every term sheet; this agent decides what the rules cannot know, for the one
thread the orchestrator hands it, inside bounds code keeps (docs/adr-bounded-agent.md):
  action    send, or accept the standing sheet - accept only where it dominates our own next offer
            (policy.dominates); walking away is the policy's decision
  price     "follow" the planner's concession, "trim" (half of it) where the supplier would likely accept less,
            or "push" (only the minimal step); never more than follow, never after a final offer
  package   non-price terms of one more package (what the supplier asked for in words), priced by the planner at
            no more than the deterministic plan's C, placed right after the lead sheet
  claims    which arguments (indices into the filtered list), at most 3
  question  one plain question about a term, or none
It also returns its read of the supplier (p_more, walk_risk, wants, signals). Every answer goes through `safe()`;
no model, a late model or a broken answer gives `RuleStrategist`: the planner's step and the rules' arguments.

With tool calling on (`converse`) the model may first call the read-only tools in `toolbox.py` for one turn.
"""

import json
import logging
import math
from dataclasses import asdict

from ....cost import total_cost
from ....docs import standard_terms
from ....play import latest, moves_left, standing
from ...llm import ID_RE
from ..config import OrchestratorConfig
from ..contracts import BehaviourRead, CaseContext, Claim, Tactic
from ..tools import message
from ..tools.claims import default_choice
from .llm import call_json, call_json_with_tools, prompt
from .toolbox import StrategistToolbox

log = logging.getLogger("arena")
TIMEOUT_S = 15.0  # the writer takes up to 15 s after this; the move limit is 60 s
TOOLS_TIMEOUT_S = OrchestratorConfig().strategist_timeout_s - 1.0  # tool loop ends before the orchestrator stops
TERMS = {
    "unit_price", "payment_days", "volume_per_year", "contract_months", "effective_date", "lead_time_days",
    "incoterm", "index_clause", "tooling_eur", "ppm_commitment", "chargeback_eur_per_complaint",
}  # fmt: skip
PRICE_DECISIONS = ("follow", "trim", "push")
ATTACHMENT_CHARS = 1500


# ---- shared helpers (pure) ---------------------------------------------------------------------------------------


def our_messages(events: list[dict]) -> int:
    return sum(m.get("from") == "buyer" for m in events)


def the_thread(threads: dict[str, list[dict]]) -> tuple[str, list[dict]]:
    """The supplier thread the orchestrator hands us (each thread is decided on its own)."""
    sid = next(iter(threads))
    return sid, threads[sid]


def cited_refs(events: list[dict]) -> set[str]:
    """Every data ID we already cited to this supplier: each counts once."""
    refs = set()
    for m in events:
        if m.get("from") == "buyer":
            refs.update(m.get("refs") or ())
            refs.update(ID_RE.findall(str(m.get("body") or "")))
    return refs


def usable_claims(ctx: CaseContext, sid: str, used: set[str]) -> list[tuple[int, Claim]]:
    """(index, claim) the supplier has not heard yet, best first."""
    return [(i, c) for i, c in enumerate(ctx.claims.get(sid, ())) if c.favours_buyer and not (set(c.refs) & used)]


def standing_c(ctx: CaseContext, events: list[dict]) -> float:
    _, terms = standing(events)
    if not terms:
        return math.inf
    try:
        return total_cost(ctx.case, terms, standard_terms(events))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return math.inf


def alternatives(ctx: CaseContext, events: list[dict]) -> list[tuple[dict, float]]:
    """The other sheets of its last e-mail as (terms, C), cheapest first (only its first sheet can be accepted)."""
    out = []
    for sheet in (latest(events).get("term_sheets") or [])[1:]:
        terms = sheet.get("terms") or {}
        try:
            out.append((terms, total_cost(ctx.case, terms, standard_terms(events))))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
    return sorted(out, key=lambda tc: tc[1])


# ---- the rule-based decision (fallback) ------------------------------------------------------------------------


class RuleStrategist:
    """The planner's step, the rules' arguments, no question; accepting and walking are left to the policy."""

    def choose(self, ctx: CaseContext, threads: dict[str, list[dict]], reads: dict[str, BehaviourRead]) -> Tactic:
        sid, events = the_thread(threads)
        claims = ctx.claims.get(sid, ())
        used = cited_refs(events)
        fresh = tuple(c for c in claims if not (set(c.refs) & used))
        chosen = default_choice(ctx.case, fresh, events, ctx.documents.get(sid, ""))
        index = {id(c): i for i, c in enumerate(claims)}
        picked = tuple(index[id(fresh[i])] for i in chosen)
        return Tactic(sid, "send", picked, None, "rule: the planner's step and arguments")


# ---- the LLM decision agent --------------------------------------------------------------------------------------


def _clip(x, default: float) -> float:
    try:
        return min(1.0, max(0.0, float(x)))
    except (TypeError, ValueError):
        return default


def _items(x) -> list:
    """A model field that should be a list: anything else (a string, a number, null) counts as empty."""
    return list(x) if isinstance(x, list | tuple) else []


def _thread_view(ctx: CaseContext, sid: str, events: list[dict], read: BehaviourRead, used: set[str]) -> dict:
    dossier = ctx.dossiers.get(sid)
    sheet_id, terms = standing(events)
    c = standing_c(ctx, events)
    theirs = [m for m in events if m.get("from") != "buyer"][-3:]
    ours = [m for m in events if m.get("from") == "buyer"]
    return {
        "supplier_id": sid,
        "dossier": asdict(dossier) if dossier else None,
        "status": latest(events).get("status"),
        "messages_left": moves_left(ctx.case, events),
        "our_messages_sent": len(ours),
        "standing": {"term_sheet_id": sheet_id, "terms": terms, "c": None if math.isinf(c) else round(c)},
        "fair_price": ctx.fair.get(sid),
        "rule_read": asdict(read),
        "concession": asdict(fit) if (fit := ctx.fits.get(sid)) else None,
        "alternatives": [
            {"terms": t, "c": round(c_alt), "vs_standing_c": None if math.isinf(c) else round(c_alt - c)}
            for t, c_alt in alternatives(ctx, events)
        ],
        "supplier_emails": [
            {
                "status": m.get("status"),
                "body": str(m.get("body") or "")[:900],
                "claim_results": m.get("claim_results"),
                "attachments": [
                    {"name": a.get("name"), "text": str(a.get("text") or "")[:ATTACHMENT_CHARS]}
                    for a in (m.get("attachments") or [])[:2]
                ],
            }
            for m in theirs
        ],
        "our_last_offer": [t.get("terms", t) for t in (ours[-1].get("term_sheets") or [])] if ours else None,
        "planner": {
            price: [
                {"terms": t, "c": None if math.isinf(c) else round(c), "below_its_price_pct": _below(t, terms)}
                for t, c in zip(p.term_sheets, p.c_values, strict=True)
            ]
            for price, p in ctx.offers.get(sid, {}).items()
        },  # the deterministic sheets this round: follow, and push where a push is allowed
        "rule_paragraph": message.narrative(ctx.case, ctx.documents.get(sid, ""), events),
        "claims": [{"index": i, "sentence": c.sentence, "kind": c.kind} for i, c in usable_claims(ctx, sid, used)],
    }


def _below(ours: dict, theirs: dict) -> float | None:
    """How far our sheet's price sits under its standing price, in % (small: it may well accept)."""
    a, b = ours.get("unit_price"), (theirs or {}).get("unit_price")
    return round((b - a) / b * 100, 2) if a and b else None


def _case_view(ctx: CaseContext) -> dict:
    case, buyer = ctx.case, ctx.case.get("buyer", {})
    alt = buyer.get("alternative") or {}
    return {
        "title": case.get("title"),
        "briefing": str(case.get("briefing") or "")[:1500],
        "type": case.get("type"),
        "category_goal": buyer.get("category_goal"),
        "negotiable": case.get("negotiable"),
        "term_options": case.get("term_options"),  # the values a package may use
        "fixed_terms": case.get("fixed_terms"),
        "must_haves": buyer.get("must_haves"),
        "suppliers": len(case.get("suppliers") or []),
        "alternative": {"kind": alt.get("kind"), "c": None if ctx.c_alternative is None else round(ctx.c_alternative)},
        "round_limit": case.get("round_limit", 10),
    }


def refined_read(sid: str, raw: dict | None, rule: BehaviourRead, events: list[dict]) -> BehaviourRead:
    """The model's read, bounded: probabilities in [0, 1], wants only known terms, signals only real quotes."""
    if not isinstance(raw, dict):
        return rule
    text = "\n".join(str(m.get("body") or "") for m in events if m.get("from") != "buyer")
    wants = tuple(dict.fromkeys(w for w in _items(raw.get("wants")) if isinstance(w, str) and w in TERMS))
    signals = tuple(s for s in _items(raw.get("signals")) if isinstance(s, str) and s and s in text)[:3]
    return BehaviourRead(
        sid, _clip(raw.get("p_more"), rule.p_more), _clip(raw.get("walk_risk"), rule.walk_risk),
        wants or rule.wants, signals or rule.signals,
    )  # fmt: skip


def safe(ctx: CaseContext, threads: dict[str, list[dict]], raw: dict, rule: Tactic) -> Tactic:
    """The model's tactic for the one supplier, within our policy; what it gets wrong falls back to the rule's. The
    arguments and the wording stay the rules': a change there cannot be bounded by C, and in practice it cost
    points every time (fewer arguments lost P08 and P11, another mix lost P08) - the agent's levers are the ones
    code bounds: the price (follow / trim / push), its own package, an accept that dominates our next offer."""
    sid, events = the_thread(threads)
    price = raw.get("price") if raw.get("price") in PRICE_DECISIONS else "follow"
    if latest(events).get("status") == "final_offer":
        price = "follow"
    action = raw.get("action")
    if action not in ("send", "accept") or (action == "accept" and our_messages(events) == 0):
        action = "send"  # never the opening quote; walking away is the policy's decision
    rationale = str(raw.get("rationale") or "")[:300]
    package = raw.get("package") if isinstance(raw.get("package"), dict) and action == "send" else None
    return Tactic(sid, action, rule.claims, None, rationale, price, package)


class LLMStrategist:
    def __init__(self, complete=None, fallback: RuleStrategist | None = None, timeout_s: float | None = None,
                 converse=None, tools=None):  # fmt: skip
        """`converse` (roles.llm.converse_from_env) turns on tool calling; `tools` is the orchestrator's DataTools,
        whose planner, supplier estimate and data package the toolbox uses. Without `converse`: one call."""
        self.complete, self.converse, self.tools = complete, converse, tools
        self.timeout_s = timeout_s or (TOOLS_TIMEOUT_S if converse is not None else TIMEOUT_S)
        self.fallback = fallback or RuleStrategist()
        self.system = prompt("strategist").replace("{analyst}", prompt("analyst").strip())
        if converse is not None:
            self.system += "\n\n" + prompt("strategist_tools").strip()

    def choose(self, ctx: CaseContext, threads: dict[str, list[dict]], reads: dict[str, BehaviourRead]) -> Tactic:
        rule = self.fallback.choose(ctx, threads, reads)
        if self.complete is None and self.converse is None:
            return rule
        sid, events = the_thread(threads)
        read = reads.get(sid) or BehaviourRead(sid, 0.5, 0.2, (), ())
        user = json.dumps(
            {"case": _case_view(ctx), "thread": _thread_view(ctx, sid, events, read, cited_refs(events)),
             "rule_suggestion": asdict(rule)},
            ensure_ascii=False, default=str,
        )  # fmt: skip
        toolbox = None
        if self.converse is not None:
            toolbox = StrategistToolbox(ctx, threads, getattr(self.tools, "package", None))
            reply = call_json_with_tools(self.converse, self.system, user, toolbox, self.timeout_s, name="strategist")
        else:
            reply = call_json(self.complete, self.system, user, self.timeout_s, name="strategist")
        if toolbox is not None and toolbox.calls:
            log.info("strategist called tools: %s", ", ".join(toolbox.calls))
        if not isinstance(reply, dict) or not isinstance(reply.get("tactic"), dict):
            return rule
        try:
            refined = refined_read(sid, reply.get("read"), read, events)
            tactic = safe(ctx, threads, reply["tactic"], rule)
        except Exception as e:  # a malformed answer must never cost the round
            log.warning("strategist answer unusable (%r) - using the rule", e)
            return rule
        reads[sid] = refined
        log.info("%s: decision %s price=%s claims=%s package=%s (%s)", sid, tactic.action, tactic.price,
                 tactic.claims, tactic.package, tactic.rationale)  # fmt: skip
        return tactic


__all__ = ["LLMStrategist", "RuleStrategist", "safe"]
