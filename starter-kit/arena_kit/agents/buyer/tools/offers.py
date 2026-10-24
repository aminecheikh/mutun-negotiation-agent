"""The offer engine: 1-3 complete term sheets per round (owner: Amine).

Price of the main sheet, each round:
  anchor   2 x target - their opening: deals tend to close near the midpoint of the two opening offers
           (split-the-difference, Raiffa), so this anchor puts the midpoint on our target; never below the
           plausible cost floor (an impossible ask wastes rounds and trust)
  time     anchor -> ceiling on a Boulware curve: (t / T) ** (1 / beta), beta < 1 holds early and concedes late
           (time-dependent tactics, Faratin, Sierra & Jennings 1998); above our target its steps never grow -
           shrinking steps signal a limit (Tey et al. 2021)
  pace     until our target, at least a linear step per round (reaching it halfway): visible movement
  reply    at least what the supplier conceded in its last move (absolute tit-for-tat, same paper): "it gives
           nothing without a move from you" (the brief) - and matched moves meet at the midpoint, i.e. the target.
           Never past the midpoint of the current gap: a big drop from an inflated opening is not a step to copy
           (split-the-difference, Raiffa); below our target, not past the target either
  stand    when the supplier stood still, never our last price again: a step toward its price (the pace above,
           else a share of the gap), never past the midpoint - its standstill says our target was low
  limits   never above the ceiling or its standing price, never below our previous offer, never below the
           lowest credible price (tools.credibility: our cost evidence and its current offer), never below the
           price where C stops falling (the squeeze charge makes a lower price dearer: stabilise cases)
The main sheet is the cheapest package in C the supplier can still accept at this price: the band's target
package, its own terms or our last ones (the target package's short payment or pickup only pays near the target
price). Up to two more sheets change one term each and are re-priced to cost us the same C: worth more to the
supplier (its estimated cost of money), so its answer shows what it values.
"""

from ....cost import complete_offer, cost_breakdown, must_haves_ok, supplier_value, total_cost
from ....docs import standard_terms
from ....play import standing
from ..contracts import BehaviourRead, CaseContext, OfferPlan, Tactic
from .bands import HOLD_AT_STANDARD, PRICE_STEPS
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
    credible = ctx.credibility.get(supplier_id)
    alternative = _cheaper_alternative(case, events, std, supplier)
    if alternative is not None:  # its own cheaper proposal goes back unchanged: it will agree to it (Saed, P18)
        c_alt = total_cost(case, alternative, std, supplier)
        return OfferPlan(
            supplier_id, (alternative,), (c_alt,), "its own alternative sheet is cheaper for us: sent back"
        )
    price = next_price(case, band, events, credible.lowest_price if credible else 0.0)
    main = _main_sheet(case, band, theirs, price, std, supplier, _our_last_terms(events))
    main = _not_below_c_minimum(case, main, std, supplier, band, min(band.ceiling, theirs["unit_price"]))
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


def next_price(case: dict, band, events: list[dict], lowest: float = 0.0) -> float:
    theirs, ours = supplier_prices(events), our_prices(events)
    limit = min(band.ceiling, theirs[-1])
    anchor = anchor_price(band, events)
    horizon = max(case.get("round_limit", 10) - 1, 1)
    share = min(len(ours) / horizon, 1.0) ** (1 / BOULWARE_BETA)
    price = anchor + (band.ceiling - anchor) * share
    if len(ours) >= 2 and ours[-1] >= band.target and ours[-1] > ours[-2]:
        # above our target the curve's late steps grow; shrinking steps signal a limit (Tey et al. 2021, research
        # 02): never a larger step than our last one. Matching its move and the stand-still step still apply
        price = min(price, ours[-1] + (ours[-1] - ours[-2]))
    if ours:
        price = max(price, ours[-1])
        if ours[-1] < band.target:  # a visible step every round until our target, even if it waits for us
            pace = (band.target - anchor) / max(horizon * TARGET_BY_SHARE_OF_ROUNDS, 1)
            price = max(price, min(ours[-1] + pace, band.target))
        if len(theirs) >= 2 and theirs[-1] >= theirs[-2] and price <= ours[-1] < theirs[-1]:  # it stood still
            pace = (band.target - anchor) / max(horizon * TARGET_BY_SHARE_OF_ROUNDS, 1)
            step = max(pace, (theirs[-1] - ours[-1]) / horizon)
            price = max(price, min(ours[-1] + step, (ours[-1] + theirs[-1]) / 2))
        if len(theirs) >= 2 and theirs[-2] > theirs[-1]:
            matched = ours[-1] + TIT_FOR_TAT * (theirs[-2] - theirs[-1])
            cap = (ours[-1] + theirs[-1]) / 2
            price = max(price, min(matched, cap, band.target) if ours[-1] < band.target else min(matched, cap))
    return round(min(max(price, lowest), limit), 2)


def _not_below_c_minimum(case: dict, sheet: dict, std: dict, supplier, band, limit: float) -> dict:
    """The same sheet, never below the price where C on its terms is lowest (never above `limit`). Without the
    squeeze charge C rises with price and the sheet stays as it is; with it (stabilise / secure_supply, a supplier
    below its sustainable margin) a lower price can cost us more - never worth offering. The minimum is searched
    between fixed bounds (half the band's ceiling .. the ceiling), so it is the same price every round: a floor
    that moved with the round's price crept up by cents while the supplier was still moving (P01)."""
    if supplier is None or limit <= sheet["unit_price"]:
        return sheet
    low, high = band.ceiling / 2, band.ceiling

    def cheapest(a: float, b: float) -> dict:
        grid = [a + (b - a) * i / PRICE_STEPS for i in range(PRICE_STEPS + 1)]
        options = [complete_offer(case, {**sheet, "unit_price": round(p, 2)}) for p in grid]
        return min(options, key=lambda s: (total_cost(case, s, std, supplier), s["unit_price"]))

    best = cheapest(low, high)
    width = (high - low) / PRICE_STEPS
    floor = cheapest(max(low, best["unit_price"] - width), min(high, best["unit_price"] + width))["unit_price"]
    if floor <= sheet["unit_price"]:
        return sheet
    return complete_offer(case, {**sheet, "unit_price": round(min(floor, limit), 2)})


def _cheaper_alternative(case: dict, events: list[dict], std: dict, supplier) -> dict | None:
    """The supplier's cheapest alternative sheet in its last e-mail, if it costs us less than its main sheet."""
    last = events[-1] if events and events[-1].get("from") != "buyer" else {}
    sheets = [complete_offer(case, ts.get("terms") or {}) for ts in last.get("term_sheets") or []]
    sheets = [s for s in sheets if s.get("unit_price") is not None and must_haves_ok(case, s, std)]
    if len(sheets) < 2 or (last.get("term_sheets") or [{}])[0].get("terms", {}).get("unit_price") is None:
        return None
    main_c = total_cost(case, complete_offer(case, last["term_sheets"][0]["terms"]), std, supplier)
    best = min(sheets[1:], key=lambda s: total_cost(case, s, std, supplier), default=None)
    return best if best is not None and total_cost(case, best, std, supplier) < main_c else None


def _our_last_terms(events: list[dict]) -> dict | None:
    mine = [m for m in events if m.get("from") == "buyer" and m.get("term_sheets")]
    return mine[-1]["term_sheets"][0]["terms"] if mine else None


def _main_sheet(case: dict, band, theirs: dict, price: float, std: dict, supplier=None, last=None) -> dict:
    """The lowest-C sheet at this round's price among the target package, its terms and our last terms, that
    keeps the must-haves and is worth as much to the supplier as the target package at the target price (what
    the band found it can accept) and as our last sheet (never take back what we offered). Without a supplier
    estimate: the target package, else its own terms."""
    bases = [band.target_terms, theirs, *([last] if last and supplier else [])]
    sheets = [complete_offer(case, {**base, "unit_price": price}) for base in bases]
    valid = [s for s in sheets if must_haves_ok(case, s, std)]
    if not valid:
        return complete_offer(case, {**theirs, "unit_price": price})
    if supplier is None:
        return valid[0]
    bar = max(supplier_value(case, band.target_terms, supplier), supplier_value(case, last, supplier) if last else 0)
    feasible = [s for s in valid if supplier_value(case, s, supplier) >= bar] or valid[:1]
    return min(feasible, key=lambda s: total_cost(case, s, std, supplier))


def reprice(case: dict, sheet: dict, cost: float, std: dict, supplier) -> dict | None:
    """The same sheet at the price where it costs `cost`. C rises with price above the price where it is lowest
    (below that, the squeeze charge makes it rise again): find that minimum on a grid, then bisect on the rising
    side. A straight-line step failed right at the C minimum of squeeze cases (no variants at all)."""

    def c_at(price: float) -> float:
        return total_cost(case, {**sheet, "unit_price": round(price, 2)}, std, supplier)

    base = sheet["unit_price"]
    if base <= 0:
        return None
    grid = [base * (0.5 + i / PRICE_STEPS) for i in range(PRICE_STEPS + 1)]  # 0.5x .. 1.5x its price
    low = min(grid, key=c_at)
    high = grid[-1]
    if cost < c_at(low):  # even at its cheapest these terms cost more (at the C minimum every other package does)
        out = complete_offer(case, {**sheet, "unit_price": round(low, 2)})
        return out if c_at(low) <= cost * (1 + EQUAL_C_TOLERANCE) else None
    if cost > c_at(high):
        return None
    for _ in range(40):  # bisection to well under a cent
        mid = (low + high) / 2
        low, high = (mid, high) if c_at(mid) < cost else (low, mid)
    price = round(high, 2)
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
