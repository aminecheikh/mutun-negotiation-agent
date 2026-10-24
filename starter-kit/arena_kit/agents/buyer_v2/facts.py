"""What a case and each thread say, computed - never guessed (deterministic, no LLM).

`CaseState` is prepared once per case (bands, claims, dossiers, trades from the shared deterministic tools) and
refreshed every round. `thread_facts` reads one thread into `ThreadFacts`: the supplier's standing sheet and its
total cost C, its other sheets, our offers still on the table, whether it accepted one of them, whether its
sheet may be accepted at all, and the hard limits for our next counter. The negotiator sees these facts as its
decision frame; the guard enforces them.

An acceptance is recognised by terms, not only by status: `agreed`, or a standing sheet equal to one of our
offers, or one that costs us no more than our own offer - whatever status the supplier left on it.
"""

import math
import threading
from dataclasses import dataclass, field, replace

from ...cost import closing_terms, complete_offer, must_haves_ok, total_cost
from ...docs import standard_terms
from ...play import ENDED, moves_left
from ..llm import ID_RE
from .config import V2Config

FINAL_STATUSES = ENDED | {"final_offer", "agreed"}


@dataclass(frozen=True)
class Sheet:
    id: str | None  # the supplier's term_sheet_id; None for ours
    terms: dict
    c: float  # total cost C in EUR on closing terms; inf if it has no price

    @property
    def price(self) -> float | None:
        p = self.terms.get("unit_price")
        return float(p) if isinstance(p, int | float) and not isinstance(p, bool) else None


@dataclass(frozen=True)
class ThreadFacts:
    supplier_id: str
    status: str
    alive: bool
    our_rounds: int  # messages we sent in this thread
    moves_left: int
    standing: Sheet | None  # the sheet an accept can name (its first sheet in its latest message)
    alternatives: tuple[Sheet, ...]  # its other sheets in that message (cannot be accepted by ID)
    our_offer: tuple[Sheet, ...]  # our latest term sheets, still on the table
    our_last_c: float  # the C of our current position: the cheapest sheet of our latest offer (inf before it)
    our_prices: tuple[float, ...]  # main price of each of our offers, oldest first
    supplier_prices: tuple[float, ...]  # its standing price after each of its messages, oldest first
    supplier_cs: tuple[float, ...]  # C of each of its standing sheets, oldest first (same order)
    acceptable: bool  # its standing sheet may be accepted (must-haves, not dearer than opening/alternative)
    why_not: str  # why it is not acceptable ("" if it is)
    accepted_ours: bool  # it accepted our offer (status agreed, or its standing sheet is our offer or better)
    accepted_alternative: Sheet | None  # one of its other sheets is our offer: confirm it so it becomes standing
    std: dict = field(default_factory=dict)  # its standard terms from its latest quote PDF


@dataclass
class CaseState:
    case: dict
    ctx: object  # contracts.CaseContext (shared deterministic tools)
    tools: object  # tools.DataTools
    config: V2Config
    c_alt: float | None
    c_open: float = math.inf
    estimates: dict = field(default_factory=dict)  # supplier_id -> our estimate of it (relationship charge)
    burned: set = field(default_factory=set)  # data IDs already cited in this case: each counts once
    notes: dict = field(default_factory=dict)  # supplier_id -> {"counterpart": {...}, "market": {...}}
    refused: set = field(default_factory=set)  # term_sheet_ids the arena refused to let us accept: never again
    tokens: dict = field(default_factory=dict)  # argument refs -> its token ("A1"): stable for the whole case
    lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def negotiable(self) -> list[str]:
        return list(self.case.get("negotiable") or ())


# ---- costs -------------------------------------------------------------------------------------------------


def cost(state: CaseState, supplier_id: str, terms: dict, std: dict | None = None) -> float:
    """C of a sheet with this supplier on closing terms, with our estimate of it for the relationship charge."""
    if not terms or terms.get("unit_price") is None:
        return math.inf
    try:
        return total_cost(state.case, terms, std or {}, state.estimates.get(supplier_id))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return math.inf


# Goals where C charges us for leaving the supplier below a sustainable margin: there a quote under our cost
# floor means a genuinely squeezed supplier, and the floor stands. Elsewhere it means our estimate is wrong.
RELATIONSHIP_GOALS = {"stabilise", "secure_supply"}


def floor_is_credible(state: CaseState, supplier_id: str, quote: float | None) -> bool:
    """A cost floor above the supplier's own asking price contradicts itself: nobody quotes below cost for long.
    Outside the relationship goals we stop treating such a floor as a constraint, rather than anchoring just
    under a quote our own model cannot explain."""
    band = getattr(state.ctx, "bands", {}).get(supplier_id)
    if band is None or band.floor is None or quote is None:
        return True
    if state.case.get("buyer", {}).get("category_goal") in RELATIONSHIP_GOALS:
        return True
    return band.floor <= quote


def credible_floor(state: CaseState, supplier_id: str, quote: float | None) -> float | None:
    """The lowest price we may offer this supplier: `tools/credibility`, unless its cost evidence contradicts the
    supplier's own quote, in which case the no-evidence rule applies instead."""
    cred = getattr(state.ctx, "credibility", {}).get(supplier_id)
    floor = cred.lowest_price if cred is not None and cred.lowest_price else None
    if floor is None or quote is None:
        return floor
    if floor_is_credible(state, supplier_id, quote):
        return floor
    from ..buyer.tools.credibility import NO_EVIDENCE_GAP_PCT

    return round(min(floor, quote * (1 - NO_EVIDENCE_GAP_PCT / 100)), 2)


def alternative_cost(case: dict) -> float | None:
    alt = case.get("buyer", {}).get("alternative") or {}
    if alt.get("kind") in (None, "none") or not (alt.get("terms") or {}).get("unit_price"):
        return None
    try:
        return total_cost(case, alt["terms"])
    except (KeyError, TypeError, ValueError):
        return None


def opening_cost(state: CaseState, threads: dict[str, list[dict]]) -> float:
    """C_open as the arena defines it: the cheapest opening quote that keeps the must-haves."""
    best = math.inf
    for sid, events in threads.items():
        quote = next((m for m in events if m.get("from") != "buyer" and m.get("term_sheets")), None)
        if quote is None:
            continue
        terms = quote["term_sheets"][0].get("terms") or {}
        std = standard_terms(events[: events.index(quote) + 1]) or standard_terms(events)
        if terms.get("unit_price") is not None and must_haves_ok(state.case, terms, std):
            best = min(best, cost(state, sid, terms, std))
    return best


# ---- reading a thread ---------------------------------------------------------------------------------------


def latest_supplier(events: list[dict]) -> dict | None:
    return next((m for m in reversed(events) if m.get("from") != "buyer"), None)


def ours_with_sheets(events: list[dict]) -> list[dict]:
    return [m for m in events if m.get("from") == "buyer" and m.get("term_sheets")]


def _sheet_terms(ts: dict) -> dict:
    return dict(ts.get("terms", ts) or {})


def same_terms(case: dict, a: dict, b: dict, std: dict, eps: float) -> bool:
    """Two sheets close on the same deal: every negotiable field equal on closing terms, prices within eps."""
    ca, cb = closing_terms(case, a, std), closing_terms(case, b, std)
    for f in case.get("negotiable") or ():
        x, y = ca.get(f), cb.get(f)
        if f == "unit_price":
            if not isinstance(x, int | float) or not isinstance(y, int | float) or abs(float(x) - float(y)) > eps:
                return False
        elif x != y:
            return False
    return True


def thread_facts(state: CaseState, supplier_id: str, events: list[dict]) -> ThreadFacts:
    case, cfg = state.case, state.config
    last = latest_supplier(events) or {}
    status = str(last.get("status") or "open")
    std = standard_terms(events)
    # its standing offer is its latest term sheet: an e-mail without one (a question, a note) leaves it standing
    priced = next((m for m in reversed(events) if m.get("from") != "buyer" and m.get("term_sheets")), {})
    raw = [s for s in (priced.get("term_sheets") or []) if isinstance(s, dict)]
    sheets = [
        Sheet(s.get("term_sheet_id"), _sheet_terms(s), cost(state, supplier_id, _sheet_terms(s), std)) for s in raw
    ]
    standing = sheets[0] if sheets else None
    mine = ours_with_sheets(events)
    our_offer = tuple(
        Sheet(None, t, cost(state, supplier_id, t, std)) for t in (_sheet_terms(ts) for ts in mine[-1]["term_sheets"])
    ) if mine else ()  # fmt: skip
    our_last_c = min((o.c for o in our_offer), default=math.inf)
    our_prices = tuple(
        float(m["term_sheets"][0]["terms"]["unit_price"])
        for m in mine
        if isinstance((m["term_sheets"][0].get("terms") or {}).get("unit_price"), int | float)
    )
    supplier_prices = tuple(
        float(m["term_sheets"][0]["terms"]["unit_price"])
        for m in events
        if m.get("from") != "buyer"
        and m.get("term_sheets")
        and isinstance((m["term_sheets"][0].get("terms") or {}).get("unit_price"), int | float)
    )

    supplier_cs = tuple(
        cost(state, supplier_id, _sheet_terms(m["term_sheets"][0]), std)
        for m in events
        if m.get("from") != "buyer"
        and m.get("term_sheets")
        and isinstance((m["term_sheets"][0].get("terms") or {}).get("unit_price"), int | float)
    )

    def is_ours(sheet: Sheet) -> bool:
        if not our_offer or sheet.price is None:
            return False
        if any(same_terms(case, sheet.terms, o.terms, std, cfg.same_price_eps) for o in our_offer):
            return True
        best_ours = min(o.c for o in our_offer)
        return not math.isinf(sheet.c) and sheet.c <= best_ours * (1 + cfg.same_cost_rel)

    acceptable, why = sheet_acceptable(state, standing, std)
    accepted = standing is not None and (status == "agreed" or is_ours(standing))
    alternative = next((s for s in sheets[1:] if is_ours(s)), None) if not accepted else None
    return ThreadFacts(
        supplier_id=supplier_id,
        status=status,
        alive=status not in ENDED,
        our_rounds=sum(m.get("from") == "buyer" for m in events),
        moves_left=moves_left(case, events),
        standing=standing,
        alternatives=tuple(sheets[1:]),
        our_offer=our_offer,
        our_last_c=our_last_c,
        our_prices=our_prices,
        supplier_prices=supplier_prices,
        supplier_cs=supplier_cs,
        acceptable=acceptable,
        why_not=why,
        accepted_ours=accepted,
        accepted_alternative=alternative,
        std=std,
    )


def sheet_acceptable(state: CaseState, sheet: Sheet | None, std: dict) -> tuple[bool, str]:
    """May this supplier sheet be accepted at all? Must-haves on closing terms, priced, not dearer than the best
    opening (a deal above it scores 0) and not dearer than our alternative (no deal scores the alternative)."""
    if sheet is None or not sheet.id:
        return False, "no standing sheet"
    if sheet.id in state.refused:
        return False, "the arena refused an accept of this sheet"
    if math.isinf(sheet.c):
        return False, "its sheet cannot be priced"
    if not must_haves_ok(state.case, sheet.terms, std):
        return False, "breaks a must-have on its closing terms"
    if not math.isinf(state.c_open) and sheet.c > state.c_open * (1 + 1e-9):
        return False, f"C {sheet.c:,.0f} is above the best opening quote ({state.c_open:,.0f}): it would score 0"
    if state.c_alt is not None and sheet.c > state.c_alt:
        return False, f"C {sheet.c:,.0f} is above our alternative ({state.c_alt:,.0f})"
    return True, ""


# ---- what we may still cite -----------------------------------------------------------------------------------


def cited_ids(threads: dict[str, list[dict]]) -> set[str]:
    """Every data ID we already cited in this case (body and refs): each counts once per case."""
    out = set()
    for events in threads.values():
        for m in events:
            if m.get("from") == "buyer":
                out.update(ID_RE.findall(str(m.get("body") or "")))
                out.update(r for r in m.get("refs") or () if r)
    return out


@dataclass(frozen=True)
class Argument:
    token: str  # "A1", "A2", ... - how agents and the e-mail refer to it (fixed per case, see token_for)
    sentence: str  # the verified sentence, copied verbatim into the e-mail
    refs: tuple[str, ...]
    kind: str
    strength: float


def arguments(state: CaseState, supplier_id: str) -> list[Argument]:
    """Verified arguments for this supplier that help us and whose IDs were not cited yet, strongest first. No
    argument rests on another supplier's quote or offer (organisers: each supplier is negotiated on its own)."""
    claims = getattr(state.ctx, "claims", {}).get(supplier_id, ())
    usable = [
        c
        for c in claims
        if c.favours_buyer
        and not (set(c.refs) & state.burned)
        and not any(r.startswith(("offer:", "quote:")) for r in c.refs)
        and c.kind not in {"rival_quote", "rival_offer"}
    ]
    usable.sort(key=lambda c: (-effect(c), -c.strength))
    return [
        Argument(token_for(state, c.refs), true_unit(state, c), tuple(c.refs), c.kind, round(effect(c) * c.strength, 2))
        for c in usable
    ]


def effect(claim) -> float:
    """How likely this argument changes the supplier's calculation (KIND_EFFECT). A cost driver that ROSE is read
    by suppliers as confirming their case ('your figures confirm our case: that driver rose', 17 replays): it ranks
    below a falling driver even when it bounds an increase."""
    base = KIND_EFFECT.get(claim.kind, 0.5)
    if claim.kind == "index_change" and isinstance(claim.figure, int | float) and claim.figure > 0:
        return min(base, RISING_DRIVER_EFFECT)
    return base


# How often a verified argument of each kind changed a supplier's calculation in our practice replays (94
# sessions, every supplier): facts about ITS cost structure and OUR commitments move it; what we paid it before
# never does - it knows its own prices. A prior on the argument, not a rule: the market agent still chooses.
KIND_EFFECT = {
    "index_change": 1.0,  # 15 of 15 'took it into account'
    "should_cost": 1.0,  # 14 of 14
    "notice": 0.95,  # 9 of 10
    "forecast": 0.8,  # volume change: 22 of 29
    "quality": 0.7,
    "scorecard": 0.4,  # delivery record: 20 of 61
    "po_history": 0.1,  # 0 of 79: 'it holds, but it does not change our calculation'
}
RISING_DRIVER_EFFECT = 0.4


UOM = {"pc": ("unit", "pieces"), "kg": ("kg", "kg"), "m": ("m", "m")}


def true_unit(state: CaseState, claim) -> str:
    """The claim's sentence with the price unit the data really uses: "EUR 12.46 per 100 pieces", not "per unit",
    when the part is priced per 100 (the figure and the IDs stay as checked; only the unit words change)."""
    sentence = claim.sentence
    if " per unit" not in sentence or claim.kind not in {"po_history", "should_cost"}:
        return sentence
    package = getattr(state.tools, "package", None)
    if package is None:
        return sentence
    try:
        fact = package.lookup(claim.refs[0]) or {}
        part = fact.get("part") or fact.get("part_id")
        row = package.query("SELECT price_unit, uom FROM parts WHERE id = ?", (part,), 1) if part else []
    except Exception:
        return sentence
    if not row:
        return sentence
    per, uom = int(fact.get("price_unit") or row[0]["price_unit"] or 1), str(row[0]["uom"] or "pc")
    single, plural = UOM.get(uom, (uom, uom))
    unit = single if per == 1 else f"{per:,} {plural}"
    return sentence.replace(" per unit", f" per {unit}")


def token_for(state: CaseState, refs) -> str:
    """The argument's token, fixed for the whole case: tokens never shift when other arguments get cited, so the
    token an agent chose always names the same sentence."""
    key = tuple(sorted(refs))
    with state.lock:
        if key not in state.tokens:
            state.tokens[key] = f"A{len(state.tokens) + 1}"
        return state.tokens[key]


def with_estimates(state: CaseState, threads: dict[str, list[dict]]) -> None:
    """Refresh our estimate of every supplier from its thread (its own cost breakdown replaces the data-based
    estimate once it sent one). The relationship charge in stabilise / secure_supply cases depends on it."""
    for sid, events in threads.items():
        try:
            state.estimates[sid] = state.tools.supplier_from_thread(state.case, sid, events)
        except Exception:  # an estimate refines C; without it C leaves the relationship charge out
            state.estimates.setdefault(sid, None)


def complete(state: CaseState, terms: dict, base: dict | None = None) -> dict:
    """A full term sheet over the negotiable fields: `terms` over `base` (the supplier's standing terms)."""
    return complete_offer(state.case, {**(base or {}), **terms})


def refreshed(state: CaseState, threads: dict[str, list[dict]]) -> CaseState:
    """Re-run the per-round deterministic tools (claims, credibility, bands after a cost breakdown, fits)."""
    with state.lock:
        state.burned |= cited_ids(threads)
    with_estimates(state, threads)
    try:
        ctx = state.tools.refresh(state.ctx, threads)
    except Exception:
        ctx = state.ctx
    fits = {}
    for sid, events in threads.items():
        try:
            fits[sid] = state.tools.fit_concessions(ctx, sid, events)
        except Exception:
            continue
    state.ctx = replace(ctx, fits={**getattr(ctx, "fits", {}), **fits})
    state.c_open = opening_cost(state, threads)  # the same supplier estimates as every other C this round
    return state
