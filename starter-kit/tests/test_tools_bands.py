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


def test_price_bands_when_no_data_package_aims_at_todays_price():
    band = bands.price_bands(CASES["case:P01"], thread("sup:70110", P01_OPENING), None)["sup:70110"]
    assert band.floor is None and band.target == CASES["case:P01"]["buyer"]["current_unit_price"]


def test_price_bands_when_cost_estimate_is_above_opening_stays_within_its_uncertainty(package):
    # P02: should-cost 374.95 > opening 370.96 - the supplier is already squeezed; never aim far below cost
    opening = {"unit_price": 370.96, "payment_days": 30, "index_clause": None}
    band = bands.price_bands(CASES["case:P02"], thread("sup:70510", opening), package)["sup:70510"]
    assert 374.95 * (1 - 0.05) <= band.target <= 370.96


def unknown(case: dict, current, suppliers=("sup:99999",)) -> dict:
    out = json.loads(json.dumps(case))
    out["parts"], out["suppliers"] = ["part:999999"], list(suppliers)
    out["buyer"]["current_unit_price"] = current
    return out


def test_price_bands_when_nothing_is_known_but_todays_price_aims_at_no_increase(package):
    case = unknown(CASES["case:P01"], 126.53)
    band = bands.price_bands(case, thread("sup:99999", P01_OPENING), package)["sup:99999"]
    assert band.target == 126.53 and any("today's price" in d for d in band.derivation)


def test_price_bands_when_rfq_one_suppliers_band_ignores_the_other_bidders(package):
    # no inter-supplier comparison: another bidder's quote never moves this supplier's band
    case = unknown(CASES["case:P01"], None, ("sup:99998", "sup:99999"))
    alone = bands.price_bands(case, thread("sup:99999", P01_OPENING), package)["sup:99999"]
    threads = {**thread("sup:99998", {**P01_OPENING, "unit_price": 131.0}), **thread("sup:99999", P01_OPENING)}
    band = bands.price_bands(case, threads, package)["sup:99999"]
    assert band.target == alone.target and not any("other bidder" in d for d in band.derivation)


def test_price_bands_when_there_is_no_anchor_at_all_says_so(package):
    case = unknown(CASES["case:P01"], None)
    case["buyer"]["fallback_unit_price"] = 150.0  # above the opening: no anchor
    band = bands.price_bands(case, thread("sup:99999", P01_OPENING), package)["sup:99999"]
    assert band.target < 138.55 and any("no anchor" in d for d in band.derivation)


def test_supplier_band_when_supplier_leaves_lead_time_out_keeps_its_standard_not_our_wish(package):
    case = json.loads(json.dumps(CASES["case:P01"]))
    case["negotiable"] = [*case["negotiable"], "lead_time_days"]
    case["term_options"]["lead_time_days"] = [28, 35, 42, 56]
    case["fixed_terms"].pop("lead_time_days", None)
    band = bands.supplier_band(case, package, "sup:70110", P01_OPENING, {"lead_time_days": 42})
    assert band.target_terms["lead_time_days"] == 42  # its cost of faster delivery is unknown to us: ask, don't impose


def test_held_at_standard_when_its_lead_time_breaks_a_must_have_moves_to_the_closest_allowed_option():
    # P05: its 52 days broke our <= 42; asking 28-35 days raised its price, 42 days got its own price agreed
    import json
    from pathlib import Path

    from arena_kit.agents.buyer.tools.bands import held_at_standard

    cases = json.loads((Path(__file__).parent / "fixtures/practice_cases.json").read_text(encoding="utf-8"))
    case = next(c for c in cases if c["case_id"] == "case:P05")  # must-have lead_time_days <= 42
    assert held_at_standard(case, {"unit_price": 31.5, "lead_time_days": 52})["lead_time_days"] == 42
    assert held_at_standard(case, {"unit_price": 31.5, "lead_time_days": 35})["lead_time_days"] == 35  # kept
