"""The supplier's price path inside the negotiation, and where it will stop (owner: Amine).

Bayesian grid over its reservation price R (Zeng & Sycara 1998: learn the other side's reservation value from
its offers). Likelihood: its prices under the time-dependent concession curve
p_t = R + (p_0 - R) * (1 - (t / T) ** (1 / beta)) (Faratin, Sierra & Jennings 1998), averaged over beta from
Boulware to Conceder. Prior: normal around our cost-floor estimate, flat without one. Its own moves outweigh a
wrong prior after a few rounds; R never exceeds a price it has already offered.
"""

import math

from ..contracts import ConcessionFit

BETAS = (0.2, 0.5, 1.0, 2.0)  # Boulware ... Conceder, the range used in the time-dependent-tactics literature
NOISE_PCT = 0.5  # spread of its prices around the curve, % of its opening: rounding and its own discretion
PRIOR_SD_PCT = 10.0  # spread of the prior around our floor: should-cost errs 5-15 % (data README, company.md)
ROOM_PCT = 1.0  # "room left" means at least this % below its last price
GRID = 120
GRID_LOW = 0.5  # the grid reaches down to half the lowest of floor and its prices


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


def fit(supplier_id: str, events: list[dict], floor: float | None = None, round_limit: int = 10) -> ConcessionFit:
    prices = supplier_prices(events)
    steps = [a - b for a, b in zip(prices, prices[1:], strict=False)]
    last_step = steps[-1] / prices[-2] * 100 if steps and prices[-2] else None
    shrinking = len(steps) >= 2 and all(later <= earlier for earlier, later in zip(steps, steps[1:], strict=False))
    if not prices:
        return ConcessionFit(supplier_id, prices, None, last_step, shrinking)
    if len(prices) < 2:  # only its opening: the prior is all we have
        estimate = min(floor, prices[0]) if floor else None
        return ConcessionFit(supplier_id, prices, estimate, last_step, shrinking)
    reservation, p_room = _posterior(prices, floor, round_limit)
    return ConcessionFit(supplier_id, prices, reservation, last_step, shrinking, p_room)


def _posterior(prices: tuple[float, ...], floor: float | None, round_limit: int) -> tuple[float, float]:
    """(posterior mean of R, P(R <= last price - ROOM_PCT %)) on a grid below its lowest price."""
    top = min(prices)
    low = GRID_LOW * min(top, floor or top)
    grid = [low + (top - low) * i / (GRID - 1) for i in range(GRID)]
    logs = [_log_prior(r, floor) + _log_likelihood(r, prices, round_limit) for r in grid]
    peak = max(logs)
    weights = [math.exp(v - peak) for v in logs]
    total = sum(weights)
    mean = sum(r * w for r, w in zip(grid, weights, strict=True)) / total
    room_below = prices[-1] * (1 - ROOM_PCT / 100)
    p_room = sum(w for r, w in zip(grid, weights, strict=True) if r <= room_below) / total
    return round(mean, 2), round(p_room, 3)


def _log_prior(r: float, floor: float | None) -> float:
    if not floor:
        return 0.0
    sd = floor * PRIOR_SD_PCT / 100
    return -0.5 * ((r - floor) / sd) ** 2


def _log_likelihood(r: float, prices: tuple[float, ...], round_limit: int) -> float:
    """log of the mean likelihood of its prices after the opening, over its unknown concession speed (beta)
    and its unknown own deadline (2 .. round limit rounds; after it, it holds at R)."""
    sd = prices[0] * NOISE_PCT / 100
    logs = []
    for beta in BETAS:
        for horizon in range(2, max(round_limit, 2) + 1):
            sq = 0.0
            for t, p in enumerate(prices[1:], 1):
                share = min(t / horizon, 1.0) ** (1 / beta)
                sq += (p - (r + (prices[0] - r) * (1 - share))) ** 2
            logs.append(-0.5 * sq / sd**2)
    peak = max(logs)
    return peak + math.log(sum(math.exp(v - peak) for v in logs) / len(logs))
