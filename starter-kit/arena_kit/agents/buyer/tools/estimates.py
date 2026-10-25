"""What the data says about things the supplier does not tell us (owner: Amine).

Pure functions over the data package. Every estimate carries its value, its source in words (for the prompts and
the pitch) and an uncertainty.

  supplier_estimate  our estimate of the supplier for the published cost function's squeeze charge: the latest
                     should-cost of the part for this supplier (no margin), valued at our own cost of capital
  money_rate         what money is worth to the supplier per year, from what it showed: two of its own sheets that
                     differ only in payment days, else the skonto it offers; None without such evidence
  real_ppm           the defect rate the supplier really delivers (its latest scorecard), which prices a chargeback
                     for it; None without a scorecard
  fair_price         a base price moved by the part's cost-driver indices over 12 months
"""

import statistics
from dataclasses import dataclass

DEFAULT_UNCERTAINTY_PCT = 15  # a should-cost row without a stated confidence
# Creditreform-style credit index: above these a supplier's credit is weak (dossier evidence lines)
CREDIT_PREMIA = ((300, 0.08), (250, 0.04))
THIN_EQUITY_PCT = 25.0
MONEY_RATE_BOUNDS = (0.0, 0.6)  # a revealed rate outside this is a different trade, not a payment price
TERMS = ("lead_time_days", "volume_per_year", "contract_months", "effective_date", "index_clause", "incoterm",
         "ppm_commitment", "chargeback_eur_per_complaint", "tooling_eur")  # fmt: skip


@dataclass(frozen=True)
class Estimate:
    value: float
    source: str
    uncertainty_pct: float = 0.0


@dataclass(frozen=True)
class IndexChange:
    series: str
    earlier_id: str
    later_id: str
    earlier: float
    later: float
    pct: float


def should_cost(package, case: dict, supplier_id: str) -> Estimate | None:
    """The latest should-cost row of the case's one part for this supplier (or for any supplier), in the part's own
    price unit. A bundle of several parts has no single unit cost: None."""
    parts = case.get("parts") or []
    if package is None or len(parts) != 1:
        return None
    rows = package.query(
        "SELECT s.id, s.unit_cost, s.price_unit, s.confidence_pct, s.estimate_date, p.price_unit AS part_unit "
        "FROM should_cost s LEFT JOIN parts p ON p.id = s.part_id "
        "WHERE s.part_id = ? AND (s.supplier_id = ? OR s.supplier_id IS NULL OR s.supplier_id = '') "
        "ORDER BY s.estimate_date DESC",
        (parts[0], supplier_id),
    )
    row = rows[0] if rows else None
    if row is None or row["unit_cost"] is None:
        return None
    if row["part_unit"] is not None and float(row["price_unit"]) != float(row["part_unit"]):
        return None
    pct = float(row["confidence_pct"] or DEFAULT_UNCERTAINTY_PCT)
    return Estimate(float(row["unit_cost"]), f"should-cost {row['id']} ({row['estimate_date']})", pct)


def supplier_estimate(package, case: dict, supplier_id: str, std: dict) -> dict | None:
    """`supplier` for cost.supplier_value / relationship_cost: its full cost from the should-cost, our interest rate,
    its standard terms. None without a should-cost (C then leaves the squeeze charge out)."""
    estimate = should_cost(package, case, supplier_id)
    if estimate is None:
        return None
    return {
        "full_cost": estimate.value,
        "interest_rate": case["buyer"]["interest_rate"],
        "standard": std,
        "source": estimate.source,
        "uncertainty_pct": estimate.uncertainty_pct,
    }


def money_rate(package, supplier_id: str, events=()) -> float | None:
    """What money is worth to the supplier per year (the brief: payment timing counts "at what money is worth to
    it"). Its own sheets in this thread that differ only in payment days price the days directly; else a skonto
    in the data (x % within d days, net n: x / (n - d) * 360)."""
    revealed = []
    for m in events:
        if m.get("from") == "buyer":
            continue
        sheets = [s.get("terms") or {} for s in m.get("term_sheets") or []]
        for i, a in enumerate(sheets):
            for b in sheets[i + 1 :]:
                days = (a.get("payment_days") or 0) - (b.get("payment_days") or 0)
                if not days or not a.get("unit_price") or not b.get("unit_price"):
                    continue
                if any(a.get(f) != b.get(f) for f in TERMS):
                    continue
                share = (a["unit_price"] - b["unit_price"]) / ((a["unit_price"] + b["unit_price"]) / 2)
                rate = share / days * 360
                if MONEY_RATE_BOUNDS[0] < rate <= MONEY_RATE_BOUNDS[1]:
                    revealed.append(rate)
    if revealed:
        return statistics.median(revealed)
    if package is None:
        return None
    rows = package.query("SELECT skonto_pct, skonto_days, payment_days FROM suppliers WHERE id = ?", (supplier_id,))
    for r in rows:
        try:
            pct, within, net = float(r["skonto_pct"]), float(r["skonto_days"]), float(r["payment_days"])
        except (TypeError, ValueError, KeyError):
            continue
        if net > within and pct > 0:
            return min(MONEY_RATE_BOUNDS[1], pct / 100 / (net - within) * 360)
    return None


def real_ppm(package, case: dict, supplier_id: str) -> float | None:
    """Its latest scorecard's ppm (the case's own facts first, then the package): the brief values a chargeback for
    the supplier "at the defect rate it really has"."""
    facts = {**(package.facts if package is not None else {}), **(case.get("facts") or {})}
    cards = [f for ref, f in facts.items() if ref.startswith("sc:") and f.get("supplier") == supplier_id]
    latest = max(cards, key=lambda f: f.get("quarter", ""), default={})
    return float(latest["ppm"]) if latest.get("ppm") is not None else None


def index_change(package, series_id: str) -> IndexChange | None:
    """The change over the latest 12 months of a series, as the arena checks a claim on two idx IDs."""
    rows = package.query(
        "SELECT id, month, value FROM index_values WHERE series_id = ? ORDER BY month DESC", (series_id,), limit=13
    )
    if not rows:
        return None
    later = rows[0]
    year_before = f"{int(later['month'][:4]) - 1}{later['month'][4:]}"
    earlier = next((r for r in rows if r["month"] == year_before), None)
    if earlier is None:
        return None
    pct = (later["value"] / earlier["value"] - 1) * 100
    return IndexChange(series_id, earlier["id"], later["id"], earlier["value"], later["value"], pct)


def fair_price(package, part_id: str, base_price: float) -> Estimate | None:
    """The base price moved by the part's cost drivers over the last 12 months; the rest of the cost held."""
    drivers = package.query("SELECT series_id, weight FROM cost_drivers WHERE part_id = ?", (part_id,))
    moves = [(d, index_change(package, d["series_id"])) for d in drivers]
    moves = [(d, c) for d, c in moves if c is not None]
    if not moves:
        return None
    change = sum(d["weight"] * c.pct for d, c in moves) / 100
    parts = ", ".join(f"{c.series} {c.pct:+.1f}% x {d['weight']}" for d, c in moves)
    return Estimate(round(base_price * (1 + change), 2), f"{base_price} moved {change:+.2%} by {parts}")
