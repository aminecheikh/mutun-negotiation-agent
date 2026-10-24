"""The offer engine: 1-3 complete term sheets per round (owner: Amine).

Price of the main sheet, each round:
  anchor   2 x target - their opening: deals tend to close near the midpoint of the two opening offers
           (split-the-difference, Raiffa), so this anchor puts the midpoint on our target; never below the
           plausible cost floor (an impossible ask wastes rounds and trust)
  time     anchor -> ceiling on a Boulware curve: (t / T) ** (1 / beta), beta < 1 holds early and concedes late
           (time-dependent tactics, Faratin, Sierra & Jennings 1998)
  pace     until our target, at least a linear step per round (reaching it halfway): visible movement
  reply    at least what the supplier conceded in its last move (absolute tit-for-tat, same paper): "it gives
           nothing without a move from you" - and matched moves meet at the midpoint, i.e. the target
  limits   never above the ceiling or its standing price, never below our previous offer
The main sheet uses the band's target package (payment days, dates, clauses). Up to two more sheets change one
term each and are re-priced to cost us the same C: worth more to the supplier (its estimated cost of money),
so its answer shows what it values.
"""

from ....cost import complete_offer, cost_breakdown, must_haves_ok, supplier_value, total_cost
from ....docs import standard_terms
from ....play import standing
from ..contracts import BehaviourRead, CaseContext, OfferPlan, Tactic
from .bands import HOLD_AT_STANDARD
from .concession import supplier_prices

BOULWARE_BETA = 0.5  # Faratin et al. 1998: beta < 1 is Boulware
TIT_FOR_TAT = 1.0  # absolute tit-for-tat: match the supplier's last concession
MAX_ANCHOR_BELOW_FLOOR_PCT = 5  # as bands.MAX_AIM_BELOW_FLOOR_PCT: the stated error of a plant-specific should-cost
MAX_SHEETS = 3  # the arena's limit per message
EQUAL_C_TOLERANCE = 0.002  # variants cost us within 0.2 % of the main sheet
# until our target we move at least at a linear pace that reaches it halfway through the rounds: the supplier
# 'gives nothing without a move from you', and a matched linear concession from its side meets us there
TARGET_BY_SHARE_OF_ROUNDS = 0.5


def plan(
    ctx: CaseContext, supplier_id: str, events: list[dict], tactic: Tactic, read: BehaviourRead, supplier=None
) -> OfferPlan:
    case, band = ctx.case, ctx.bands.get(supplier_id)
    _, theirs = standing(events)
    std = standard_terms(events)
    if band is None or theirs.get("unit_price") is None:
        sheet = complete_offer(case, theirs)
        return OfferPlan(supplier_id, (sheet,), (total_cost(case, sheet, std, supplier),), "no band: echo its terms")
    price = next_price(case, band, events)
    main = _main_sheet(case, band, theirs, price, std)
    c_main = total_cost(case, main, std, supplier)
    levers = tuple(f for f in tactic.levers if f != "unit_price" and f in case["negotiable"])
    fields = list(dict.fromkeys([*levers, *case["negotiable"]]))
    credible = band.floor * (1 - MAX_ANCHOR_BELOW_FLOOR_PCT / 100) if band.floor else 0.0
    credible = max(credible, anchor_price(band, events) * (1 - MAX_ANCHOR_BELOW_FLOOR_PCT / 100))
    price_range = (credible, theirs["unit_price"])
    variants = equal_cost_variants(case, main, c_main, std, supplier, fields, price_range, levers)
    sheets = (main, *variants)[:MAX_SHEETS]
    costs = tuple(total_cost(case, s, std, supplier) for s in sheets)
    why = f"price {main['unit_price']} (theirs {theirs['unit_price']}, target {band.target}, ceiling {band.ceiling})"
    return OfferPlan(supplier_id, sheets, costs, f"{why}; {len(variants)} equal-cost alternative(s)")


def our_prices(events: list[dict]) -> list[float]:
    mine = (m for m in events if m.get("from") == "buyer" and m.get("term_sheets"))
    return [m["term_sheets"][0]["terms"]["unit_price"] for m in mine]


def anchor_price(band, events: list[dict]) -> float:
    """Our most aggressive justified price: 2 x target - its opening, not under the plausible cost floor."""
    theirs = supplier_prices(events)
    lowest = band.floor * (1 - MAX_ANCHOR_BELOW_FLOOR_PCT / 100) if band.floor else 0.01
    return min(max(2 * band.target - theirs[0], lowest), band.target, band.ceiling, theirs[-1])


def next_price(case: dict, band, events: list[dict]) -> float:
    theirs, ours = supplier_prices(events), our_prices(events)
    limit = min(band.ceiling, theirs[-1])
    anchor = anchor_price(band, events)
    horizon = max(case.get("round_limit", 10) - 1, 1)
    share = min(len(ours) / horizon, 1.0) ** (1 / BOULWARE_BETA)
    price = anchor + (band.ceiling - anchor) * share
    if ours:
        price = max(price, ours[-1])
        if ours[-1] < band.target:  # a visible step every round until our target, even if it waits for us
            pace = (band.target - anchor) / max(horizon * TARGET_BY_SHARE_OF_ROUNDS, 1)
            price = max(price, min(ours[-1] + pace, band.target))
        if len(theirs) >= 2 and theirs[-2] > theirs[-1]:
            price = max(price, ours[-1] + TIT_FOR_TAT * (theirs[-2] - theirs[-1]))
    return round(min(price, limit), 2)


def _main_sheet(case: dict, band, theirs: dict, price: float, std: dict) -> dict:
    """The target package at this round's price; its own terms if the target package breaks a must-have."""
    for base in (band.target_terms, theirs):
        sheet = complete_offer(case, {**base, "unit_price": price})
        if must_haves_ok(case, sheet, std):
            return sheet
    return complete_offer(case, {**theirs, "unit_price": price})


def reprice(case: dict, sheet: dict, cost: float, std: dict, supplier) -> dict | None:
    """The same sheet at the price where it costs `cost` (secant on C, which is near-linear in price)."""
    p1 = sheet["unit_price"]
    p2 = p1 * 1.01 + 0.01
    c1, c2 = (total_cost(case, {**sheet, "unit_price": p}, std, supplier) for p in (p1, p2))
    if c2 == c1:
        return None
    price = round(p1 + (cost - c1) * (p2 - p1) / (c2 - c1), 2)
    if price <= 0:
        return None
    out = complete_offer(case, {**sheet, "unit_price": price})
    return out if abs(total_cost(case, out, std, supplier) - cost) <= cost * EQUAL_C_TOLERANCE else None


def equal_cost_variants(case, main, c_main, std, supplier, fields, price_range, levers=()) -> list[dict]:
    """One-term changes of the main sheet, re-priced to cost us the same. The strategist's levers always get
    their best such sheet - it chose to trade them; other fields only when the change leaves the supplier
    better off (its estimated cash value; without an estimate: more price for it). Levers first, then the
    best others, two at most. Never less coverage of our need than the main sheet (a shorter contract "paid
    for" by our uncovered-need cost would show our fallback), and prices within `price_range` (credible low ..
    its price)."""
    worth = (lambda s: supplier_value(case, s, supplier)) if supplier else (lambda s: s["unit_price"])
    base_worth, best = worth(main), {}
    uncovered = cost_breakdown(case, main, std)["uncovered_need"]
    for field in fields:
        for option in case.get("term_options", {}).get(field, []):
            if option == main.get(field) or (field not in levers and field in HOLD_AT_STANDARD):
                continue
            sheet = reprice(case, complete_offer(case, {**main, field: option}), c_main, std, supplier)
            if sheet is None or not price_range[0] <= sheet["unit_price"] <= price_range[1]:
                continue
            if cost_breakdown(case, sheet, std)["uncovered_need"] > uncovered or not must_haves_ok(case, sheet, std):
                continue
            value = worth(sheet)
            if (field in levers or value > base_worth) and value > best.get(field, (None, -float("inf")))[1]:
                best[field] = (sheet, value)
    ranked = sorted(best.items(), key=lambda kv: (kv[0] not in levers, -kv[1][1]))
    return [sheet for _, (sheet, _) in ranked[: MAX_SHEETS - 1]]
