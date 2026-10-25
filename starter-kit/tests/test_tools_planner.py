"""tools/planner.py: properties of the concession, anchor and exchange rules on every practice case file - never
numbers fitted to a practice outcome."""

import copy
import json
from pathlib import Path

import pytest

from arena_kit.agents.buyer.tools import estimates, planner
from arena_kit.data import DataPackage

DATA = Path(__file__).parents[2] / "data"
CASES = json.loads((Path(__file__).parent / "fixtures/practice_cases.json").read_text())
STRATEGIC = [c for c in CASES if c["buyer"].get("category_goal") in planner.STRATEGIC]


@pytest.fixture(scope="module")
def package():
    return DataPackage(DATA)


def opening(case: dict, sid: str, markup: float = 1.06) -> list[dict]:
    from arena_kit.cost import complete_offer

    base = case["buyer"].get("current_unit_price") or case["buyer"]["fallback_unit_price"]
    terms = complete_offer(case, {"unit_price": round(base * markup, 2)})
    return [{"from": "supplier", "status": "open", "attachments": [], "body": "Offer.",
             "term_sheets": [{"term_sheet_id": f"offer:{sid}-0", "terms": terms}]}]  # fmt: skip


def reply(events: list[dict], ours: list[dict], theirs: dict, n: int) -> list[dict]:
    return [*events, {"from": "buyer", "term_sheets": [{"terms": t} for t in ours]},
            {"from": "supplier", "status": "open", "attachments": [], "body": "Revised offer.",
             "term_sheets": [{"term_sheet_id": f"offer:x-{n}", "terms": theirs}]}]  # fmt: skip


def setup(case: dict, package, markup: float = 1.06):
    sid = case["suppliers"][0]
    events = opening(case, sid, markup)
    std = planner.standard(case, events)
    return sid, events, std, estimates.supplier_estimate(package, case, sid, std, events)


@pytest.mark.parametrize("case", STRATEGIC, ids=[c["case_id"] for c in STRATEGIC])
def test_packages_when_sustainable_margin_is_null_falls_back_to_the_default(case, package):
    mutated = copy.deepcopy(case)
    mutated["buyer"]["sustainable_margin_pct"] = None
    _, events, _, supplier = setup(mutated, package)
    assert planner.packages(mutated, events, supplier)


@pytest.mark.parametrize("case", STRATEGIC, ids=[c["case_id"] for c in STRATEGIC])
def test_packages_when_strategic_the_lead_sheet_never_opens_above_the_suppliers_own_price(case, package):
    _, events, _, supplier = setup(case, package, markup=0.95)
    theirs = events[0]["term_sheets"][0]["terms"]
    lead = planner.packages(case, events, supplier)[0]
    assert lead["unit_price"] <= theirs["unit_price"] + 0.005 or lead["payment_days"] != theirs["payment_days"]


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
@pytest.mark.parametrize("rate", [0.02, 0.36])
def test_value_equivalent_when_the_supplier_values_money_differently_no_exchange_costs_us_more(case, rate, package):
    _, events, std, supplier = setup(case, package)
    first = planner.packages(case, events, supplier)[0]
    exchanges, _ = planner._exchanges(case, first, events[0]["term_sheets"][0]["terms"], std, supplier, events)
    sheets = planner.value_equivalent(case, first, exchanges, std, supplier, events, rate, 50.0)
    budget = planner.cost(case, first, std, supplier)
    assert all(planner.cost(case, s, std, supplier) <= budget + 0.01 for s in sheets)


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_keep_margin_when_the_supplier_moves_push_concedes_less_than_follow(case, package):
    """Also where the supplier's price is already under our full-cost estimate (its price refutes the estimate)."""
    _, events, std, supplier = setup(case, package, markup=0.9)
    first = planner.packages(case, events, supplier)
    theirs = events[0]["term_sheets"][0]["terms"]
    events = reply(events, first, {**theirs, "unit_price": round(theirs["unit_price"] * 0.97, 2)}, 1)
    follow, push = planner.packages(case, events, supplier, 1.0), planner.packages(case, events, supplier, 0.0)
    if not (follow and push):
        return
    prior_c = planner.cost(case, first[0], std, supplier)
    c_follow, c_push = (planner.cost(case, s[0], std, supplier) for s in (follow, push))
    if c_follow - prior_c > 1.0 and push[0]["unit_price"] >= planner.credible_floor(supplier, theirs["unit_price"]):
        assert c_push < c_follow


def test_credible_floor_is_the_low_edge_of_the_estimate_or_of_the_suppliers_price():
    supplier = {"full_cost": 100.0, "uncertainty_pct": 10}
    assert planner.credible_floor(supplier, 200.0) == pytest.approx(90.0)
    assert planner.credible_floor(supplier, 80.0) == pytest.approx(72.0)  # its price refutes our higher estimate
    assert planner.credible_floor(None, 80.0) == 0.0


def test_anchor_share_when_leverage_opens_deeper_unless_switched_off(monkeypatch):
    case = next(c for c in CASES if c["buyer"].get("category_goal") == "leverage")
    assert planner.anchor_share(case) == planner.LEVERAGE_ANCHOR_SHARE
    monkeypatch.setenv("BUYER_LEVERAGE_ANCHOR", "0")
    assert planner.anchor_share(case) == planner.ANCHOR_SHARE


def test_concession_step_follows_the_suppliers_movement_in_c(package):
    case = next(c for c in CASES if c["case_id"] == "case:P04")
    sid, events, std, supplier = setup(case, package)
    theirs = events[0]["term_sheets"][0]["terms"]
    ours = planner.packages(case, events, supplier)
    assert planner.concession_step(case, events, supplier) == 0.0  # nothing to measure yet
    still = reply(events, ours, theirs, 1)
    assert planner.concession_step(case, still, supplier) == 0.0  # it did not move after our first offer
    assert planner.concession_step(case, reply(still, ours, theirs, 2), supplier) == planner.NUDGE_SHARE
    other = {**theirs, "unit_price": round(theirs["unit_price"] * 0.97, 2)}
    assert planner.concession_step(case, reply(events, ours, other, 1), supplier) in (
        planner.UNCONFIRMED_SHARE,
        *(1 / k for k in (2, 3)),
    )
    confirmed = {**ours[0], "unit_price": round(theirs["unit_price"] * 0.97, 2)}
    assert planner.concession_step(case, reply(events, ours, confirmed, 1), supplier) in (1 / 2, 1 / 3)


def test_commercial_brief_when_the_volume_is_large_prints_it_in_full():
    case = next(c for c in CASES if c["case_id"] == "case:P04")
    sid = case["suppliers"][0]
    events = opening(case, sid)
    sheet = {**events[0]["term_sheets"][0]["terms"], "volume_per_year": 1250000}
    text = planner.commercial_brief(case, [sheet], events)
    assert "1,250,000" in text and "e+" not in text
