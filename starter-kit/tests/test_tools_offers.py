"""tools/offers.py: the offer engine on real cases, and its invariants on every case file."""

import json
from dataclasses import replace
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
    main = plans[0].term_sheets[0]
    # P01 is a stabilise case: below the C minimum the squeeze charge makes a lower price dearer, so the anchor may
    # only rise above the midpoint anchor where that lowers our C
    supplier = tools.supplier(ctx, sid, opening)
    at_anchor = {**main, "unit_price": round(expected, 2)}
    assert main["unit_price"] >= expected - 0.011
    assert total_cost(CASES[case_id], main, None, supplier) <= total_cost(CASES[case_id], at_anchor, None, supplier)


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


# Sandbox D02 (2026-10-24, session ed152d4335edfbca): an unknown supplier sent its own cost breakdown after round 1
# and dropped 1.40 from an inflated opening. Matching that step took our price to its own, and the band's target
# terms (short payment, FCA) at that price cost more than its standing sheet: no sheet could go out, the runner sent
# the same keep-alive three times and the supplier closed. The breakdown below is the attachment's text.
D02_J1 = "sup:DEMO-J1"
D02_BREAKDOWN = (
    "[Cost breakdown]\nElement | EUR per unit | Share of price\n"
    "BAR-14021 (material / cost driver) | 5.56 | =B2/B6\nWAGES-METAL (material / cost driver) | 4.39 | =B3/B6\n"
    "conversion (machine-hour rate x time) | 2.81 | =B4/B6\noverhead and profit | 2.94 | =B5/B6\n"
    "Unit price | =SUM(B2:B5) | =SUM(C2:C5)"
)
D02_STANDING = {"unit_price": 15.7, "payment_days": 30, "lead_time_days": 28, "incoterm": "DAP"}


def d02_after_breakdown(tools):
    case = SANDBOX["case:D02"]
    opening = {"unit_price": 17.1, "payment_days": 30, "lead_time_days": 28}
    events = [supplier_msg(D02_J1, opening, 0)]
    ctx = tools.prepare(case, {D02_J1: events})
    ours = {"unit_price": 14.9, "payment_days": 60, "lead_time_days": 28, "incoterm": "DAP"}
    reply = supplier_msg(D02_J1, D02_STANDING, 1)
    reply["attachments"] = [{"name": "cost-breakdown.xlsx", "text": D02_BREAKDOWN}]
    events = [*events, buyer_msg(ours), reply]
    return case, tools.refresh(ctx, {D02_J1: events}), events


def test_plan_when_supplier_drops_from_an_inflated_opening_never_meets_its_price(tools):
    case, ctx, events = d02_after_breakdown(tools)
    plan = tools.plan_offers(ctx, D02_J1, events, Tactic(D02_J1, "send"), READ)
    midpoint = (14.9 + D02_STANDING["unit_price"]) / 2  # split the difference at most, never jump to its price
    assert plan.term_sheets[0]["unit_price"] <= midpoint + 0.005


def test_plan_when_above_target_every_sheet_costs_less_than_its_standing_sheet(tools):
    case, ctx, events = d02_after_breakdown(tools)
    tactic = Tactic(D02_J1, "send", ("unit_price", "incoterm", "payment_days"))
    plan = tools.plan_offers(ctx, D02_J1, events, tactic, READ)
    supplier = tools.supplier_from_thread(case, D02_J1, events)
    standing_c = total_cost(case, D02_STANDING, None, supplier)
    assert plan.term_sheets
    for c in plan.c_values:
        assert c < standing_c


def test_plan_when_supplier_concedes_a_lot_never_concedes_past_the_midpoint(tools):
    case_id, sid, opening = P01
    theirs = [138.55, 131.55, 131.55]  # a 7.00 drop that still leaves it above our target
    ctx, plans = play_rounds(tools, CASES[case_id], sid, opening, theirs)
    assert theirs[1] > ctx.bands[sid].target
    ours = [p.term_sheets[0]["unit_price"] for p in plans]
    assert ours[2] <= (ours[1] + theirs[1]) / 2 + 0.005


@pytest.mark.parametrize("case", list(CASES.values()), ids=list(CASES))
def test_plan_when_rounds_pass_never_takes_back_what_we_offered(tools, case):
    # research 02 ("never move our offer backwards"): our main sheet is never worth less to the supplier than the
    # last one, also when its other terms change; only the supplier's own lower price may pull it down. Measured
    # on its side: in stabilise cases our C falls as our price rises (the relationship charge)
    base = case["buyer"].get("current_unit_price") or case["buyer"]["fallback_unit_price"]
    opening = complete_offer(case, {"unit_price": round(base * 1.06, 2)})
    sid = case["suppliers"][0]
    theirs = [round(opening["unit_price"] * (1 - 0.01 * i), 2) for i in range(case.get("round_limit", 10))]
    ctx, plans = play_rounds(tools, case, sid, opening, theirs)
    supplier = tools.supplier(ctx, sid, opening)
    previous = None
    for plan, their_price in zip(plans, [opening["unit_price"], *theirs], strict=False):
        main = plan.term_sheets[0]
        worth = supplier_value(case, main, supplier) if supplier else main["unit_price"]  # no estimate: price
        if previous is not None and main["unit_price"] < their_price:
            assert worth >= previous - 1e-6
        previous = worth


def test_plan_when_above_target_and_supplier_moves_we_still_move(tools):
    # brief: "it gives nothing without a move from you". Sandbox D02 (53ce03986722d2b5): holding at 14.90 while J1
    # came down from 15.70 to 15.30 left it waiting, and it walked after we answered its final offer unmoved
    case, ctx, events = d02_after_breakdown(tools)
    events = [*events, buyer_msg({"unit_price": 14.9, "payment_days": 60, "lead_time_days": 28, "incoterm": "DAP"}),
              supplier_msg(D02_J1, {**D02_STANDING, "unit_price": 15.3}, 2)]  # fmt: skip
    plan = tools.plan_offers(ctx, D02_J1, events, Tactic(D02_J1, "send"), READ)
    assert 14.9 < plan.term_sheets[0]["unit_price"] <= (14.9 + 15.3) / 2 + 0.005


# ---- sweep 885eec2 -----------------------------------------------------------------------------------------------


def test_plan_when_stabilise_supplier_is_below_its_cost_offers_a_sheet_cheaper_than_its_own(tools):
    # P02 (a3d4f84f7d5954f7) scored 0: the supplier priced under its plant should-cost, so in C (squeeze charge
    # included) every price below the C minimum costs more than its own sheet - we sent 3 e-mails without a sheet
    case = CASES["case:P02"]
    sid = "sup:70510"
    opening = {"unit_price": 370.96, "payment_days": 30, "index_clause": None}
    events = [supplier_msg(sid, opening, 0)]
    ctx = tools.prepare(case, {sid: events})
    supplier = tools.supplier_from_thread(case, sid, events)
    plan = tools.plan_offers(ctx, sid, events, Tactic(sid, "send"), READ)
    assert plan.c_values[0] < total_cost(case, opening, None, supplier)


@pytest.mark.parametrize("case", list(CASES.values()), ids=list(CASES))
def test_plan_when_any_case_never_offers_a_price_below_where_c_stops_falling(tools, case):
    # a lower price that costs us more C (the squeeze charge) is never worth offering
    base = case["buyer"].get("current_unit_price") or case["buyer"]["fallback_unit_price"]
    opening = complete_offer(case, {"unit_price": round(base * 1.06, 2)})
    sid = case["suppliers"][0]
    ctx, plans = play_rounds(tools, case, sid, opening, [opening["unit_price"]])
    supplier = tools.supplier(ctx, sid, opening)
    main = plans[0].term_sheets[0]
    higher = {**main, "unit_price": round(main["unit_price"] * 1.01, 2)}
    if higher["unit_price"] <= opening["unit_price"]:  # within what we may offer (never above its own price)
        assert total_cost(case, main, None, supplier) <= total_cost(case, higher, None, supplier) + 0.01


def test_plan_when_supplier_does_not_move_never_repeats_our_price(tools):
    # P11 (ac175e43e80a8f6f) and P05: the same offer three rounds running while the supplier stood still; it went
    # final - "it gives nothing without a move from you" (the brief), and its standstill says our target was low
    case_id, sid, opening = D01
    ctx, plans = play_rounds(tools, SANDBOX[case_id], sid, opening, [52.9] * 8)
    ours = [p.term_sheets[0]["unit_price"] for p in plans]
    for before, after in zip(ours, ours[1:], strict=False):
        assert after > before or after >= 52.9 - 0.005  # a visible step, unless we already reached its price


def test_c_minimum_floor_when_the_round_changes_stays_the_same_price(tools):
    # Saed (sweep e848872): P01's offer crept up (134.87 -> 134.88) while the supplier was still moving: the floor's
    # grid ran from this round's price to its current price, so the same floor moved by a cent each round
    case = CASES["case:P01"]
    sid, opening = P01[1], complete_offer(CASES["case:P01"], P01[2])
    events = [supplier_msg(sid, opening, 0)]
    ctx = tools.prepare(case, {sid: events})
    supplier, band = tools.supplier(ctx, sid, opening), ctx.bands[sid]
    floors = {
        offers._not_below_c_minimum(case, {**band.target_terms, "unit_price": low}, None, supplier, band, limit)[
            "unit_price"
        ]
        for low, limit in ((120.0, 138.55), (121.5, 138.0), (122.3, 137.4))
    }
    assert len(floors) == 1


def _with_cheaper_alternative(tools, status):
    case_id, sid, opening = P01
    case = CASES[case_id]
    events = [supplier_msg(sid, opening, 0)]
    ctx = tools.prepare(case, {sid: events})
    reply = {**supplier_msg(sid, {**opening, "unit_price": 136.0}, 1), "status": status}
    alternative = {**opening, "unit_price": 131.0, "payment_days": 30}
    reply["term_sheets"].append({"term_sheet_id": f"offer:{sid[4:]}-1-a", "terms": alternative})
    events = [*events, buyer_msg({**opening, "unit_price": 128.0}), reply]
    return case, tools.plan_offers(ctx, sid, events, Tactic(sid, "send"), READ), alternative


def test_plan_when_final_offer_has_a_cheaper_alternative_sends_exactly_that_alternative(tools):
    # Saed: at closing time send the supplier's cheaper alternative back (it will agree to its own proposal)
    case, plan, alternative = _with_cheaper_alternative(tools, "final_offer")
    assert plan.term_sheets[0] == complete_offer(case, alternative)


def test_plan_when_cheaper_alternative_mid_negotiation_keeps_negotiating(tools):
    # sweep 92d9283: sent back in round 2, P08/P18 closed at its own proposal (0.43/0.34) - negotiate it down instead
    case, plan, alternative = _with_cheaper_alternative(tools, "open")
    assert plan.term_sheets[0] != complete_offer(case, alternative)
    assert plan.term_sheets[0]["unit_price"] < alternative["unit_price"]


def test_plan_when_credibility_missing_never_opens_below_the_no_evidence_gap(tools):
    case_id, sid, opening = P01
    case = CASES[case_id]
    events = [supplier_msg(sid, opening, 0)]
    ctx = replace(tools.prepare(case, {sid: events}), credibility={})
    plan = tools.plan_offers(ctx, sid, events, Tactic(sid, "send"), READ)
    assert min(s["unit_price"] for s in plan.term_sheets) >= round(opening["unit_price"] * 0.92, 2) - 0.01


@pytest.mark.parametrize("case", list(CASES.values()), ids=list(CASES))
def test_plan_when_above_target_our_steps_never_grow(tools, case):
    # research 02 (Tey et al. 2021): growing late steps (Boulware) tell the supplier we have more to give - 11 of 19
    # case files showed them, up to +13 in the last rounds; shrinking steps signal a limit. Above our target, with
    # a supplier conceding evenly, a step of ours is never larger than our previous one
    base = case["buyer"].get("current_unit_price") or case["buyer"]["fallback_unit_price"]
    opening = complete_offer(case, {"unit_price": round(base * 1.06, 2)})
    sid = case["suppliers"][0]
    theirs = [round(opening["unit_price"] * (1 - 0.003 * i), 2) for i in range(case.get("round_limit", 10))]
    ctx, plans = play_rounds(tools, case, sid, opening, theirs)
    band, ours = ctx.bands[sid], [p.term_sheets[0]["unit_price"] for p in plans]
    steps = [(b, a - b) for b, a in zip(ours, ours[1:], strict=False)]
    above = [step for before, step in steps if before >= band.target]
    assert all(later <= earlier + 0.011 for earlier, later in zip(above, above[1:], strict=False)), above
