"""How low an offer may go and still be taken seriously (owner: Amine).

An offer far under the supplier's price that our evidence cannot back reads as a lowball: practice sweep 4c30c05
opened 25-28 % under the supplier wherever our cost estimate was a rough guess (P04, P07, P08, P10, P18), and P10
answered with a final offer at once ("too ambitious -> impasse", research 02). The lowest credible price is tied
to two things:
  our evidence      firm (its own breakdown, a plant should-cost: uncertainty <= 5 %) backs offers down to the
                    low end of its floor, the same aim the bands allow
  its current offer with a rough estimate we stay within the estimate's own error under its current offer; with
                    no cost evidence at all, within the 8 % the bands already use for "no anchor in the data"
It is recomputed every round from the supplier's latest price, so it follows the supplier down.
"""

from ..contracts import Credibility
from .bands import MAX_AIM_BELOW_FLOOR_PCT, UNINFORMED_TARGET_SHARE

FIRM_EVIDENCE_PCT = MAX_AIM_BELOW_FLOOR_PCT  # bands: above this uncertainty an estimate is weak
NO_EVIDENCE_GAP_PCT = round((1 - UNINFORMED_TARGET_SHARE) * 100, 6)  # bands: 8 % under the opening without data


def assess(supplier_id: str, floor: float | None, supplier_price: float, estimate) -> Credibility:
    """The lowest price an offer to this supplier may carry, and why."""
    if estimate is None or not floor:
        lowest = supplier_price * (1 - NO_EVIDENCE_GAP_PCT / 100)
        return Credibility(supplier_id, round(lowest, 2), None, f"no cost evidence: at most {NO_EVIDENCE_GAP_PCT:g} % "
                           "under its current offer")  # fmt: skip
    pct = estimate.uncertainty_pct
    floor_low = floor * (1 - min(pct, MAX_AIM_BELOW_FLOOR_PCT) / 100)
    if pct <= FIRM_EVIDENCE_PCT:
        lowest, why = floor_low, f"firm cost evidence (+-{pct:g} %, {estimate.source}): down to its floor"
    else:
        lowest = max(floor_low, supplier_price * (1 - pct / 100))
        why = f"rough cost estimate (+-{pct:g} %, {estimate.source}): at most {pct:g} % under its current offer"
    return Credibility(supplier_id, round(min(lowest, supplier_price), 2), pct, why)
