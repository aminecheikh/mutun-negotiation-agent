"""S2 + S3 (owner: Saed): the one-call LLM strategist, its safety net and the rule-based fallback it uses."""

import json
import time
from dataclasses import replace
from pathlib import Path

from arena_kit.agents.buyer.contracts import BehaviourRead, Claim, ConcessionFit, Trade
from arena_kit.agents.buyer.roles import LLMStrategist, RuleAnalyst, RuleStrategist
from arena_kit.agents.buyer.roles.analyst import wants_from
from arena_kit.agents.buyer.roles.strategist import (
    accept_ready,
    negotiable_levers,
    refined_read,
    safe,
    usable_claims,
)
from arena_kit.agents.buyer.tools import DataTools

CASE = json.loads((Path(__file__).parents[1] / "arena_kit/sim_cases/S01.json").read_text())
SID = "sup:70270"
CLAIMS = (
    Claim("Index A fell 3.7% (idx:A@2025-08, idx:A@2026-08).", ("idx:A@2025-08", "idx:A@2026-08"), "index_change",
          -3.7, True, 0.5),
    Claim("Index B rose 40% (idx:B@2025-08, idx:B@2026-08).", ("idx:B@2025-08", "idx:B@2026-08"), "index_change",
          40.0, False, 0.0),
    Claim("Your on-time delivery averaged 63.5% (sc:70270@2026Q3).", ("sc:70270@2026Q3",), "scorecard",
          63.5, True, 0.8),
)  # fmt: skip


def supplier(price=17.4, status="open", body="Our offer stands.", n=0):
    sheet = {"term_sheet_id": f"offer:70270-{n}", "terms": {"unit_price": price, "payment_days": 30}}
    return {"from": "sales@70270", "status": status, "term_sheets": [sheet], "attachments": [], "body": body}


def buyer(body="Our proposal.", refs=()):
    return {"from": "buyer", "body": body, "refs": list(refs), "term_sheets": [{"terms": {"unit_price": 15.0}}]}


def setup(events, alt_c=None):
    threads = {SID: events}
    ctx = DataTools().prepare(CASE, {SID: events[:1]})
    ctx = replace(ctx, claims={SID: CLAIMS}, c_alternative=alt_c)
    return ctx, threads


def rule_read(ctx, threads):
    fit = ConcessionFit(SID, (17.4,), None, None, False)
    return {SID: RuleAnalyst().read(ctx, SID, threads[SID], fit)}


def rule_tactic(ctx, threads):
    return RuleStrategist().choose(ctx, threads, rule_read(ctx, threads))


# ---- safe(): the model can never break policy ---------------------------------------------------


def test_safe_when_model_names_another_supplier_still_answers_the_one_thread():
    ctx, threads = setup([supplier()])
    tactic = safe(ctx, threads, {"supplier_id": "sup:99999", "action": "send"}, rule_tactic(ctx, threads))
    assert tactic.supplier_id == SID  # one-on-one: the model never picks a supplier


def test_safe_when_accept_before_our_first_message_sends_with_cost_breakdown_question():
    ctx, threads = setup([supplier()])
    tactic = safe(ctx, threads, {"action": "accept"}, rule_tactic(ctx, threads))
    assert tactic.action == "send" and "cost breakdown" in tactic.question


def test_safe_when_walk_without_alternative_sends_instead():
    ctx, threads = setup([supplier(), buyer(), supplier(status="final_offer", n=1)])
    tactic = safe(ctx, threads, {"action": "walk_away"}, rule_tactic(ctx, threads))
    assert tactic.action == "send"


def test_safe_when_alternative_cheaper_and_supplier_final_allows_walk():
    ctx, threads = setup([supplier(), buyer(), supplier(status="final_offer", n=1)], alt_c=1.0)
    tactic = safe(ctx, threads, {"action": "walk_away"}, rule_tactic(ctx, threads))
    assert tactic.action == "walk_away"


def test_safe_filters_claims_levers_and_question_with_figures():
    ctx, threads = setup([supplier(), buyer(refs=["sc:70270@2026Q3"]), supplier(n=1)])
    raw = {
        "action": "send",
        "claims": [0, 1, 2, 7, "x"],
        "levers": ["payment_days", "tooling_eur", "made_up", "unit_price"],
        "question": "Can you do 15.00?",
    }
    tactic = safe(ctx, threads, raw, rule_tactic(ctx, threads))
    assert tactic.claims == (0,)  # 1 hurts us, 2 already cited, 7 and "x" do not exist
    assert tactic.levers == ("payment_days", "unit_price")  # tooling_eur is not negotiable in S01
    assert tactic.question is None


def test_safe_when_supplier_agreed_accepts():
    ctx, threads = setup([supplier(), buyer(), supplier(status="agreed", n=1)])
    tactic = safe(ctx, threads, {"action": "send", "levers": ["unit_price"]}, rule_tactic(ctx, threads))
    assert tactic.action == "accept"


def test_safe_when_no_messages_left_accepts():
    events = [supplier()]
    for n in range(CASE.get("round_limit", 10)):
        events += [buyer(), supplier(n=n + 1)]
    ctx, threads = setup(events)
    tactic = safe(ctx, threads, {"action": "send", "levers": ["unit_price"]}, rule_tactic(ctx, threads))
    assert tactic.action == "accept"


# ---- refined_read(): the model's read is bounded ---------------------------------------------------


def test_refined_read_clips_and_keeps_only_real_quotes_and_known_terms():
    events = [supplier(body="We could pay attention to faster payment if volumes grow.")]
    rule = BehaviourRead(SID, 0.6, 0.1, ("volume_per_year",), ("status open",))
    raw = {"p_more": 7, "walk_risk": "high", "wants": ["payment_days", "free_lunch"],
           "signals": ["faster payment", "an invented quote"]}  # fmt: skip
    read = refined_read(SID, raw, rule, events)
    assert read.p_more == 1.0 and read.walk_risk == 0.1
    assert read.wants == ("payment_days",) and read.signals == ("faster payment",)


# ---- LLMStrategist: one call, fallback on anything wrong -----------------------------------------


def test_llm_strategist_when_valid_reply_uses_tactic_and_updates_reads():
    ctx, threads = setup([supplier(), buyer(), supplier(price=17.0, n=1, body="Faster payment would help.")])
    reads = rule_read(ctx, threads)

    def complete(system, user):
        assert "Faster payment would help." in user and '"category_goal"' in user
        read = {"p_more": 0.8, "walk_risk": 0.05, "wants": ["payment_days"], "signals": ["Faster payment"]}
        tactic = {
            "action": "send",
            "levers": ["unit_price", "payment_days"],
            "claims": [2],
            "question": "Which payment term would change your price?",
            "rationale": "cash",
        }
        return json.dumps({"read": read, "tactic": tactic})

    tactic = LLMStrategist(complete).choose(ctx, threads, reads)
    assert tactic.action == "send" and tactic.levers == ("unit_price", "payment_days") and tactic.claims == (2,)
    assert reads[SID].p_more == 0.8 and reads[SID].wants == ("payment_days",)


def test_llm_strategist_when_reply_is_garbage_returns_rule():
    ctx, threads = setup([supplier()])
    reads = rule_read(ctx, threads)
    tactic = LLMStrategist(lambda s, u: "I think you should negotiate harder.").choose(ctx, threads, reads)
    assert tactic == RuleStrategist().choose(ctx, threads, rule_read(ctx, threads))


def test_llm_strategist_when_model_is_late_returns_rule_in_time():
    ctx, threads = setup([supplier()])
    reads = rule_read(ctx, threads)

    def slow(system, user):
        time.sleep(2)
        return "{}"

    t0 = time.monotonic()
    tactic = LLMStrategist(slow, timeout_s=0.2).choose(ctx, threads, reads)
    assert time.monotonic() - t0 < 1.5 and tactic.rationale.startswith("rule:")


# ---- RuleStrategist / RuleAnalyst: the policy without a model ------------------------------------


def test_rule_strategist_when_first_round_probes_two_terms_with_claims_and_question():
    ctx, threads = setup([supplier()])
    tactic = rule_tactic(ctx, threads)
    assert tactic.action == "send" and len(tactic.levers) >= 2 and "unit_price" in tactic.levers
    assert tactic.claims == (2, 0) and tactic.question  # strongest first, the claim that hurts us never


def test_rule_strategist_when_final_offer_and_no_alternative_accepts():
    ctx, threads = setup([supplier(), buyer(), supplier(status="final_offer", n=1)])
    assert rule_tactic(ctx, threads).action == "accept"


def test_rule_strategist_when_final_offer_dearer_than_alternative_does_not_accept():
    ctx, threads = setup([supplier(), buyer(), supplier(status="final_offer", n=1)], alt_c=1.0)
    assert rule_tactic(ctx, threads).action == "walk_away"


def test_wants_from_reads_supplier_words():
    text = "Faster payment would help our cash flow. With a firm commitment on volumes we can review the price."
    assert set(wants_from(text)) >= {"payment_days", "volume_per_year"}


def test_rule_analyst_when_supplier_irritated_raises_walk_risk():
    ctx, threads = setup(
        [supplier(), buyer(), supplier(n=1, body="Without any movement on your side, this is our last price.")]
    )
    read = rule_read(ctx, threads)[SID]
    assert read.walk_risk > 0.2 and any("without any movement" in s.lower() for s in read.signals)


def test_llm_strategist_when_fields_are_malformed_never_raises():
    ctx, threads = setup([supplier(), buyer(), supplier(n=1)])
    bad = [
        {"read": [1, 2], "tactic": {"action": "send", "claims": 3, "levers": "unit_price"}},
        {"read": {"wants": 5, "signals": None}, "tactic": {"supplier_id": ["x"], "action": "send"}},
        {"read": "open", "tactic": {"action": "send", "claims": [True, None, 2.5]}},
    ]
    for answer in bad:
        reads = rule_read(ctx, threads)
        tactic = LLMStrategist(lambda s, u, a=answer: json.dumps(a)).choose(ctx, threads, reads)
        assert tactic.supplier_id == SID and tactic.action in ("send", "accept")
        assert all(isinstance(i, int) and not isinstance(i, bool) for i in tactic.claims)


def test_negotiable_levers_keeps_index_clause_on_rising_paths_c_decides():
    ctx, _ = setup([supplier()])
    case = {**ctx.case, "negotiable": [*ctx.case["negotiable"], "index_clause"]}
    rising = replace(ctx, case={**case, "buyer": {**case["buyer"], "index_paths": {"AS-14404": 0.06}}})
    assert "index_clause" in negotiable_levers(rising, ["unit_price", "index_clause"])  # P18: the 24-month package


def at_target(ctx, threads, fit):
    """The context with a band whose target its standing sheet already meets, and the given price fit."""
    band = ctx.bands[SID]
    return replace(ctx, bands={SID: replace(band, c_target=1e12)}, fits={SID: fit})


def test_accept_when_at_target_but_still_moving_keeps_trading():
    ctx, threads = setup([supplier(), buyer(), supplier(price=17.0, n=1)])
    moving = at_target(ctx, threads, ConcessionFit(SID, (17.4, 17.0), 16.0, 2.3, False, p_room=0.7))
    assert not accept_ready(moving, SID, threads[SID])
    tactic = safe(moving, threads, {"action": "accept"}, rule_tactic(moving, threads))
    assert tactic.action == "send" and "still moving" in tactic.rationale


def test_accept_when_at_target_and_stopped_moving_accepts():
    ctx, threads = setup([supplier(), buyer(), supplier(price=17.0, n=1)])
    stopped = at_target(ctx, threads, ConcessionFit(SID, (17.4, 17.0, 16.99), 16.98, 0.06, True, p_room=0.1))
    assert accept_ready(stopped, SID, threads[SID])
    assert safe(stopped, threads, {"action": "accept"}, rule_tactic(stopped, threads)).action == "accept"


def at_floor_band(ctx, floor):
    band = ctx.bands[SID]
    return replace(ctx, bands={SID: replace(band, floor=floor, c_target=0.0)})


def test_accept_when_price_at_floor_accepts_even_if_still_moving():
    ctx, threads = setup([supplier(), buyer(), supplier(price=16.0, n=1)])
    ctx = replace(at_floor_band(ctx, 16.2), fits={SID: ConcessionFit(SID, (17.4, 16.0), 15.5, 8.0, False, 0.9)})
    assert accept_ready(ctx, SID, threads[SID])
    assert safe(ctx, threads, {"action": "accept"}, rule_tactic(ctx, threads)).action == "accept"


def test_accept_when_opening_quote_at_floor_still_counters():
    ctx, threads = setup([supplier(price=16.0)])
    ctx = at_floor_band(ctx, 16.2)
    assert not accept_ready(ctx, SID, threads[SID])  # the opening quote is the score's zero line
    assert rule_tactic(ctx, threads).action == "send"


def test_accept_when_price_at_floor_but_alternative_cheaper_does_not_accept():
    ctx, threads = setup([supplier(), buyer(), supplier(price=16.0, n=1)], alt_c=1.0)
    assert not accept_ready(at_floor_band(ctx, 16.2), SID, threads[SID])


def test_usable_claims_when_rival_quote_or_offer_drops_them():
    rivals = (
        Claim("Another supplier quoted EUR 15.10 (quote:Q1).", ("quote:Q1",), "rival_quote", 15.1, True, 0.9),
        Claim("Another supplier offers EUR 15.00 (offer:2-1).", ("offer:2-1",), "rival_offer", 15.0, True, 0.9),
    )
    ctx, _ = setup([supplier()])
    ctx = replace(ctx, claims={SID: (*CLAIMS, *rivals)})
    kinds = {c.kind for _, c in usable_claims(ctx, SID, set())}
    assert kinds == {"index_change", "scorecard"}


# ---- what the model sees ------------------------------------------------------------------------------------


def seen_thread(ctx, threads):
    seen = {}

    def complete(system, user):
        seen["user"] = json.loads(user)
        return "{}"

    LLMStrategist(complete).choose(ctx, threads, rule_read(ctx, threads))
    assert "threads" not in seen["user"]  # one supplier, never a list of bidders
    return seen["user"]["thread"]


def test_choose_when_fit_known_shows_where_the_supplier_stops_and_its_room():
    ctx, threads = setup([supplier(17.4), buyer(), supplier(16.9, n=1)])
    fit = ConcessionFit(SID, (17.4, 16.9), 16.2, -2.9, True, 0.35)
    view = seen_thread(replace(ctx, fits={SID: fit}), threads)
    assert view["concession"]["reservation_estimate"] == 16.2
    assert view["concession"]["p_room"] == 0.35


def test_choose_when_trades_priced_shows_them_with_their_euro_value():
    ctx, threads = setup([supplier()])
    trade = Trade("payment_days", 30, 60, 1234.0, 0.21)
    view = seen_thread(replace(ctx, trades={SID: (trade,)}), threads)
    assert view["trades"] == [{"field": "payment_days", "theirs": 30, "ours": 60, "eur": 1234.0, "per_unit": 0.21}]


def test_llm_strategist_prompt_embeds_analyst_guide():
    system = LLMStrategist(None).system
    assert "{analyst}" not in system
    assert "READING THE SUPPLIER" in system and "p_room" in system and "walk_risk" in system
    assert '"tactic"' in system  # the JSON braces of the reply format survive the substitution


def test_standing_c_when_supplier_estimate_known_includes_relationship_charge():
    import copy
    from types import SimpleNamespace

    from arena_kit.agents.buyer.roles.strategist import standing_c
    from arena_kit.cost import closing_terms, total_cost

    case = copy.deepcopy(CASE)
    case["buyer"].update(category_goal="stabilise", relationship_eur_per_point=1000, sustainable_margin_pct=8)
    events = [supplier(), buyer(), supplier(price=16.5, n=1)]
    terms = events[-1]["term_sheets"][0]["terms"]
    estimate = {"full_cost": 18, "interest_rate": 0.08, "standard": closing_terms(case, terms)}
    with_estimate = SimpleNamespace(case=case, supplier_estimates={SID: estimate})
    without = SimpleNamespace(case=case)  # before Jeddou's contracts field lands: no charge, no crash
    assert standing_c(with_estimate, events, SID) == total_cost(case, terms, supplier=estimate)
    assert standing_c(with_estimate, events, SID) > standing_c(without, events, SID)
