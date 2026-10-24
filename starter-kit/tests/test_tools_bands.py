"""tools/bands.py: price bands on real practice cases and the real data package."""

import json
from pathlib import Path

import pytest

from arena_kit.agents.buyer.tools import bands
from arena_kit.cost import total_cost
from arena_kit.data import DataPackage

DATA = Path(__file__).parents[2] / "data"
CASES = {c["case_id"]: c for c in json.loads((Path(__file__).parent / "fixtures/practice_cases.json").read_text())}


@pytest.fixture(scope="module")
def package():
    return DataPackage(DATA)


def thread(sid: str, terms: dict) -> dict[str, list[dict]]:
    sheet = {"term_sheet_id": f"offer:{sid[4:]}-0", "terms": terms}
    return {sid: [{"from": f"sales@{sid[4:]}", "status": "open", "term_sheets": [sheet], "attachments": []}]}


P01_OPENING = {"unit_price": 138.55, "effective_date": "2026-11-01"}


def test_price_bands_when_stabilise_case_aims_between_supplier_cost_and_its_opening(package):
    band = bands.price_bands(CASES["case:P01"], thread("sup:70110", P01_OPENING), package)["sup:70110"]
    assert band.floor == pytest.approx(132.64 * (1 - bands.BELOW_COST_TOLERANCE))
    assert band.fair == pytest.approx(129.48, abs=0.01)
    assert band.floor < band.target < 138.55 and band.ceiling == 138.55
    assert band.c_target < band.c_ceiling


def test_price_bands_when_stabilise_case_target_beats_the_kit_agents_deal_in_our_model(package):
    case = CASES["case:P01"]
    band = bands.price_bands(case, thread("sup:70110", P01_OPENING), package)["sup:70110"]
    supplier = bands.supplier_estimate(case, package, "sup:70110", P01_OPENING, {})
    baseline = {"unit_price": 133.5, "payment_days": 60, "effective_date": "2027-01-01"}
    assert band.c_target < total_cost(case, baseline, None, supplier)
    assert set(band.target_terms) >= set(case["negotiable"])


def test_price_bands_when_target_terms_chosen_they_are_allowed_options(package):
    case = CASES["case:P01"]
    band = bands.price_bands(case, thread("sup:70110", P01_OPENING), package)["sup:70110"]
    for field, options in case["term_options"].items():
        assert band.target_terms[field] in options


def test_price_bands_when_alternative_is_cheaper_than_opening_caps_ceiling_below_opening(package):
    case = json.loads(json.dumps(CASES["case:P01"]))
    case["buyer"]["alternative"]["terms"]["unit_price"] = 134.0
    band = bands.price_bands(case, thread("sup:70110", P01_OPENING), package)["sup:70110"]
    assert band.ceiling < 138.55
    assert band.c_ceiling == pytest.approx(bands.alternative_cost(case), rel=1e-3)


def test_price_bands_when_leverage_case_without_should_cost_targets_below_opening(package):
    case = CASES["case:P08"]
    opening = {"unit_price": 6.05, "payment_days": 30, "index_clause": None, "contract_months": 12}
    band = bands.price_bands(case, thread("sup:70710", opening), package)["sup:70710"]
    assert band.floor is not None and band.target < 6.05 and band.c_target < band.c_ceiling


def test_price_bands_when_no_data_package_falls_back_to_share_of_opening():
    band = bands.price_bands(CASES["case:P01"], thread("sup:70110", P01_OPENING), None)["sup:70110"]
    assert band.floor is None and band.target == round(138.55 * bands.PLACEHOLDER_TARGET_SHARE, 2)


def test_price_bands_when_cost_estimate_is_above_opening_stays_within_its_uncertainty(package):
    # P02: should-cost 374.95 > opening 370.96 - the supplier is already squeezed; never aim far below cost
    opening = {"unit_price": 370.96, "payment_days": 30, "index_clause": None}
    band = bands.price_bands(CASES["case:P02"], thread("sup:70510", opening), package)["sup:70510"]
    assert 374.95 * (1 - 0.05) <= band.target <= 370.96
