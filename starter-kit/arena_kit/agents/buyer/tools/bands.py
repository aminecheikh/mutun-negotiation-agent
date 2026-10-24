"""Price bands per supplier: floor, fair, target, ceiling (owner: Amine).

PLACEHOLDER: target = 92 % of the opening price, ceiling = the opening price capped at the fallback
price. To build: floor from should_cost (country factor), fair from index pass-through, ceiling from the
alternative / rival quotes / C_open, target minimising C incl. the relationship term
(`cost.relationship_cost`) in stabilise and secure_supply cases. See skill `data-package`.
"""

import math

from ....cost import total_cost
from ....docs import standard_terms
from ....play import standing
from ..contracts import PriceBand

PLACEHOLDER_TARGET_SHARE = 0.92


def alternative_cost(case: dict) -> float | None:
    """C of what we get without a deal, or None if there is no priced alternative."""
    alt = case["buyer"].get("alternative") or {}
    if alt.get("kind") in (None, "none") or not alt.get("terms", {}).get("unit_price"):
        return None
    return total_cost(case, alt["terms"])


def price_bands(case: dict, threads: dict[str, list[dict]], package) -> dict[str, PriceBand]:
    out = {}
    for sid, events in threads.items():
        _, terms = standing(events)
        opening = terms.get("unit_price")
        if opening is None:  # no bid: no band
            continue
        std = standard_terms(events)
        fallback = case["buyer"].get("fallback_unit_price") or math.inf
        ceiling = min(opening, fallback)
        target = round(opening * PLACEHOLDER_TARGET_SHARE, 2)
        out[sid] = PriceBand(
            supplier_id=sid,
            floor=None,
            fair=None,
            target=target,
            ceiling=ceiling,
            c_target=total_cost(case, {**terms, "unit_price": target}, std),
            c_ceiling=total_cost(case, {**terms, "unit_price": ceiling}, std),
            derivation=("placeholder: target 92 % of opening, ceiling = opening capped at fallback",),
        )
    return out
