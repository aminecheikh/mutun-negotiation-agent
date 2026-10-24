"""Price bands per supplier: floor, fair, target, ceiling (owner: Amine).

floor   the supplier's estimated full cost (estimates.full_cost), lowered for suppliers known to go below it
fair    today's price moved by the part's cost drivers (estimates.fair_price)
target  the cheapest package in C - relationship charge included - that the supplier can still accept
        (what it is worth to it in cash >= its floor), searched over every allowed option of the case
ceiling the most we accept: the opening, or less if our alternative is cheaper
Without a cost estimate (no data, bundles): the lowest anchor the case justifies (_anchor_without_cost).
"""

import itertools
import math
import random

from ....cost import closing_terms, complete_offer, must_haves_ok, supplier_value, total_cost
from ....docs import standard_terms
from ....play import standing
from ..contracts import PriceBand
from . import estimates

# only when the case gives no anchor at all: a first ask a little under the opening, as negotiators open
# below the other side's number; the live concession fit replaces it as soon as the supplier moves
UNINFORMED_TARGET_SHARE = 0.92
BELOW_COST_TOLERANCE = 0.05  # how far under full cost a supplier with a below-cost history may go
# the target may sit this far under the floor estimate, never more: the stated error of a plant-specific
# should-cost model (company.md, ~5 %); wider errors mean a weak estimate, not a supplier that goes lower
MAX_AIM_BELOW_FLOOR_PCT = 5
# a cost estimate this many times off the case's own price level is in another unit (per 100, per kg): ignore it
MAX_COST_TO_PRICE_RATIO = 3.0
PRICE_STEPS = 40  # price grid per package between its feasible minimum and the ceiling
MAX_PACKAGES = 300  # option combinations searched per supplier; above that a fixed-seed sample
MIN_PRICE = 0.01
# terms whose cost to the supplier we cannot model (supplier_value has no estimate of its overtime for faster
# delivery or of what a tighter defect commitment costs it): kept at its terms in the target - asking for
# them is a trade the strategist makes explicitly, not a silent demand that raises its price
HOLD_AT_STANDARD = ("lead_time_days", "ppm_commitment")


def alternative_cost(case: dict) -> float | None:
    """C of what we get without a deal, or None if there is no priced alternative."""
    alt = case["buyer"].get("alternative") or {}
    if alt.get("kind") in (None, "none") or not alt.get("terms", {}).get("unit_price"):
        return None
    return total_cost(case, alt["terms"])


def supplier_estimate(case: dict, package, supplier_id: str, terms: dict, std: dict) -> dict | None:
    """Our estimate of the supplier for `cost.supplier_value` / `relationship_cost`, or None."""
    if package is None or len(case["parts"]) != 1:
        return None
    cost = estimates.full_cost(package, case["parts"][0], supplier_id)
    if cost is None or not _same_scale(cost.value, terms["unit_price"]):
        return None
    standard = {k: v for k, v in closing_terms(case, terms, std).items() if k != "unit_price"}
    rate = estimates.cost_of_money(package, supplier_id).value
    return {"full_cost": cost.value, "interest_rate": rate, "standard": standard}


def _same_scale(cost: float, price: float) -> bool:
    return price > 0 and 1 / MAX_COST_TO_PRICE_RATIO <= cost / price <= MAX_COST_TO_PRICE_RATIO


def price_bands(case: dict, threads: dict[str, list[dict]], package) -> dict[str, PriceBand]:
    quotes = {sid: standing(ev)[1] for sid, ev in threads.items()}
    out = {}
    for sid, events in threads.items():
        terms = quotes[sid]
        if terms.get("unit_price") is not None:  # no bid: no band
            rivals = [t["unit_price"] for s, t in quotes.items() if s != sid and t.get("unit_price") is not None]
            out[sid] = supplier_band(case, package, sid, terms, standard_terms(events), rivals)
    return out


def supplier_band(case: dict, package, sid: str, terms: dict, std: dict, rivals=()) -> PriceBand:
    supplier = supplier_estimate(case, package, sid, terms, std)
    # what it left out of its term sheet closes on its standard terms (quote PDF), not on our wishes
    stated = {field: std[field] for field in case["negotiable"] if field in std}
    opening = complete_offer(case, {**stated, **terms})
    current = case["buyer"].get("current_unit_price") or terms["unit_price"]
    fair = estimates.fair_price(package, case["parts"][0], current) if package else None
    ceiling, c_ceiling = _ceiling(case, opening, std, supplier)
    floor, lowest_plausible, why = _floor(case, package, sid, supplier)
    best = _best_package(case, opening, std, supplier, lowest_plausible, ceiling) if floor else None
    if best is None:
        price, reason = _anchor_without_cost(case, terms["unit_price"], fair, rivals, ceiling)
        best = complete_offer(case, {**opening, "unit_price": price})
        why.append(f"target without a cost estimate: {reason}")
    fair_why = f"fair: {fair.source}" if fair else "fair: no cost drivers"
    return PriceBand(
        supplier_id=sid,
        floor=floor,
        fair=fair.value if fair else None,
        target=best["unit_price"],
        ceiling=ceiling,
        c_target=total_cost(case, best, std, supplier),
        c_ceiling=c_ceiling,
        derivation=(*why, fair_why, f"ceiling: {ceiling}"),
        target_terms=best,
    )


def _anchor_without_cost(case: dict, opening: float, fair, rivals, ceiling: float) -> tuple[float, str]:
    """Without a cost estimate: the lowest price the case itself justifies - the cost drivers' fair price,
    today's price (no increase without a reason), the cheapest other bidder, our fallback - else a share of
    the opening, said plainly, for the concession fit to correct."""
    b = case["buyer"]
    anchors = [
        (fair.value if fair else None, "fair price from the part's cost drivers"),
        (b.get("current_unit_price"), "today's price: no increase without a reason"),
        (min(rivals) if rivals else None, "the cheapest other bidder's quote"),
        (b.get("fallback_unit_price"), "our fallback price"),
    ]
    usable = [(price, why) for price, why in anchors if price and price < ceiling]
    if usable:
        return min(usable)
    share = round(min(opening, ceiling) * UNINFORMED_TARGET_SHARE, 2)
    return share, f"no anchor in the data: {1 - UNINFORMED_TARGET_SHARE:.0%} under the opening until it moves"


def _floor(case: dict, package, sid: str, supplier: dict | None) -> tuple[float | None, float | None, list[str]]:
    """(floor, lowest plausible floor, why): the estimate, and its low end given the estimate's uncertainty -
    the target may aim there, never below."""
    if supplier is None:
        return None, None, ["floor: no cost estimate"]
    cost = estimates.full_cost(package, case["parts"][0], sid)
    rate = estimates.cost_of_money(package, sid)
    why = [f"full cost {cost.value} +-{cost.uncertainty_pct}%: {cost.source}", f"cost of money: {rate.source}"]
    floor = cost.value
    if estimates.accepts_below_cost(package, sid):
        why.append(f"history: accepted below full cost - floor {BELOW_COST_TOLERANCE:.0%} under it")
        floor = cost.value * (1 - BELOW_COST_TOLERANCE)
    # a wide uncertainty means a weak estimate, not a supplier that goes lower: aim at most 5 % under it
    return floor, floor * (1 - min(cost.uncertainty_pct, MAX_AIM_BELOW_FLOOR_PCT) / 100), why


def _ceiling(case: dict, opening: dict, std: dict, supplier: dict | None) -> tuple[float, float]:
    """(price, C): the opening, or the price on the opening's terms that costs what our alternative does."""
    c_open = total_cost(case, opening, std, supplier)
    c_alt = alternative_cost(case)
    if c_alt is None or c_alt >= c_open:
        return opening["unit_price"], c_open
    # C without the relationship term is linear in price: solve it from two points
    c1, c2 = (total_cost(case, {**opening, "unit_price": p}, std) for p in (1.0, 2.0))
    price = 1.0 + (c_alt - c1) / (c2 - c1)
    return round(max(price, 0.01), 2), c_alt


def _packages(case: dict, opening: dict):
    """The case's allowed option combinations for the negotiable fields (price set later): all of them, or a
    fixed-seed sample of MAX_PACKAGES in which every option of every field appears at least once."""
    fields = [
        f
        for f in case["negotiable"]
        if f != "unit_price" and f not in HOLD_AT_STANDARD and case.get("term_options", {}).get(f)
    ]
    options = [case["term_options"][f] for f in fields]
    if math.prod(len(o) for o in options) <= MAX_PACKAGES:
        combos = list(itertools.product(*options))
    else:
        rng = random.Random(0)
        width = max(len(o) for o in options)
        combos = [tuple(o[i % len(o)] for o in options) for i in range(width)]  # every option once
        combos += [tuple(rng.choice(o) for o in options) for _ in range(MAX_PACKAGES - width)]
    for combo in combos:
        yield complete_offer(case, {**opening, **dict(zip(fields, combo, strict=True))})


def _best_package(case: dict, opening: dict, std: dict, supplier: dict, floor: float, ceiling: float) -> dict | None:
    """The package with the lowest C (incl. relationship) whose cash value to the supplier covers its floor."""
    best, best_c = None, math.inf
    for package in _packages(case, opening):
        if not must_haves_ok(case, package, std):
            continue
        v1, v2 = (supplier_value(case, {**package, "unit_price": p}, supplier) for p in (1.0, 2.0))
        lowest = 1.0 + (floor - v1) / (v2 - v1) if v2 != v1 else ceiling
        lowest = min(max(lowest, MIN_PRICE), ceiling)  # positive; even a squeezed supplier gets the ceiling
        step = (ceiling - lowest) / PRICE_STEPS
        for i in range(PRICE_STEPS + 1):
            candidate = complete_offer(case, {**package, "unit_price": math.ceil((lowest + i * step) * 100) / 100})
            c = total_cost(case, candidate, std, supplier)
            if c < best_c:
                best, best_c = candidate, c
    return best
