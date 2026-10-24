"""The team of agents: who reads what, with which tools, and what each one must report.

  reader        every round   the supplier's latest e-mail, read as written, against the thread
  deal          every round   the economics: C of its offers, room, trades, counter candidates
  counterpart   first round,  who the supplier is and what moves it (profile, history, documents, news)
                and when new documents arrive
  market        first round,  whether the price is justified (cost drivers, indices, should-cost, history) and
                and when new documents arrive    which verified arguments fit this supplier
  negotiator    every round   decides accept / counter / walk_away from the reports and the decision frame, and
                              writes the e-mail

The specialists run in parallel; the negotiator runs after them with their reports. Every input is plain text:
the case, the facts code computed, and the e-mails exactly as written.
"""

import json
import math
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from . import emails
from . import facts as F
from . import terms as T

PROMPTS = Path(__file__).parent / "prompts"


@cache
def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8").strip()


@cache
def system_prompt(role: str) -> str:
    """House rules + playbook + the role's own instructions: identical for every call of a role (cached)."""
    return "\n\n".join([prompt("house_rules"), prompt("playbook"), prompt(role)])


# ---- output schemas (structured outputs: every object closed, every field required, nulls explicit) ---------


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


STR = {"type": "string"}
NUM = {"type": "number"}
BOOL = {"type": "boolean"}
OPT_NUM = {"anyOf": [{"type": "number"}, {"type": "null"}]}
STRS = {"type": "array", "items": STR}
TERM_ITEMS = {"type": "array", "items": _obj({"field": STR, "value": STR})}
TOKEN_WHY = {"type": "array", "items": _obj({"token": STR, "why": STR})}

SCHEMAS = {
    "reader": _obj({
        "accepted_our_offer": BOOL, "acceptance_evidence": STR, "offer_summary": STR, "movement": STR,
        "wants": STRS, "values_terms": STRS, "refused_terms": STRS, "questions_to_answer": STRS,
        "its_arguments": STRS, "information_offered": STRS, "tone": STR, "relationship_signal": STR,
        "walk_risk": NUM, "room_to_move": NUM, "language": {"type": "string", "enum": ["de", "en"]},
        "red_flags": STRS, "key_quotes": STRS,
    }),
    "counterpart": _obj({
        "situation": STR, "relationship": STR, "negotiation_behaviour": STR,
        "profile": {"type": "string", "enum": ["cash_short", "full_order_book", "price_freeze_or_committee",
                                               "relationship_partner", "volume_hungry", "uninformed_owner",
                                               "near_monopoly", "unknown"]},
        "secondary_traits": STRS, "what_moves_it": STRS, "values_terms": STRS, "avoid_terms": STRS,
        "style_advice": STR, "risks": STRS, "evidence": STRS, "confidence": NUM,
    }),
    "market": _obj({
        "price_view": STR, "justified_change_pct": OPT_NUM, "fair_price": OPT_NUM, "floor_estimate": OPT_NUM,
        "credible_lowest_price": OPT_NUM, "ambitious_target_price": OPT_NUM, "floor_confidence": STR,
        "strongest_arguments": TOKEN_WHY, "arguments_to_avoid": TOKEN_WHY, "volume_trend": STR, "risks": STRS,
        "confidence": NUM,
    }),
    "deal": _obj({
        "standing_c": OPT_NUM, "standing_acceptable": BOOL, "why_not_acceptable": STR, "accepted_our_offer": BOOL,
        "room_assessment": STR, "gap_to_target_pct": OPT_NUM,
        "recommended_action": {"type": "string", "enum": ["accept", "counter", "walk_away"]},
        "action_reasoning": STR,
        "candidates": {"type": "array", "items": _obj({"unit_price": NUM, "terms": TERM_ITEMS, "c_eur": OPT_NUM,
                                                       "gives_supplier": STR, "why": STR})},
        "levers_ranked": {"type": "array", "items": _obj({"field": STR, "saves_eur": OPT_NUM, "why": STR})},
        "walk_away_point": STR, "confidence": NUM,
    }),
    "negotiator": _obj({
        "decision": {"type": "string", "enum": ["accept", "counter", "walk_away"]},
        "reasoning": STR,
        "counter_offers": {"type": "array", "items": _obj({"unit_price": NUM, "terms": TERM_ITEMS, "label": STR})},
        "arguments": STRS, "email_subject": STR, "email_body": STR,
    }),
}  # fmt: skip


@dataclass(frozen=True)
class Spec:
    role: str
    tools: tuple[str, ...]
    static: bool  # runs once per thread (and again when new documents arrive), not every round
    tool_turns: int = 1  # its numbers come precomputed; one tool turn for a follow-up
    preload: tuple[str, ...] = ()  # tools run by code and put into its input before it starts


SPECS = {
    "reader": Spec("reader", ("supplier_offers", "read_attachment", "thread_transcript", "lookup_id"), False,
                   preload=("supplier_offers",)),
    "deal": Spec("deal", ("supplier_offers", "concession_fit", "price_band", "lever_trades", "propose_counter",
                          "price_package", "case_file"), False,
                 preload=("supplier_offers", "concession_fit", "price_band", "lever_trades", "propose_counter")),
    "counterpart": Spec("counterpart", ("supplier_overview", "supplier_documents", "read_document",
                                        "negotiation_history", "payment_and_delivery", "contract_terms", "lookup_id",
                                        "list_ids", "sql_query", "list_tables"), True,
                        preload=("supplier_overview", "supplier_documents", "negotiation_history",
                                 "payment_and_delivery")),
    "market": Spec("market", ("price_band", "cost_drivers", "market_index", "price_history", "should_cost",
                              "part_quotes", "demand_forecast", "verified_arguments", "lookup_id", "read_document",
                              "sql_query", "list_tables"), True,
                   preload=("price_band", "cost_drivers", "should_cost", "verified_arguments")),
}  # fmt: skip
NEGOTIATOR_TOOLS = ("price_package", "propose_counter", "supplier_offers")


# ---- the texts every agent reads ---------------------------------------------------------------------------


def _money(x) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isinf(x)) else f"EUR {x:,.0f}"


def supplier_name(state: F.CaseState, sid: str) -> str:
    d = getattr(state.ctx, "dossiers", {}).get(sid)
    return d.name if d and d.name else sid


def case_brief(state: F.CaseState, sid: str) -> str:
    case, b = state.case, state.case.get("buyer", {})
    alt = b.get("alternative") or {}
    must = "; ".join(f"{m['field']} {m['op']} {m['value']}" for m in b.get("must_haves") or []) or "none"
    lines = [
        f"CASE {case.get('case_id')} ({case.get('type')}): {case.get('title')}",
        f"Briefing: {str(case.get('briefing') or '').strip()}",
        f"This thread: {supplier_name(state, sid)} ({sid}). Suppliers in this case: {len(case.get('suppliers') or [])}"
        " - each is negotiated on its own.",
        f"Parts: {', '.join(case.get('parts') or [])}. The case started with: {case.get('trigger_doc') or 'n/a'}.",
        f"Need: {b.get('need_per_year')} per year for {b.get('horizon_years')} year(s). Current price "
        f"{b.get('current_unit_price')}, fallback price for uncovered need {b.get('fallback_unit_price')} "
        f"(+{b.get('fallback_risk_pct', 0)} % risk). Category goal: {b.get('category_goal')}.",
        f"Our rates: interest {b.get('interest_rate')} per year, reference payment {b.get('reference_payment_days')} "
        f"days, need lead time {b.get('need_lead_time_days')} days, late cost {b.get('late_cost_per_day')} EUR/day, "
        f"cost per defect {b.get('cost_per_defect')} EUR, freight per unit {b.get('freight_eur_per_unit')}.",
        f"Index paths over the horizon: {b.get('index_paths') or 'none'}; index risk {b.get('index_risk_pct', 0)} %.",
    ]
    if b.get("sustainable_margin_pct"):
        lines.append(
            f"Supplier health: sustainable margin {b.get('sustainable_margin_pct')} %, "
            f"{b.get('relationship_eur_per_point')} EUR per point below it per year (counted in C)."
        )
    lines += [
        f"Must-haves: {must}.",
        f"Our alternative: {alt.get('kind') or 'none'}"
        + (f", C = {_money(state.c_alt)}" if state.c_alt is not None else " - no deal scores zero")
        + ".",
        f"Negotiable: {', '.join(state.negotiable)}. Fixed: {case.get('fixed_terms') or {}}. "
        f"Round limit: {case.get('round_limit', 10)} messages per thread.",
    ]
    return "\n".join(lines)


def _sheet_line(s: F.Sheet) -> str:
    return f"{T.describe(s.terms)} -> C {_money(s.c)}"


def thread_state(state: F.CaseState, f: F.ThreadFacts) -> str:
    lines = [
        f"STATE OF THE THREAD (computed by code): status {f.status}; we sent {f.our_rounds} message(s), "
        f"{f.moves_left} left.",
        f"- Its standing sheet {f.standing.id}: {_sheet_line(f.standing)}" if f.standing else "- No standing sheet.",
        f"- May be accepted now: {'yes' if f.acceptable else 'no - ' + f.why_not}.",
        f"- It accepted our offer: {'YES' if f.accepted_ours else 'no'}.",
    ]
    for s in f.alternatives:
        lines.append(f"- Its alternative sheet {s.id} (cannot be accepted by ID): {_sheet_line(s)}")
    for i, s in enumerate(f.our_offer, 1):
        lines.append(f"- Our offer on the table, sheet {i}: {_sheet_line(s)}")
    lines += [
        f"- Its prices so far: {list(f.supplier_prices) or 'none'}; ours: {list(f.our_prices) or 'none'}.",
        f"- Best opening quote C: {_money(state.c_open)}; our alternative C: {_money(state.c_alt)}.",
    ]
    return "\n".join(lines)


def decision_frame(state: F.CaseState, f: F.ThreadFacts) -> str:
    sid = f.supplier_id
    band = getattr(state.ctx, "bands", {}).get(sid)
    cred = getattr(state.ctx, "credibility", {}).get(sid)
    fit = getattr(state.ctx, "fits", {}).get(sid)
    standing = f.standing.terms if f.standing else {}
    lines = [
        "DECISION FRAME (computed by code - hard limits; a sheet outside them is corrected or dropped)",
        thread_state(state, f),
    ]
    if f.standing and f.standing.price is not None:
        lines.append(
            f"- Counter limits: unit_price <= {f.standing.price} (its current offer) and every sheet must cost "
            f"us less than {_money(f.standing.c)} (its standing sheet)."
        )
    if not math.isinf(f.our_last_c):
        lines.append(
            f"- Never take back: no sheet may cost us less than {_money(f.our_last_c)} "
            "(our current offer: never take it back)."
        )
    if cred is not None:
        lines.append(f"- Lowest credible price: {cred.lowest_price} ({cred.reason}).")
    if band is not None:
        lines.append(
            f"- Price band: floor {band.floor}, fair {band.fair}, target {band.target} (C {_money(band.c_target)}),"
            f" ceiling {band.ceiling}. Derivation: {'; '.join(band.derivation)[:400]}"
        )
    if fit is not None and fit.prices:
        lines.append(
            f"- Its price path fit: reservation estimate {fit.reservation_estimate}, last step "
            f"{None if fit.last_step_pct is None else round(fit.last_step_pct, 2)} %, shrinking {fit.shrinking},"
            f" chance of another >= 1 % step {fit.p_room}."
        )
    accept_ok = f.acceptable and (f.our_rounds > 0 or f.status == "agreed")
    lines.append(
        f"- Accept allowed now: {'yes' if accept_ok else 'no'}. Walk away allowed now: "
        f"{'yes' if walk_allowed(state, f) else 'no'}."
    )
    lines.append("ALLOWED VALUES PER FIELD (use these labels in counter_offers):")
    lines.append(T.options_text(state.case, standing, f.std))
    lines.append(lever_guidance(state, f))
    lines.append(pace_guidance(state, f))
    args = F.arguments(state, sid)
    lines.append(
        "VERIFIED ARGUMENTS YOU MAY CITE (put the token on its own line; max 3; ranked by how often each"
        " kind changed a supplier's calculation in our replays - our own order history never did):"
    )
    lines += [f"- [[{a.token}]] ({a.kind}, strength {a.strength}): {a.sentence}" for a in args[:10]] or ["- none left"]
    return "\n".join(lines)


def lever_guidance(state: F.CaseState, f: F.ThreadFacts) -> str:
    """The biggest savings per lever (the lever tool), with the direction, and what an early payment costs us."""
    trades = getattr(state.ctx, "trades", {}).get(f.supplier_id, ())
    lines = ["LEVERS WORTH THE MOST TO US (each single term change against its quote, from the lever tool):"]
    for t in trades[:5]:
        lines.append(
            f"- {t.field}: ask for {T.label(t.ours)} instead of {T.label(t.theirs)} - saves {_money(t.eur)} over the"
            f" horizon (worth {t.per_unit:.3f} per unit as a price step)"
        )
    if not trades:
        lines.append("- none priced yet")
    b = state.case.get("buyer", {})
    lines.append(
        f"- Paying earlier than our reference ({b.get('reference_payment_days')} days) COSTS us interest at"
        f" {b.get('interest_rate')} a year: offer early payment only where the price cut it buys is larger"
        " (price_package shows C). A longer payment term is money for us."
    )
    lines.append("- Put the biggest lever in at least one package every round unless it refused that term.")
    return "\n".join(lines)


def pace_guidance(state: F.CaseState, f: F.ThreadFacts) -> str:
    """Round 1: the anchor. Later: the step the concession rules allow this round (code enforces it)."""
    from . import guard

    sid = f.supplier_id
    band = getattr(state.ctx, "bands", {}).get(sid)
    cred = getattr(state.ctx, "credibility", {}).get(sid)
    price = f.standing.price if f.standing else None
    if f.our_rounds == 0 or math.isinf(f.our_last_c):
        lines = ["YOUR FIRST COUNTER (the anchor - judged by you, inside the hard limits):"]
        if price and band is not None:
            fair = f"{band.fair} ({(band.fair / price - 1) * 100:+.1f} % vs its quote)" if band.fair else "unknown"
            lines.append(
                f"- Its quote {price}; fair price by its cost drivers {fair}; our target package price "
                f"{band.target} ({(band.target / price - 1) * 100:+.1f} %); floor estimate {band.floor}."
            )
        if cred is not None and price:
            lines.append(
                f"- Lowest credible price {cred.lowest_price} ({(cred.lowest_price / price - 1) * 100:+.1f} %):"
                f" {cred.reason}."
            )
        lines.append(
            "- In our replays, first counters 4-8 % under the quote closed best; counters more than about 15 %"
            " under were answered with a final offer almost every time, which ends the room to trade."
            " Go that deep only with firm cost evidence (a should-cost, its own breakdown) and a supplier"
            " that is not walk-prone. Anchor on fair price and target, precise figures, 2-3 packages."
        )
        return "\n".join(lines)
    limit = guard.step_limit(state, f)
    its_step = f.supplier_cs[-2] - f.supplier_cs[-1] if len(f.supplier_cs) >= 2 else 0.0
    gap = (f.standing.c - f.our_last_c) if f.standing else float("nan")
    lines = [
        "THIS ROUND'S STEP (concession discipline, enforced by code):",
        f"- Its last move was worth {_money(its_step)} to us; the gap between its sheet and our offer is "
        f"{_money(gap)}.",
    ]
    if limit is not None:
        lines.append(
            f"- Our next sheet may cost us at most {_money(limit)} (its last step, or a small visible step if it"
            " stood still, and never more than half the gap): a bigger step is cut back to that."
        )
    lines.append(
        "- Do not close the gap yourself while it still moves: hold the price and vary a term, let it come."
        " If its sheet is already as good as what you would offer next, accept it (code does this too)."
        " A supplier that answers 'agreed' got more than it needed."
    )
    return "\n".join(lines)


def walk_allowed(state: F.CaseState, f: F.ThreadFacts) -> bool:
    """Walking away from a supplier: only when its sheet cannot be signed and it will not move any more."""
    return not f.acceptable and (f.status == "final_offer" or f.moves_left <= 1)


def price_path(prices) -> str:
    """'17.40 -> 17.00 (-2.3 %) -> 16.85 (-0.9 %)': each step and its size, so the pattern of moves is visible."""
    prices = [float(p) for p in prices]
    if not prices:
        return "none yet"
    out = [f"{prices[0]:.2f}"]
    for a, b in zip(prices, prices[1:], strict=False):
        out.append(f"{b:.2f} ({(b / a - 1) * 100:+.1f} %)" if a else f"{b:.2f}")
    return " -> ".join(out)


def negotiation_log(state: F.CaseState, f: F.ThreadFacts) -> str:
    """What we decided in every earlier round with this supplier and why, with both price paths - the memory that
    keeps one strategy across rounds (the e-mails show what was said; this shows what we meant)."""
    entries = state.notes.get(f.supplier_id, {}).get("log", [])
    lines = [
        "OUR NEGOTIATION LOG WITH THIS SUPPLIER (every round so far - keep one coherent strategy):",
        f"- Its price path: {price_path(f.supplier_prices)}",
        f"- Our price path: {price_path(f.our_prices)}",
    ]
    if not entries:
        lines.append("- No earlier round: this is our first answer.")
    for e in entries:
        sheets = "; ".join(f"{s['terms']} (C {_money(s['c'])})" for s in e.get("sheets") or []) or "none"
        lines.append(f"- Round {e['round']} ({e.get('path')}): {e.get('decision')} - {e.get('why')}")
        lines.append(f"    our sheets: {sheets}")
        if e.get("cited"):
            lines.append(f"    data we cited (never again): {', '.join(e['cited'])}")
        read = e.get("its_message") or {}
        if read:
            lines.append(
                f"    its message as we read it then: {read.get('offer_summary')} | moved: {read.get('movement')} | "
                f"wants: {read.get('wants')} | tone: {read.get('tone')} | walk risk {read.get('walk_risk')}, "
                f"room {read.get('room_to_move')}"
            )
            if read.get("questions_to_answer"):
                lines.append(f"    its questions then: {read.get('questions_to_answer')}")
    return "\n".join(lines)


def precomputed(role: str, tools, f: F.ThreadFacts) -> str:
    """The tool results a specialist would ask for first, run by code before it starts (milliseconds, no model
    turn): the deal agent timed out in 18 of 42 live calls while fetching these one by one."""
    spec = SPECS.get(role)
    if spec is None or not spec.preload:
        return ""
    sid, part = f.supplier_id, None
    blocks = []
    for name in spec.preload:
        tool = tools.tools.get(name)
        if tool is None:
            continue
        args = {}
        if "supplier_id" in tool.params:
            args["supplier_id"] = sid
        if "part_id" in tool.params:
            part = part or (tools.ctx.state.case.get("parts") or [None])[0]
            if not part:
                continue
            args["part_id"] = part
        if name == "propose_counter":
            trades = getattr(tools.ctx.state.ctx, "trades", {}).get(sid, ())
            args["levers"] = [t.field for t in trades[:3] if t.field != "unit_price"]
        try:
            out = tools.run(name, args)
        except Exception as e:  # a tool that fails here is still callable by the agent
            out = {"error": str(e)[:200]}
        blocks.append(
            f"[{name}({', '.join(f'{k}={v}' for k, v in args.items())})]\n"
            + json.dumps(out, ensure_ascii=False, default=str)[:6000]
        )
    return "TOOL RESULTS ALREADY FETCHED FOR YOU (call a tool only for something not here):\n\n" + "\n\n".join(blocks)


def analyst_input(role: str, state: F.CaseState, f: F.ThreadFacts, events: list[dict], pre: str = "") -> str:
    """Every specialist reads the case, the computed state, our log of every round and the WHOLE thread as written."""
    brief, now, memory = case_brief(state, f.supplier_id), thread_state(state, f), negotiation_log(state, f)
    thread = "THE WHOLE E-MAIL THREAD AS WRITTEN (oldest first):\n\n" + emails.transcript(events)
    args = F.arguments(state, f.supplier_id)
    tokens = "\n".join(f"- [[{a.token}]] ({a.kind}): {a.sentence}" for a in args[:12]) or "- none left"
    task = {
        "reader": "Read the supplier's latest e-mail (the last SUPPLIER message) in the light of everything before "
        "it, and report.",
        "deal": "Price the deal and design this round's counter candidates, consistent with the moves so far.",
        "counterpart": f"Profile {supplier_name(state, f.supplier_id)} ({f.supplier_id}) from the data and from how it "
        "has negotiated in this thread so far, and report.",
        "market": "Judge the price and choose the arguments (never one we already cited), and report.",
    }[role]
    extra = "VERIFIED ARGUMENTS AVAILABLE (tokens):\n" + tokens + "\n\n" if role == "market" else ""
    if role == "deal":
        extra += lever_guidance(state, f) + "\n\n"
    return "\n\n".join(x for x in [brief, now, memory, pre, extra + thread, task] if x)


def negotiator_input(state: F.CaseState, f: F.ThreadFacts, events: list[dict], reports: dict) -> str:
    parts = [
        case_brief(state, f.supplier_id),
        decision_frame(state, f),
        negotiation_log(state, f),
        "THE WHOLE E-MAIL THREAD AS WRITTEN (oldest first):",
        emails.transcript(events),
        "SPECIALIST REPORTS:",
    ]
    for role in ("reader", "counterpart", "market", "deal"):
        report = reports.get(role)
        body = json.dumps(report, ensure_ascii=False, indent=1) if report else "(no report this round)"
        parts.append(f"[{role}]\n{body}")
    parts.append("Decide now and write the e-mail if you counter.")
    return "\n\n".join(parts)
