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


CASE = {"case_id": "case:T", "parts": ["part:310010"], "buyer": {"interest_rate": 0.08}}


def test_should_cost_when_the_part_has_a_row_for_this_supplier_uses_it_with_its_confidence(package):
    est = estimates.should_cost(package, CASE, "sup:70110")
    assert est.value == 132.64 and est.uncertainty_pct == 5 and "cost:310010" in est.source


def test_should_cost_when_the_case_bundles_several_parts_has_no_single_unit_cost(package):
    assert estimates.should_cost(package, {**CASE, "parts": ["part:310010", "part:310011"]}, "sup:70110") is None


def test_should_cost_when_the_part_is_unknown_returns_none(package):
    assert estimates.should_cost(package, {**CASE, "parts": ["part:999999"]}, "sup:70110") is None


def test_supplier_estimate_when_should_cost_exists_values_it_at_our_rate_on_its_standard_terms(package):
    std = {"payment_days": 30, "volume_per_year": 1000}
    est = estimates.supplier_estimate(package, CASE, "sup:70110", std)
    assert est["full_cost"] == 132.64 and est["interest_rate"] == 0.08 and est["standard"] is std


def test_supplier_estimate_without_a_package_is_none():
    assert estimates.supplier_estimate(None, CASE, "sup:70110", {}) is None
