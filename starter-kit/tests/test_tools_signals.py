"""tools/signals.py and tools/levers.py: what the supplier tells us, and what each term is worth to us."""

import json
from pathlib import Path

import pytest

from arena_kit.agents.buyer.tools import bands, levers, signals
from arena_kit.cost import complete_offer
from arena_kit.data import DataPackage
from arena_kit.docs import read_document

DATA = Path(__file__).parents[2] / "data"
FIXTURES = Path(__file__).parent / "fixtures"
CASES = {c["case_id"]: c for c in json.loads((FIXTURES / "practice_cases.json").read_text())}
BREAKDOWN = read_document("cost-breakdown.xlsx", (FIXTURES / "cost-breakdown.xlsx").read_bytes())


@pytest.fixture(scope="module")
def package():
    return DataPackage(DATA)


def supplier_email(body: str = "", attachments=()) -> dict:
    return {"from": "sales@x", "status": "open", "body": body, "attachments": list(attachments), "term_sheets": []}


def test_read_when_supplier_states_its_drivers_returns_their_shares(package):
    body = (
        "LCI-C rose 2.7 % in twelve months and is 30 % of our cost. That remains.\n"
        "PPI-C241 rose 6.5 % in twelve months and is 24 % of our cost.\n"
        "NOT-A-SERIES rose 9 % in twelve months and is 50 % of our cost."
    )
    found = signals.read([supplier_email(body)], package)
    assert found.driver_shares == {"LCI-C": 0.30, "PPI-C241": 0.24}


def test_read_when_a_cost_breakdown_is_attached_returns_its_full_cost_and_price(package):
    sheet = {"name": "cost-breakdown.xlsx", "type": "xlsx", "text": BREAKDOWN}
    found = signals.read([supplier_email(attachments=[sheet])], package)
    assert found.breakdown_cost == pytest.approx(14.1 + 19.6 + 6.3) and found.breakdown_price == pytest.approx(43.2)


def test_read_when_nothing_is_stated_returns_empty_signals(package):
    found = signals.read([supplier_email("Thank you for your enquiry.")], package)
    assert found.driver_shares == {} and found.breakdown_cost is None


def test_stated_fair_price_when_shares_known_uses_our_verified_index_changes(package):
    # it says electricity is 50 % of its cost; our data: PPI-D351 -3.72 % -> 100 x (1 - 0.0186)
    fair = signals.stated_fair_price(package, 100.0, {"PPI-D351": 0.5})
    assert fair.value == pytest.approx(98.14, abs=0.01) and "its own" in fair.source


def test_supplier_band_when_cost_breakdown_known_uses_it_as_the_floor(package):
    case = CASES["case:P01"]
    terms = {"unit_price": 138.55, "effective_date": "2026-11-01"}
    sig = signals.SupplierSignals(driver_shares={}, breakdown_cost=125.0, breakdown_price=138.55)
    band = bands.supplier_band(case, package, "sup:70110", terms, {}, signals=sig)
    assert band.floor == pytest.approx(125.0 * (1 - bands.BELOW_COST_TOLERANCE))
    assert any("cost breakdown" in d for d in band.derivation)


def test_trades_when_its_lead_time_is_late_values_a_shorter_one(package):
    case = json.loads(json.dumps(CASES["case:P01"]))
    case["negotiable"] = [*case["negotiable"], "lead_time_days"]
    case["term_options"]["lead_time_days"] = [28, 42, 56]
    case["fixed_terms"].pop("lead_time_days", None)
    opening = complete_offer(case, {"unit_price": 138.55, "effective_date": "2026-11-01", "lead_time_days": 56})
    found = {t.field: t for t in levers.trades(case, opening, {}, None)}
    late = case["buyer"]["late_cost_per_day"] * (56 - case["buyer"]["need_lead_time_days"])
    assert found["lead_time_days"].eur == pytest.approx(late, rel=1e-3) and found["lead_time_days"].per_unit > 0


@pytest.mark.parametrize("case", list(CASES.values()), ids=list(CASES))
def test_trades_when_any_case_only_lists_changes_that_save_us_money(case, package):
    base = case["buyer"].get("current_unit_price") or case["buyer"]["fallback_unit_price"]
    opening = complete_offer(case, {"unit_price": round(base * 1.06, 2)})
    for trade in levers.trades(case, opening, {}, None):
        assert trade.eur > 0 and trade.per_unit > 0 and trade.field != "unit_price"
        assert trade.ours in case["term_options"][trade.field]


def test_refresh_when_a_cost_breakdown_arrives_moves_the_floor_to_its_real_cost(package):
    from arena_kit.agents.buyer.tools import DataTools

    case = CASES["case:P01"]
    quote = {"term_sheet_id": "offer:70110-0", "terms": {"unit_price": 138.55, "effective_date": "2026-11-01"}}
    events = [{"from": "sales@70110", "status": "open", "body": "", "attachments": [], "term_sheets": [quote]}]
    tools = DataTools(package)
    ctx = tools.prepare(case, {"sup:70110": events})
    rows = [
        "[Cost breakdown]",
        "Element | EUR per unit | Share",
        "Material | 50.0 | x",
        "Conversion | 60.0 | x",
        "Margin | 8.0 | x",
    ]
    breakdown = "\n".join(rows)
    sheet = {"name": "cost-breakdown.xlsx", "type": "xlsx", "text": breakdown}
    later = [*events, {"from": "buyer"}, {**events[0], "attachments": [sheet]}]
    band = tools.refresh(ctx, {"sup:70110": later}).bands["sup:70110"]
    assert band.floor == pytest.approx(110.0 * (1 - bands.BELOW_COST_TOLERANCE)) != ctx.bands["sup:70110"].floor
    assert ctx.trades["sup:70110"] == tools.refresh(ctx, {"sup:70110": events}).trades["sup:70110"]
