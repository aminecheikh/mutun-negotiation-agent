"""tools/estimates.py against the real data package in the repo's data/ folder."""

from pathlib import Path

import pytest

from arena_kit.agents.buyer.tools import estimates
from arena_kit.data import DataPackage

DATA = Path(__file__).parents[2] / "data"


@pytest.fixture(scope="module")
def package():
    return DataPackage(DATA)


def test_index_change_when_series_has_a_year_of_data_matches_the_arena_example(package):
    change = estimates.index_change(package, "PPI-D351")
    assert change.earlier_id == "idx:PPI-D351@2025-08" and change.later_id == "idx:PPI-D351@2026-08"
    assert round(change.pct, 1) == -3.7


def test_index_change_when_series_is_unknown_returns_none(package):
    assert estimates.index_change(package, "NO-SUCH-SERIES") is None


def test_fair_price_when_part_has_cost_drivers_moves_base_by_weighted_index_change(package):
    # P01: iron/steel +6.5 % x 0.26, electricity -3.7 % x 0.06, labour +2.7 % x 0.32 -> +2.33 %
    fair = estimates.fair_price(package, "part:310010", 126.53)
    assert fair.value == pytest.approx(129.48, abs=0.01)
    assert "PPI-C241" in fair.source and "LCI-C" in fair.source


def test_fair_price_when_part_has_no_cost_drivers_returns_none(package):
    assert estimates.fair_price(package, "part:999999", 10.0) is None


def test_full_cost_when_should_cost_models_this_supplier_uses_it_with_five_percent(package):
    cost = estimates.full_cost(package, "part:310010", "sup:70110")
    assert cost.value == 132.64 and cost.uncertainty_pct == 5 and "sup:70110" in cost.source


def test_full_cost_when_only_a_german_base_exists_scales_by_supplier_country(package):
    # part:310070 has a generic DE row (194.18); sup:70150 is Turkish (factor 0.75)
    cost = estimates.full_cost(package, "part:310070", "sup:70150")
    assert cost.value == pytest.approx(194.18 * 0.75, abs=0.01) and cost.uncertainty_pct == 15


def test_full_cost_when_no_should_cost_row_falls_back_to_lowest_recent_price_minus_margin(package):
    cost = estimates.full_cost(package, "part:360020", "sup:70620")
    assert cost is not None and cost.uncertainty_pct >= 15 and "price" in cost.source
    lowest = min(r["price"] for r in package.query("SELECT price FROM price_conditions WHERE part_id = 'part:360020'"))
    assert cost.value < lowest


def test_cost_of_money_when_supplier_offers_skonto_uses_its_implied_rate(package):
    # sup:70210: 2 % skonto within 10 days, net 30 -> 2/98 x 360/20 = 36.7 %
    rate = estimates.cost_of_money(package, "sup:70210")
    assert rate.value == pytest.approx(0.367, abs=0.001) and "skonto" in rate.source


def test_cost_of_money_when_skonto_rate_is_extreme_caps_it(package):
    assert estimates.cost_of_money(package, "sup:70280").value == estimates.MAX_RATE


def test_cost_of_money_when_no_skonto_adds_premia_to_our_rate(package):
    # sup:70110: credit index 222 (no premium), equity 24.7 % (thin: +2 pt)
    rate = estimates.cost_of_money(package, "sup:70110")
    assert rate.value == pytest.approx(0.10) and "equity" in rate.source


def test_accepts_below_cost_when_history_note_says_so_is_true(package):
    assert estimates.accepts_below_cost(package, "sup:70110") is True
    assert estimates.accepts_below_cost(package, "sup:70210") is False
