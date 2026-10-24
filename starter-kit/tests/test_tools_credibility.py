"""tools/credibility.py: how low an offer may go and still be taken seriously - tied to our cost evidence and to the
supplier's current offer. Practice sweep 4c30c05: first offers 25-28 % under the supplier where our estimate was a
rough guess (P04, P07, P08, P10, P18); P10 answered with a final offer at once."""

import json
from pathlib import Path

import pytest

from arena_kit.agents.buyer.contracts import Tactic
from arena_kit.agents.buyer.tools import DataTools, credibility
from arena_kit.agents.buyer.tools.estimates import Estimate
from arena_kit.cost import complete_offer
from arena_kit.data import DataPackage

DATA = Path(__file__).parents[2] / "data"
CASES = {c["case_id"]: c for c in json.loads((Path(__file__).parent / "fixtures/practice_cases.json").read_text())}


@pytest.fixture(scope="module")
def tools():
    return DataTools(DataPackage(DATA))


def opening_thread(case: dict, sid: str, price: float) -> list[dict]:
    terms = complete_offer(case, {"unit_price": price})
    sheet = {"term_sheet_id": f"offer:{sid[4:]}-0", "terms": terms}
    return [{"from": "sales", "status": "open", "term_sheets": [sheet], "attachments": [], "body": ""}]


def test_assess_when_evidence_is_firm_allows_offers_down_to_its_floor():
    c = credibility.assess("sup:x", floor=100.0, supplier_price=130.0, estimate=Estimate(100.0, "should-cost", 5.0))
    assert c.lowest_price == pytest.approx(95.0)


def test_assess_when_evidence_is_rough_never_goes_further_under_its_offer_than_our_error():
    c = credibility.assess("sup:x", floor=80.0, supplier_price=100.0, estimate=Estimate(80.0, "own prices", 20.0))
    assert c.lowest_price == pytest.approx(80.0) and "20" in c.reason


def test_assess_when_there_is_no_cost_evidence_stays_close_to_its_offer():
    c = credibility.assess("sup:x", floor=None, supplier_price=100.0, estimate=None)
    assert c.lowest_price == pytest.approx(100.0 * (1 - credibility.NO_EVIDENCE_GAP_PCT / 100))


def test_assess_follows_the_suppliers_current_offer():
    est = Estimate(80.0, "own prices", 20.0)
    high = credibility.assess("sup:x", floor=70.0, supplier_price=100.0, estimate=est)
    lower = credibility.assess("sup:x", floor=70.0, supplier_price=90.0, estimate=est)
    assert lower.lowest_price < high.lowest_price


@pytest.mark.parametrize("case", list(CASES.values()), ids=list(CASES))
def test_plan_when_any_case_opens_our_first_offer_is_credible(tools, case):
    base = case["buyer"].get("current_unit_price") or case["buyer"]["fallback_unit_price"]
    opening = round(base * 1.06, 2)
    sid = case["suppliers"][0]
    events = opening_thread(case, sid, opening)
    ctx = tools.prepare(case, {sid: events})
    lowest = ctx.credibility[sid].lowest_price
    assert lowest <= opening
    plan = tools.plan_offers(ctx, sid, events, Tactic(sid, "send"), None)
    assert plan.term_sheets[0]["unit_price"] >= lowest - 0.005
