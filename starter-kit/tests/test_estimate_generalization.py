"""Synthetic evidence: estimates must survive new suppliers, units and document order."""

import copy
import sqlite3

import pytest

from arena_kit.agents.buyer.tools import DataTools, bands, estimates, signals
from arena_kit.data import DataPackage


@pytest.fixture
def package():
    package = DataPackage.__new__(DataPackage)
    package.db = sqlite3.connect(":memory:")
    package.db.row_factory = sqlite3.Row
    package.db.executescript("""
        CREATE TABLE parts (id TEXT, price_unit REAL);
        CREATE TABLE suppliers (id TEXT, country TEXT);
        CREATE TABLE should_cost (
            id TEXT, part_id TEXT, supplier_id TEXT, unit_cost REAL, cost_base TEXT,
            price_unit REAL, estimate_date TEXT
        );
        CREATE TABLE price_conditions (
            supplier_id TEXT, part_id TEXT, price REAL, price_unit REAL, currency TEXT, valid_from TEXT
        );
        CREATE TABLE quotes (
            supplier_id TEXT, part_id TEXT, unit_price REAL, price_unit REAL, currency TEXT, quote_date TEXT
        );
        INSERT INTO parts VALUES ('part:unseen', 100);
        INSERT INTO suppliers VALUES ('sup:unseen', 'DE');
    """)
    yield package
    package.db.close()


@pytest.mark.parametrize("unit", [1, 10, 100, 1000])
def test_should_cost_is_in_the_parts_price_unit(package, unit):
    package.db.execute(
        "INSERT INTO should_cost VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("cost:unseen", "part:unseen", "sup:unseen", 2 * unit, "DE", unit, "2026-08-01"),
    )
    assert estimates.full_cost(package, "part:unseen", "sup:unseen").value == 200


@pytest.mark.parametrize("table", ["quotes", "price_conditions"])
@pytest.mark.parametrize("unit", [1, 10, 100, 1000])
def test_market_cost_is_invariant_to_quote_price_unit(package, table, unit):
    package.db.execute(
        f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?, ?)",
        ("sup:unseen", "part:unseen", 2 * unit, unit, "EUR", "2026-08-01"),
    )
    assert estimates.full_cost(package, "part:unseen", "sup:unseen").value == pytest.approx(200 * 0.92)


@pytest.mark.parametrize("reverse", [False, True])
def test_latest_own_cost_wins_independent_of_row_order(package, reverse):
    rows = [
        ("cost:old", "part:unseen", "sup:unseen", 180, "DE", 100, "2025-01-01"),
        ("cost:new", "part:unseen", "sup:unseen", 205, "DE", 100, "2026-09-01"),
        ("cost:generic", "part:unseen", None, 150, "DE", 100, "2026-10-01"),
    ]
    package.db.executemany("INSERT INTO should_cost VALUES (?, ?, ?, ?, ?, ?, ?)", rows[::-1] if reverse else rows)
    cost = estimates.full_cost(package, "part:unseen", "sup:unseen")
    assert cost.value == 205 and "cost:new" in cost.source


@pytest.mark.parametrize(
    "price, unit, currency",
    [(1, 100, "USD"), (1, 0, "EUR"), (-1, 100, "EUR"), (1, None, "EUR"), (float("inf"), 100, "EUR")],
)
def test_incompatible_market_rows_cannot_undercut_valid_evidence(package, price, unit, currency):
    package.db.executemany(
        "INSERT INTO quotes VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("sup:unseen", "part:unseen", price, unit, currency, "2026-08-01"),
            ("sup:unseen", "part:unseen", 200, 100, "EUR", "2026-08-01"),
        ],
    )
    assert estimates.full_cost(package, "part:unseen", "sup:unseen").value == pytest.approx(184)


def test_unknown_part_unit_does_not_invent_a_cost_basis(package):
    package.db.execute("DELETE FROM parts")
    package.db.execute("INSERT INTO quotes VALUES ('sup:unseen', 'part:unseen', 2, 1, 'EUR', '2026-08-01')")
    assert estimates.full_cost(package, "part:unseen", "sup:unseen") is None


@pytest.mark.parametrize("shares", [{"x": 1.2}, {"x": -0.1}, {"x": 0.8, "y": 0.4}, {"x": float("nan")}])
def test_invalid_cost_shares_are_not_used_as_price_evidence(monkeypatch, shares):
    monkeypatch.setattr(estimates, "index_change", lambda *_: estimates.IndexChange("x", "a", "b", 100, 120, 20))
    assert signals.stated_fair_price(None, 100, shares) is None


def test_bundle_does_not_inherit_its_first_parts_cost_weights(monkeypatch):
    monkeypatch.setattr(estimates, "fair_price", lambda *_: estimates.Estimate(110, "one part only"))
    assert bands._fair({"parts": ["part:one", "part:two"]}, object(), 100, signals.SupplierSignals()) is None


def test_live_driver_evidence_refreshes_bands_without_a_breakdown(monkeypatch):
    from arena_kit.agents.buyer.contracts import CaseContext

    tools = DataTools()
    ctx = CaseContext(case={"suppliers": ["sup:unseen"]}, dossiers={}, bands={}, claims={}, c_alternative=None)
    updated_bands = {"sup:unseen": object()}
    monkeypatch.setattr(tools, "_claims", lambda *_: {})
    monkeypatch.setattr(tools, "_trades", lambda *_: {})
    monkeypatch.setattr(bands, "price_bands", lambda *_: updated_bands)
    threads = {"sup:unseen": [{"from": "supplier", "body": "LCI-C rose 2 % and is 30 % of our cost."}]}
    original = copy.deepcopy(threads)
    assert tools.refresh(ctx, threads).bands is updated_bands
    assert threads == original
