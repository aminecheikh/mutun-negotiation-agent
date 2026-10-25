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


def test_real_ppm_reads_the_latest_scorecard_and_none_without_one(package):
    # sup:70140 (P11) really delivers about 2,376 ppm - worse than any commitment on offer
    assert estimates.real_ppm(package, {}, "sup:70140") == 2376.0
    assert estimates.real_ppm(package, {}, "sup:99999") is None
    own = {"facts": {"sc:1@2030Q1": {"supplier": "sup:99999", "quarter": "2030Q1", "ppm": 120}}}
    assert estimates.real_ppm(None, own, "sup:99999") == 120.0  # the case's own facts count too


def test_should_cost_when_the_row_is_generic_and_the_supplier_is_turkish_applies_the_country_factor(package):
    # company.md: generic rows use a German cost base; TR full cost is about 0.75 of it
    est = estimates.should_cost(package, {**CASE, "parts": ["part:310070"]}, "sup:70150")
    assert est.value == pytest.approx(194.18 * 0.75, abs=0.01)
    assert est.uncertainty_pct > 5 and "TR" in est.source


def test_should_cost_when_the_row_is_generic_and_the_supplier_is_german_keeps_the_row(package):
    est = estimates.should_cost(package, {**CASE, "parts": ["part:310070"]}, "sup:70190")
    assert est.value == 194.18 and est.uncertainty_pct == 5


def test_should_cost_when_the_supplier_is_unknown_keeps_the_german_base_with_a_wide_band(package):
    est = estimates.should_cost(package, {**CASE, "parts": ["part:310070"]}, "sup:99999")
    assert est.value == 194.18 and est.uncertainty_pct == estimates.DEFAULT_UNCERTAINTY_PCT


def test_should_cost_when_the_row_is_this_suppliers_own_model_applies_no_country_factor(package):
    est = estimates.should_cost(package, {**CASE, "parts": ["part:310012"]}, "sup:70110")
    assert est.value == 347.27 and est.uncertainty_pct == 5


def test_supplier_estimate_when_the_supplier_offers_under_our_full_cost_caps_it_at_its_offer(package):
    """Corrections from the live negotiation: a supplier's own offer refutes a higher full-cost estimate; its opening
    ask keeps at least the sustainable margin."""
    import json

    case = next(c for c in json.loads((Path(__file__).parent / "fixtures/practice_cases.json").read_text())
                if c["case_id"] == "case:P01")  # fmt: skip
    std = {"payment_days": 60}
    low = [{"from": "supplier", "term_sheets": [{"terms": {"unit_price": 120.0}}]}]
    est = estimates.supplier_estimate(package, case, "sup:70110", std, low)
    keep = 1 - case["buyer"]["sustainable_margin_pct"] / 100
    assert est["full_cost"] < 120.0 * keep + 0.5 and "capped" in est["source"]
    high = [{"from": "supplier", "term_sheets": [{"terms": {"unit_price": 200.0}}]}]
    assert estimates.supplier_estimate(package, case, "sup:70110", std, high)["full_cost"] == 132.64
    assert estimates.supplier_estimate(package, case, "sup:70110", std)["full_cost"] == 132.64
