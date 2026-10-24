"""Strategist: this round's decision (owner: Saed). S2 + S3 in one LLM call.

`LLMStrategist.choose` sends every open thread (dossier, band, standing C, fit, the supplier's own words,
claim results, unused claims) to the model once and gets back both the behaviour read per thread (S3) and the
tactic (S2) - one call instead of two saves ~10 s of the 60 s per move. The refined reads are written back
into the `reads` dict the orchestrator passes in, so `tools.plan_offers` uses them. Every answer goes through
`safe()`: a closed thread, an unknown claim, a first-round accept, a walk without a cheaper alternative or a
question with figures never reaches the supplier. No model, a late model or a broken answer gives
`RuleStrategist`, which follows the same policy without an LLM.

With tool calling on (`converse`, Anthropic only, BUYER_LLM_TOOLS=1 by default) the model may first call the
read-only tools in `toolbox.py` - price a package, preview the offer engine, read the whole thread, look up a
data row - for one turn, then answers with the same JSON. One deadline (TOOLS_TIMEOUT_S) covers both calls.
"""

import json
import logging
import math
from dataclasses import asdict

from ....cost import total_cost
from ....docs import standard_terms
from ....play import latest, moves_left, standing
from ...llm import ID_RE
from ..contracts import BehaviourRead, CaseContext, Claim, Tactic
from .analyst import wants_from
from .llm import call_json, call_json_with_tools, prompt
from .toolbox import StrategistToolbox

log = logging.getLogger("arena")
TIMEOUT_S = 15.0  # the writer takes up to 15 s after this; the move limit is 60 s
TOOLS_TIMEOUT_S = 22.0  # with tool calling: one tool turn is a second model call (config.strategist_timeout_s)
TERMS = {
    "unit_price", "payment_days", "volume_per_year", "contract_months", "effective_date", "lead_time_days",
    "incoterm", "index_clause", "tooling_eur", "ppm_commitment", "chargeback_eur_per_complaint",
}  # fmt: skip
COST_BREAKDOWN = "Could you send us a cost breakdown for this part?"
ATTACHMENT_CHARS = 1500
MOVING_P_ROOM = 0.35  # p_room at or above this: another round likely gets >= 1 % more
MOVING_STEP_PCT = 0.3  # without p_room: a last step this big (and not shrinking) means it still moves
LAST_MESSAGES = 1  # with this many messages left, take any sheet that beats the alternative
MAX_TRADES = 6  # the biggest savings first (tools.levers sorts them); enough to pick 1-2 levers from


# ---- shared helpers (pure) ---------------------------------------------------------------------


def standing_c(ctx: CaseContext, events: list[dict]) -> float:
    """C of the supplier's standing sheet with its standard terms; inf if it has none or cannot be priced."""
    _, terms = standing(events)
    if not terms:
        return math.inf
    try:
        return total_cost(ctx.case, terms, standard_terms(events))
    except (KeyError, TypeError, ValueError):
        return math.inf


def our_messages(events: list[dict]) -> int:
    return sum(m.get("from") == "buyer" for m in events)


def cited_refs(threads: dict[str, list[dict]]) -> set[str]:
    """Every data ID we already cited in this case: each counts once, a second citation is `duplicate`."""
    refs = set()
    for events in threads.values():
        for m in events:
            if m.get("from") == "buyer":
                refs.update(m.get("refs") or ())
                refs.update(ID_RE.findall(str(m.get("body") or "")))
    return refs


def usable_claims(ctx: CaseContext, sid: str, used: set[str]) -> list[tuple[int, Claim]]:
    """(index, claim) the supplier has not heard yet and that help us, strongest first."""
    out = [
        (i, c)
        for i, c in enumerate(ctx.claims.get(sid, ()))
        if c.favours_buyer
        and not (set(c.refs) & used)
        and not any(r.startswith("offer:") and r in used for r in c.refs)
    ]
    return sorted(out, key=lambda ic: -ic[1].strength)


def negotiable_levers(ctx: CaseContext, levers) -> tuple[str, ...]:
    """Only terms the case lets us negotiate. An index clause is judged by C like any other term (the tools price
    the expected index move): banning it on rising paths cut P18's 24-month package, the one covering the need."""
    allowed = set(ctx.case.get("negotiable") or ()) | {"unit_price"}
    return tuple(dict.fromkeys(lever for lever in levers if lever in allowed and lever in TERMS))


def accept_ready(ctx: CaseContext, sid: str, events: list[dict]) -> bool:
    """The deterministic accept policy, used by the rule and as a floor under the model."""
    status = latest(events).get("status")
    c = standing_c(ctx, events)
    band = ctx.bands.get(sid)
    alt = ctx.c_alternative if ctx.c_alternative is not None else math.inf
    if status == "agreed" or moves_left(ctx.case, events) <= 0:
        return True
    if our_messages(events) == 0:
        return False  # never take the opening quote
    if status == "final_offer":
        return c < alt
    if moves_left(ctx.case, events) <= LAST_MESSAGES:
        return c < alt
    # At the target is not enough: the practice sweep closed P01 after one round at 0.65 while the supplier was
    # still offering alternatives. Accept at the target only once its price path says it has stopped moving.
    return band is not None and c <= band.c_target and not still_moving(ctx, sid)


def still_moving(ctx: CaseContext, sid: str) -> bool:
    """Its own price path says another round is likely to pay: room left (`p_room`) or real steps still coming.
    No fit yet (first rounds) counts as moving."""
    fit = ctx.fits.get(sid)
    if fit is None:
        return True
    if fit.p_room is not None:
        return fit.p_room >= MOVING_P_ROOM
    if fit.last_step_pct is None:
        return True
    return fit.last_step_pct >= MOVING_STEP_PCT and not fit.shrinking


def walk_allowed(ctx: CaseContext, threads: dict[str, list[dict]]) -> bool:
    """Only when the alternative is cheaper than everything on the table and nobody is moving any more."""
    if ctx.c_alternative is None:
        return False
    cheaper = all(standing_c(ctx, ev) > ctx.c_alternative for ev in threads.values())
    stuck = all(latest(ev).get("status") == "final_offer" or moves_left(ctx.case, ev) <= 1 for ev in threads.values())
    return cheaper and stuck


# ---- the rule-based strategist (fallback) ------------------------------------------------------


class RuleStrategist:
    """Talk to the supplier whose standing sheet costs least; trade price against what it values; accept by
    `accept_ready`. The orchestrator's guard still refuses accepts that break a must-have or lose to the
    alternative."""

    def choose(self, ctx: CaseContext, threads: dict[str, list[dict]], reads: dict[str, BehaviourRead]) -> Tactic:
        sid = min(threads, key=lambda s: standing_c(ctx, threads[s]))
        events = threads[sid]
        if accept_ready(ctx, sid, events):
            status, left = latest(events).get("status"), moves_left(ctx.case, events)
            return Tactic(sid, "accept", rationale=f"rule: accept ({status}, {left} messages left)")
        if walk_allowed(ctx, threads):
            return Tactic(sid, "walk_away", rationale="rule: the alternative beats every standing offer")
        first = our_messages(events) == 0
        read = reads.get(sid)
        dossier = ctx.dossiers.get(sid)
        valued = (*(read.wants if read else ()), *(dossier.levers if dossier else ()))
        levers = negotiable_levers(ctx, ("unit_price", *valued))
        if len(levers) < 2:  # probe at least one non-price term
            levers = negotiable_levers(ctx, (*levers, "payment_days", "contract_months", "effective_date"))
        levers = levers[:3]
        claims = tuple(i for i, _ in usable_claims(ctx, sid, cited_refs(threads))[: 3 if first else 2])
        question = COST_BREAKDOWN if first else None
        return Tactic(sid, "send", levers, claims, question, "rule: trade price against what it values")


# ---- the LLM strategist (S2 + S3) --------------------------------------------------------------


def _clip(x, default: float) -> float:
    try:
        return min(1.0, max(0.0, float(x)))
    except (TypeError, ValueError):
        return default


def _thread_view(ctx: CaseContext, sid: str, events: list[dict], read: BehaviourRead, used: set[str]) -> dict:
    dossier, band = ctx.dossiers.get(sid), ctx.bands.get(sid)
    sheet_id, terms = standing(events)
    c = standing_c(ctx, events)
    theirs = [m for m in events if m.get("from") != "buyer"][-3:]
    ours = [m for m in events if m.get("from") == "buyer"]
    return {
        "supplier_id": sid,
        "dossier": asdict(dossier) if dossier else None,
        "band": asdict(band) if band else None,
        "status": latest(events).get("status"),
        "messages_left": moves_left(ctx.case, events),
        "our_messages_sent": len(ours),
        "standing": {"term_sheet_id": sheet_id, "terms": terms, "c": None if math.isinf(c) else round(c)},
        "rule_read": asdict(read),
        "concession": asdict(fit) if (fit := ctx.fits.get(sid)) else None,
        "trades": [asdict(t) for t in ctx.trades.get(sid, ())[:MAX_TRADES]],
        "supplier_emails": [
            {
                "status": m.get("status"),
                "body": str(m.get("body") or "")[:900],
                "claim_results": m.get("claim_results"),
                "missing_fields": m.get("missing_fields"),
                "signals": m.get("signals"),
                # a cost breakdown, 8D report or delivery schedule is the best evidence of its real position
                "attachments": [
                    {"name": a.get("name"), "text": str(a.get("text") or "")[:ATTACHMENT_CHARS]}
                    for a in (m.get("attachments") or [])[:2]
                ],
            }
            for m in theirs
        ],
        "our_last_offer": [t.get("terms", t) for t in (ours[-1].get("term_sheets") or [])] if ours else None,
        "claims": [
            {"index": i, "sentence": c.sentence, "kind": c.kind, "strength": c.strength}
            for i, c in usable_claims(ctx, sid, used)
        ],
    }


def _case_view(ctx: CaseContext) -> dict:
    case, buyer = ctx.case, ctx.case.get("buyer", {})
    alt = buyer.get("alternative") or {}
    return {
        "title": case.get("title"),
        "briefing": str(case.get("briefing") or "")[:1500],
        "type": case.get("type"),
        "category_goal": buyer.get("category_goal"),
        "negotiable": case.get("negotiable"),
        "term_options": case.get("term_options"),
        "fixed_terms": case.get("fixed_terms"),
        "must_haves": buyer.get("must_haves"),
        "need_per_year": buyer.get("need_per_year"),
        "horizon_years": buyer.get("horizon_years"),
        "alternative": {"kind": alt.get("kind"), "c": None if ctx.c_alternative is None else round(ctx.c_alternative)},
        "index_paths": buyer.get("index_paths"),  # expected index moves over the horizon: + rises, - falls
        "index_risk_pct": buyer.get("index_risk_pct"),
        "round_limit": case.get("round_limit", 10),
    }


def _items(x) -> list:
    """A model field that should be a list: anything else (a string, a number, null) counts as empty."""
    return list(x) if isinstance(x, list | tuple) else []


def refined_read(sid: str, raw: dict | None, rule: BehaviourRead, events: list[dict]) -> BehaviourRead:
    """The model's read, bounded: probabilities in [0, 1], wants only known terms, signals only real quotes."""
    if not isinstance(raw, dict):
        return rule
    text = "\n".join(str(m.get("body") or "") for m in events if m.get("from") != "buyer")
    wants = tuple(dict.fromkeys(w for w in _items(raw.get("wants")) if isinstance(w, str) and w in TERMS))
    wants = wants or rule.wants
    signals = tuple(s for s in _items(raw.get("signals")) if isinstance(s, str) and s and s in text)[:3]
    return BehaviourRead(
        sid, _clip(raw.get("p_more"), rule.p_more), _clip(raw.get("walk_risk"), rule.walk_risk), wants,
        signals or rule.signals,
    )  # fmt: skip


def safe(ctx: CaseContext, threads: dict[str, list[dict]], raw: dict, rule: Tactic) -> Tactic:
    """The model's tactic within our policy. Anything it gets wrong is replaced by the rule's choice."""
    sid = raw.get("supplier_id")
    if not isinstance(sid, str) or sid not in threads:
        return rule
    events = threads[sid]
    action = raw.get("action")
    used = cited_refs(threads)
    usable = {i for i, _ in usable_claims(ctx, sid, used)}
    picked = (i for i in _items(raw.get("claims")) if isinstance(i, int) and not isinstance(i, bool) and i in usable)
    claims = tuple(dict.fromkeys(picked))[:3]
    levers = [lever for lever in _items(raw.get("levers")) if isinstance(lever, str)]
    levers = negotiable_levers(ctx, levers) or (rule.levers if rule.supplier_id == sid else ())
    question = raw.get("question")
    if not isinstance(question, str) or not question.strip() or any(ch.isdigit() for ch in question):
        question = None
    question = question.strip()[:300] if question else None
    rationale = str(raw.get("rationale") or "")[:300]

    if action == "accept":
        if our_messages(events) == 0 and latest(events).get("status") != "agreed":
            action, rationale = "send", f"safe: no accept before our first message ({rationale})"
        elif not accept_ready(ctx, sid, events) and latest(events).get("status") == "open" and still_moving(ctx, sid):
            action, rationale = "send", f"safe: it is still moving, keep trading ({rationale})"
    elif action == "walk_away":
        if not walk_allowed(ctx, threads):
            action, rationale = "send", f"safe: walk refused, no cheaper alternative ({rationale})"
    elif action != "send":
        return rule
    if action == "send" and moves_left(ctx.case, events) <= 0:
        return Tactic(sid, "accept", rationale="safe: no messages left - accept or the guard walks")
    if action == "send" and accept_ready(ctx, sid, events) and latest(events).get("status") == "agreed":
        return Tactic(sid, "accept", rationale="safe: supplier agreed to our terms")
    if action == "send" and our_messages(events) == 0 and not question:
        question = COST_BREAKDOWN
    if action != "send":
        return Tactic(sid, action, rationale=rationale)
    return Tactic(sid, "send", levers or ("unit_price",), claims, question, rationale)


class LLMStrategist:
    def __init__(
        self,
        complete=None,
        fallback: RuleStrategist | None = None,
        timeout_s: float | None = None,
        converse=None,
        tools=None,
    ):
        """`converse` (roles.llm.converse_from_env) turns on tool calling; `tools` is the orchestrator's DataTools,
        whose offer engine, supplier estimate and data package the toolbox uses. Without `converse`: one call."""
        self.complete, self.converse, self.tools = complete, converse, tools
        self.timeout_s = timeout_s or (TOOLS_TIMEOUT_S if converse is not None else TIMEOUT_S)
        self.fallback = fallback or RuleStrategist()
        # S2+S3 in one call: the analyst's reading guide (prompts/analyst.md) sits inside the strategist prompt
        self.system = prompt("strategist").replace("{analyst}", prompt("analyst").strip())
        if converse is not None:
            self.system += "\n\n" + prompt("strategist_tools").strip()

    def _toolbox(self, ctx: CaseContext, threads: dict[str, list[dict]], reads) -> StrategistToolbox:
        t = self.tools
        return StrategistToolbox(
            ctx, threads, reads, plan_offers=getattr(t, "plan_offers", None),
            supplier_of=getattr(t, "supplier_from_thread", None), package=getattr(t, "package", None),
        )  # fmt: skip

    def choose(self, ctx: CaseContext, threads: dict[str, list[dict]], reads: dict[str, BehaviourRead]) -> Tactic:
        rule = self.fallback.choose(ctx, threads, reads)
        if self.complete is None and self.converse is None:
            return rule
        used = cited_refs(threads)
        user = json.dumps(
            {
                "case": _case_view(ctx),
                "threads": [
                    _thread_view(ctx, sid, ev, reads[sid], used) for sid, ev in threads.items() if sid in reads
                ],
                "rule_suggestion": asdict(rule),
            },
            ensure_ascii=False,
            default=str,
        )
        toolbox = None
        if self.converse is not None:
            toolbox = self._toolbox(ctx, threads, reads)
            reply = call_json_with_tools(self.converse, self.system, user, toolbox, self.timeout_s)
        else:
            reply = call_json(self.complete, self.system, user, self.timeout_s)
        if toolbox is not None and toolbox.calls:
            log.info("strategist called tools: %s", ", ".join(toolbox.calls))
        if not isinstance(reply, dict) or not isinstance(reply.get("tactic"), dict):
            return rule
        try:
            model_reads = reply.get("reads") if isinstance(reply.get("reads"), dict) else {}
            refined = {
                sid: refined_read(sid, raw, reads[sid], threads[sid])
                for sid, raw in model_reads.items()
                if sid in reads
            }
            tactic = safe(ctx, threads, reply["tactic"], rule)
        except Exception as e:  # a malformed answer must never cost the round
            log.warning("strategist answer unusable (%r) - using the rule", e)
            return rule
        reads.update(refined)  # the orchestrator passes this dict on to plan_offers
        log.info("%s: strategist %s %s claims=%s (%s)", tactic.supplier_id, tactic.action, tactic.levers,
                 tactic.claims, tactic.rationale)  # fmt: skip
        return tactic


__all__ = ["LLMStrategist", "RuleStrategist", "accept_ready", "safe", "wants_from"]
