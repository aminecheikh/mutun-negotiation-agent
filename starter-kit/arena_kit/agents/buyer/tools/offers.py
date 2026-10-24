"""The offer engine: 1-3 complete term sheets per round (owner: Amine).

PLACEHOLDER: one sheet on the supplier's standing terms, price conceding from the band's target toward
its standing price on a Boulware curve (slow early, fast late). To build: equal-C sheets that trade
price against payment days / effective date / volume / contract length, index clauses only when they
lower C, and concessions shaped by the behaviour read.
"""

from ....cost import complete_offer, total_cost
from ....docs import standard_terms
from ....play import moves_left, standing
from ..contracts import BehaviourRead, CaseContext, OfferPlan, Tactic

BOULWARE_EXPONENT = 3.0  # > 1: hold near the target early, concede late


def concession_share(case: dict, events: list[dict]) -> float:
    """0 at our first message, 1 at our last allowed one."""
    limit = case.get("round_limit", 10)
    used = limit - moves_left(case, events)
    return min(1.0, used / max(limit - 1, 1)) ** BOULWARE_EXPONENT


def plan(ctx: CaseContext, supplier_id: str, events: list[dict], tactic: Tactic, read: BehaviourRead) -> OfferPlan:
    case = ctx.case
    _, terms = standing(events)
    band = ctx.bands.get(supplier_id)
    theirs = terms.get("unit_price")
    if band is None or theirs is None:
        sheet = complete_offer(case, terms)
        return OfferPlan(supplier_id, (sheet,), (total_cost(case, sheet),), "no band: echo the standing terms")
    price = band.target + (theirs - band.target) * concession_share(case, events)
    sheet = complete_offer(case, {**terms, "unit_price": min(price, theirs)})
    c = total_cost(case, sheet, standard_terms(events))
    return OfferPlan(supplier_id, (sheet,), (c,), f"placeholder Boulware: {sheet['unit_price']} vs theirs {theirs}")
