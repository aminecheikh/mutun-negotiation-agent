"""tools/offers.py: the offer engine on real cases, and its invariants on every case file."""

import json
from pathlib import Path

import pytest

from arena_kit.agents.buyer.contracts import BehaviourRead, Tactic
from arena_kit.agents.buyer.tools import DataTools, offers
from arena_kit.cost import complete_offer, must_haves_ok, supplier_value, total_cost
from arena_kit.data import DataPackage

DATA = Path(__file__).parents[2] / "data"
CASES = {c["case_id"]: c for c in json.loads((Path(__file__).parent / "fixtures/practice_cases.json").read_text())}
READ = BehaviourRead("x", 0.6, 0.1, (), ())


@pytest.fixture(scope="module")
def tools():
    return DataTools(DataPackage(DATA))


def supplier_msg(sid: str, terms: dict, n: int) -> dict:
    sheet = {"term_sheet_id": f"offer:{sid[4:]}-{n}", "terms": terms}
    return {"from": f"sales@{sid[4:]}", "status": "open", "term_sheets": [sheet], "attachments": []}


def buyer_msg(terms: dict) -> dict:
    return {"from": "buyer", "term_sheets": [{"terms": terms}]}


def play_rounds(tools, case: dict, sid: str, opening: dict, their_prices: list[float]):
    """Our plan each round against a supplier that answers with `their_prices` in turn."""
    events = [supplier_msg(sid, opening, 0)]
    ctx = tools.prepare(case, {sid: events})
    plans = []
    for n, price in enumerate(their_prices, start=1):
        plan = tools.plan_offers(ctx, sid, events, Tactic(sid, "send"), READ)
        plans.append(plan)
        events = [*events, buyer_msg(plan.term_sheets[0]), supplier_msg(sid, {**opening, "unit_price": price}, n)]
    return ctx, plans


P01 = ("case:P01", "sup:70110", {"unit_price": 138.55, "effective_date": "2026-11-01"})


def test_plan_when_first_round_anchors_so_the_midpoint_is_the_target(tools):
    case_id, sid, opening = P01
    ctx, plans = play_rounds(tools, CASES[case_id], sid, opening, [138.55])
    band = ctx.bands[sid]
    expected = max(2 * band.target - 138.55, band.floor * (1 - offers.MAX_ANCHOR_BELOW_FLOOR_PCT / 100))
    assert plans[0].term_sheets[0]["unit_price"] == pytest.approx(expected, abs=0.011)


def test_plan_when_rounds_pass_concedes_monotonically_within_ceiling_and_their_price(tools):
    case_id, sid, opening = P01
    theirs = [138.55, 137.9, 137.0, 136.4, 136.0, 135.8, 135.7, 135.6, 135.6]
    ctx, plans = play_rounds(tools, CASES[case_id], sid, opening, theirs)
    ours = [p.term_sheets[0]["unit_price"] for p in plans]
    assert ours == sorted(ours)
    assert all(o <= min(ctx.bands[sid].ceiling, t) for o, t in zip(ours, [138.55, *theirs], strict=False))


def test_plan_when_supplier_concedes_matches_its_step_at_least(tools):
    case_id, sid, opening = P01
    _, plans = play_rounds(tools, CASES[case_id], sid, opening, [138.55, 136.55, 136.55])
    ours = [p.term_sheets[0]["unit_price"] for p in plans]
    assert ours[2] - ours[1] >= 2.0 - 0.011  # it moved 2.00 between our 2nd and 3rd offer


def test_plan_when_alternatives_offered_they_cost_us_the_same_and_are_worth_more_to_it(tools):
    case_id, sid, opening = P01
    case = CASES[case_id]
    ctx, plans = play_rounds(tools, case, sid, opening, [138.55])
    plan = plans[0]
    supplier = tools.supplier(ctx, sid, opening)
    assert len(plan.term_sheets) >= 2
    first = plan.term_sheets[0]
    for sheet, c in zip(plan.term_sheets[1:], plan.c_values[1:], strict=True):
        assert c == pytest.approx(plan.c_values[0], rel=0.002)
        assert supplier_value(case, sheet, supplier) > supplier_value(case, first, supplier)


@pytest.mark.parametrize("case", list(CASES.values()), ids=list(CASES))
def test_plan_when_any_case_is_played_keeps_every_sheet_valid(tools, case):
    base = case["buyer"].get("current_unit_price") or case["buyer"]["fallback_unit_price"]
    opening = complete_offer(case, {"unit_price": round(base * 1.06, 2)})
    sid = case["suppliers"][0]
    theirs = [round(opening["unit_price"] * (1 - 0.01 * i), 2) for i in range(case.get("round_limit", 10))]
    ctx, plans = play_rounds(tools, case, sid, opening, theirs)
    previous = 0.0
    for plan, their_price in zip(plans, [opening["unit_price"], *theirs], strict=False):
        assert 1 <= len(plan.term_sheets) <= 3 and len(plan.c_values) == len(plan.term_sheets)
        for sheet in plan.term_sheets:
            assert set(sheet) == set(case["negotiable"]) and 0 < sheet["unit_price"] <= their_price
            assert must_haves_ok(case, sheet)
            for field, allowed in case.get("term_options", {}).items():
                if field in sheet:
                    assert sheet[field] in allowed, field
        main = plan.term_sheets[0]["unit_price"]
        assert main >= previous or main == their_price  # only its own lower price may pull ours down
        previous = main
        supplier = tools.supplier(ctx, sid, opening)
        assert plan.c_values[0] == pytest.approx(total_cost(case, plan.term_sheets[0], None, supplier), rel=1e-6)


SANDBOX = {c["case_id"]: c for c in json.loads((Path(__file__).parent / "fixtures/sandbox_cases.json").read_text())}
D01 = (
    "case:D01",
    "sup:DEMO-F1",
    {"unit_price": 52.9, "payment_days": 30, "contract_months": 12, "effective_date": "2026-12-01"},
)


def test_plan_when_supplier_has_no_data_still_covers_the_whole_need(tools):
    # a supplier we know nothing about quotes 12 months on a 2-year need: uncovered need is the costliest term
    case_id, sid, opening = D01
    ctx, plans = play_rounds(tools, SANDBOX[case_id], sid, opening, [52.9])
    need_months = 12 * SANDBOX[case_id]["buyer"]["horizon_years"]
    assert ctx.bands[sid].target_terms["contract_months"] == need_months
    assert plans[0].term_sheets[0]["contract_months"] == need_months


def test_plan_when_supplier_does_not_move_we_still_step_visibly_towards_target(tools):
    case_id, sid, opening = D01
    ctx, plans = play_rounds(tools, SANDBOX[case_id], sid, opening, [51.9] * 5)  # it waits for our move
    band, ours = ctx.bands[sid], [p.term_sheets[0]["unit_price"] for p in plans]
    pace = (band.target - ours[0]) / ((SANDBOX[case_id].get("round_limit", 10) - 1) / 2)
    for before, after in zip(ours, ours[1:], strict=False):
        assert after - before >= min(pace, band.target - before) - 0.011


def test_plan_when_strategist_picks_levers_every_lever_moves_in_some_sheet(tools):
    case_id, sid, opening = D01
    case = SANDBOX[case_id]
    events = [supplier_msg(sid, opening, 0)]
    ctx = tools.prepare(case, {sid: events})
    levers = ("payment_days", "effective_date")
    plan = tools.plan_offers(ctx, sid, events, Tactic(sid, "send", levers=levers), READ)
    for lever in levers:
        assert len({str(s[lever]) for s in plan.term_sheets}) >= 2, lever


def test_plan_when_levers_offered_no_sheet_covers_less_need_or_sits_far_under_our_anchor(tools):
    # equal C for us is not enough: a shorter contract "paid for" by our uncovered-need cost reveals our fallback,
    # and a price far under our most aggressive justified price is not credible
    from arena_kit.cost import cost_breakdown

    case_id, sid, opening = D01
    case = SANDBOX[case_id]
    events = [supplier_msg(sid, opening, 0)]
    ctx = tools.prepare(case, {sid: events})
    tactic = Tactic(sid, "send", levers=("contract_months", "payment_days"))
    plan = tools.plan_offers(ctx, sid, events, tactic, READ)
    main = plan.term_sheets[0]
    uncovered = cost_breakdown(case, main)["uncovered_need"]
    for sheet in plan.term_sheets:
        assert cost_breakdown(case, sheet)["uncovered_need"] <= uncovered
        assert sheet["unit_price"] >= main["unit_price"] * (1 - offers.MAX_ANCHOR_BELOW_FLOOR_PCT / 100)
