"""What the data says about things the supplier does not tell us (owner: Amine).

Pure functions over the data package. Every estimate carries its value, the source in words (for the
prompts and the pitch) and an uncertainty. Method and sources: docs/team/amine-estimation-plan.md.
"""

from dataclasses import dataclass

OUR_RATE = 0.08  # company.md: Hollerau's cost of capital, the default for a supplier
MAX_RATE = 0.5  # a skonto can imply absurd rates; cap what we feed into supplier_value
TYPICAL_MARGIN = 0.08  # assumed when only market prices bound the cost
COUNTRY_FACTOR = {"DE": 1.0, "AT": 1.05, "CH": 1.05, "CZ": 0.82, "PL": 0.82, "HU": 0.82, "TR": 0.75, "CN": 0.7}
OWN_PLANT_UNCERTAINTY, SCALED_UNCERTAINTY, PRICE_UNCERTAINTY = 5, 15, 20
PRICES_SINCE = "2025-01-01"
# cost-of-money premia in rate points: (credit index above, points), worsening credit, thin equity
CREDIT_PREMIA = ((300, 0.08), (250, 0.04))
WORSENING_PREMIUM, THIN_EQUITY_PREMIUM, THIN_EQUITY_PCT = 0.04, 0.02, 25.0


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


def _country(package, supplier_id: str) -> str | None:
    rows = package.query("SELECT country FROM suppliers WHERE id = ?", (supplier_id,))
    return rows[0]["country"] if rows else None


def full_cost(package, part_id: str, supplier_id: str) -> Estimate | None:
    """The supplier's full cost per price unit: its own should-cost, else another should-cost scaled to its
    country, else the lowest recent market price minus a typical margin."""
    rows = package.query("SELECT id, supplier_id, unit_cost, cost_base FROM should_cost WHERE part_id = ?", (part_id,))
    own = [r for r in rows if r["supplier_id"] == supplier_id]
    if own:
        r = own[0]
        return Estimate(r["unit_cost"], f"should-cost of {supplier_id}'s plant ({r['id']})", OWN_PLANT_UNCERTAINTY)
    country = _country(package, supplier_id) or "DE"
    if rows:
        r = next((r for r in rows if not r["supplier_id"]), rows[0])
        factor = COUNTRY_FACTOR.get(country, 1.0) / COUNTRY_FACTOR.get(r["cost_base"], 1.0)
        source = f"{r['id']} ({r['cost_base']} base) x {factor:.2f} for {country}"
        return Estimate(round(r["unit_cost"] * factor, 2), source, SCALED_UNCERTAINTY)
    return _cost_from_prices(package, part_id)


def _cost_from_prices(package, part_id: str) -> Estimate | None:
    prices = package.query(
        "SELECT price FROM price_conditions WHERE part_id = ? AND valid_from >= ? "
        "UNION ALL SELECT unit_price FROM quotes WHERE part_id = ? AND quote_date >= ?",
        (part_id, PRICES_SINCE, part_id, PRICES_SINCE),
    )
    values = [p["price"] for p in prices if p["price"]]
    if not values:
        return None
    lowest = min(values)
    source = f"lowest price since {PRICES_SINCE} ({lowest}) minus {TYPICAL_MARGIN:.0%} margin"
    return Estimate(round(lowest * (1 - TYPICAL_MARGIN), 2), source, PRICE_UNCERTAINTY)


def cost_of_money(package, supplier_id: str) -> Estimate:
    """What money now is worth to the supplier per year: its own skonto's implied rate if it offers one,
    else our rate plus premia for weak or worsening credit and thin equity."""
    rows = package.query("SELECT payment_days, skonto_days, skonto_pct FROM suppliers WHERE id = ?", (supplier_id,))
    s = rows[0] if rows else {}
    pct, days, net = s.get("skonto_pct"), s.get("skonto_days"), s.get("payment_days")
    if pct and days is not None and net and net > days:
        implied = pct / (100 - pct) * 360 / (net - days)
        source = f"its skonto {pct:g}% within {days} days, net {net}: implied {implied:.0%} a year"
        return Estimate(round(min(implied, MAX_RATE), 4), source)
    intel = package.query("SELECT * FROM supplier_intel WHERE supplier_id = ?", (supplier_id,))
    premia = _rate_premia(intel[0]) if intel else []
    rate = OUR_RATE + sum(p for p, _ in premia)
    reasons = "; ".join(r for _, r in premia) or "no sign of cash pressure"
    return Estimate(round(min(rate, MAX_RATE), 4), f"our {OUR_RATE:.0%} + premia: {reasons}")


def _rate_premia(intel) -> list[tuple[float, str]]:
    premia = []
    ci, ci_before, equity = intel["credit_index"], intel["credit_index_12m_ago"], intel["equity_ratio_pct"]
    if ci is not None:
        hit = next(((limit, p) for limit, p in CREDIT_PREMIA if ci > limit), None)
        if hit:
            premia.append((hit[1], f"credit index {ci} > {hit[0]}"))
        if ci_before is not None and ci > ci_before:
            premia.append((WORSENING_PREMIUM, f"credit index worsened {ci_before} -> {ci}"))
    if equity is not None and equity < THIN_EQUITY_PCT:
        premia.append((THIN_EQUITY_PREMIUM, f"equity ratio {equity}% < {THIN_EQUITY_PCT:g}%"))
    return premia


def accepts_below_cost(package, supplier_id: str) -> bool:
    """Past renewals note it 'accepted below full cost to keep the volume': its floor may sit under cost."""
    rows = package.query(
        "SELECT COUNT(*) AS n FROM negotiation_history WHERE supplier_id = ? AND note LIKE '%below full cost%'",
        (supplier_id,),
    )
    return bool(rows and rows[0]["n"])
