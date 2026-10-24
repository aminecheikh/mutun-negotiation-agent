"""What the supplier tells us itself (owner: Amine) - the best data about a supplier we have never met.

- Cost drivers and shares from its e-mails ("LCI-C rose 2.7 % in twelve months and is 30 % of our cost"): its
  own cost structure. `stated_fair_price` prices it with *our* verified index changes, not its figures.
- A cost breakdown sheet (it sends one when asked and open enough): its full cost per unit, exactly.
Pure functions over the thread; series are only accepted when the data package knows them.
"""

import re
from dataclasses import dataclass, field

from . import estimates

DRIVER = re.compile(
    r"\b(?P<series>(?-i:[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*))\s+(?:rose|fell|increased|decreased|changed|went up|went down)\b"
    r"(?:[^.\n]|\.(?=\d))*?\bis\s+(?P<share>\d+(?:\.\d+)?)\s*%\s+of\s+our\s+costs?",
    re.IGNORECASE,
)
BREAKDOWN_ROW = re.compile(r"^\s*(?P<element>[^|]+?)\s*\|\s*(?P<value>-?\d+(?:\.\d+)?)\s*\|", re.MULTILINE)
NOT_COST = re.compile(r"margin|profit|price|total|sum", re.IGNORECASE)  # rows that are not cost elements


@dataclass(frozen=True)
class SupplierSignals:
    driver_shares: dict[str, float] = field(default_factory=dict)  # series -> share of its cost (0..1)
    breakdown_cost: float | None = None  # full cost per unit from its cost breakdown
    breakdown_price: float | None = None  # the unit price that breakdown adds up to


def read(events: list[dict], package=None) -> SupplierSignals:
    """Everything it stated in this thread; later statements overwrite earlier ones."""
    known = _known_series(package)
    shares, cost, price = {}, None, None
    for message in events:
        if message.get("from") == "buyer":
            continue
        for m in DRIVER.finditer(message.get("body") or ""):
            series = m["series"].upper()
            if known is None or series in known:
                shares[series] = round(float(m["share"]) / 100, 4)
        for attachment in message.get("attachments") or []:
            if "breakdown" in (attachment.get("name") or "").lower() and attachment.get("text"):
                cost, price = _breakdown(attachment["text"]) or (cost, price)
    return SupplierSignals(shares, cost, price)


def _breakdown(text: str) -> tuple[float, float] | None:
    """(full cost, price) from a cost breakdown sheet: cost elements summed, margin on top."""
    rows = [(r["element"], float(r["value"])) for r in BREAKDOWN_ROW.finditer(text)]
    costs = [v for name, v in rows if not NOT_COST.search(name)]
    if not costs:
        return None
    margin = sum(v for name, v in rows if re.search(r"margin|profit", name, re.IGNORECASE))
    return round(sum(costs), 4), round(sum(costs) + margin, 4)


def _known_series(package) -> set[str] | None:
    if package is None:
        return None
    return {r["id"] for r in package.query("SELECT id FROM index_series")}


def stated_fair_price(package, base_price: float, shares: dict[str, float]) -> estimates.Estimate | None:
    """The base price moved by the supplier's own cost shares and our verified 12-month index changes."""
    moves = [(s, share, estimates.index_change(package, s)) for s, share in shares.items()]
    moves = [(s, share, c) for s, share, c in moves if c is not None]
    if not moves:
        return None
    change = sum(share * c.pct for _, share, c in moves) / 100
    parts = ", ".join(f"{s} {c.pct:+.1f}% x {share}" for s, share, c in moves)
    return estimates.Estimate(round(base_price * (1 + change), 2), f"its own stated cost shares: {parts}")
