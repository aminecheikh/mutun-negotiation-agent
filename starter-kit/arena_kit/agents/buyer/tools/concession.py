"""The supplier's price path inside the negotiation (owner: Amine).

Prices and step sizes are real; `reservation_estimate` is a PLACEHOLDER (None). To build: fit
p_t = R + (p_0 - R) * exp(-k t) with a prior on R from the band's floor and the negotiation history.
"""

from ..contracts import ConcessionFit


def supplier_prices(events: list[dict]) -> tuple[float, ...]:
    """The unit price of each supplier term sheet in the thread, oldest first."""
    prices = []
    for m in events:
        if m.get("from") == "buyer":
            continue
        for sheet in (m.get("term_sheets") or [])[:1]:
            if (p := sheet.get("terms", {}).get("unit_price")) is not None:
                prices.append(float(p))
    return tuple(prices)


def fit(supplier_id: str, events: list[dict]) -> ConcessionFit:
    prices = supplier_prices(events)
    steps = [a - b for a, b in zip(prices, prices[1:], strict=False)]
    last_step = steps[-1] / prices[-2] * 100 if steps else None
    shrinking = len(steps) >= 2 and all(later <= earlier for earlier, later in zip(steps, steps[1:], strict=False))
    return ConcessionFit(supplier_id, prices, None, last_step, shrinking)
