"""The decision agent on top of the planner (roles/strategist.py): its safety net, its rule fallback and its parsing.
No network: the model is a function returning text."""

import json
import time
from dataclasses import replace
from pathlib import Path

from arena_kit.agents.buyer.contracts import BehaviourRead, Claim, ConcessionFit
from arena_kit.agents.buyer.roles import LLMStrategist, RuleAnalyst, RuleStrategist
from arena_kit.agents.buyer.roles.analyst import wants_from
from arena_kit.agents.buyer.roles.strategist import refined_read, safe, usable_claims
from arena_kit.agents.buyer.tools import DataTools

CASE = json.loads((Path(__file__).parents[1] / "arena_kit/sim_cases/S01.json").read_text())
SID = "sup:70270"
CLAIMS = (
    Claim("The A index changed -3.7% from 2025-08 to 2026-08 (idx:A@2025-08, idx:A@2026-08).",
          ("idx:A@2025-08", "idx:A@2026-08"), "index", -3.7, True, 1.0),
    Claim("On-time delivery was 63.5% (sc:70270@2026Q3).", ("sc:70270@2026Q3",), "delivery", 63.5, True, 0.6),
    Claim("The demand forecast changed +12.0% (fc:1@2026-09).", ("fc:1@2026-09",), "volume", 12.0, True, 0.3),
)  # fmt: skip


def supplier(price=17.4, status="open", body="Our offer stands.", n=0):
    sheet = {"term_sheet_id": f"offer:70270-{n}", "terms": {"unit_price": price, "payment_days": 30}}
    return {"from": "sales@70270", "status": status, "term_sheets": [sheet], "attachments": [], "body": body}


def buyer(body="Our proposal.", refs=()):
    return {"from": "buyer", "body": body, "refs": list(refs), "term_sheets": [{"terms": {"unit_price": 15.0}}]}


def setup(events):
    ctx = DataTools().prepare(CASE, {SID: events[:1]})
    return replace(ctx, claims={SID: CLAIMS}), {SID: events}


def rule_read(ctx, threads):
    fit = ConcessionFit(SID, (17.4,), None, None, False)
    return {SID: RuleAnalyst().read(ctx, SID, threads[SID], fit)}


def rule_tactic(ctx, threads):
    return RuleStrategist().choose(ctx, threads, rule_read(ctx, threads))


# ---- safe(): the model can never break policy ------------------------------------------------------------------


def test_safe_when_accept_before_our_first_message_sends():
    ctx, threads = setup([supplier()])
    assert safe(ctx, threads, {"action": "accept"}, rule_tactic(ctx, threads)).action == "send"


def test_safe_when_model_walks_away_sends_the_policy_decides_walking():
    ctx, threads = setup([supplier(), buyer(), supplier(n=1)])
    assert safe(ctx, threads, {"action": "walk_away"}, rule_tactic(ctx, threads)).action == "send"


def test_safe_when_model_accepts_after_our_first_message_keeps_the_accept_for_the_policy():
    ctx, threads = setup([supplier(), buyer(), supplier(n=1)])
    assert safe(ctx, threads, {"action": "accept"}, rule_tactic(ctx, threads)).action == "accept"


def test_safe_when_model_pushes_for_price_keeps_the_push():
    ctx, threads = setup([supplier(), buyer(), supplier(price=17.0, n=1)])
    assert safe(ctx, threads, {"action": "send", "price": "push"}, rule_tactic(ctx, threads)).price == "push"


def test_safe_when_model_pushes_after_a_final_offer_follows_the_planner():
    ctx, threads = setup([supplier(), buyer(), supplier(price=17.0, status="final_offer", n=1)])
    assert safe(ctx, threads, {"action": "send", "price": "push"}, rule_tactic(ctx, threads)).price == "follow"


def test_safe_when_price_decision_is_missing_or_unknown_follows_the_planner():
    ctx, threads = setup([supplier(), buyer(), supplier(price=17.0, n=1)])
    for raw in ({"action": "send"}, {"action": "send", "price": "halve it"}, {"action": "send", "price": None}):
        assert safe(ctx, threads, raw, rule_tactic(ctx, threads)).price == "follow"


def test_safe_when_claims_are_unknown_or_already_cited_uses_the_rules_pick():
    ctx, threads = setup([supplier(), buyer(refs=["sc:70270@2026Q3"]), supplier(n=1)])
    rule = rule_tactic(ctx, threads)
    tactic = safe(ctx, threads, {"action": "send", "claims": [1, 7, "x"]}, rule)  # 1 was cited, 7 does not exist
    assert tactic.claims == rule.claims and 1 not in tactic.claims


def test_safe_keeps_the_rules_arguments_and_wording_whatever_the_model_picks():
    # practice runs: every change of arguments by the agent drew worse answers (P08, P11) - it cannot be bounded by C
    ctx, threads = setup([supplier(), buyer(), supplier(price=17.0, n=1)])
    rule = rule_tactic(ctx, threads)
    raw = {"action": "send", "claims": [2], "question": "Which contract length would improve your price?"}
    tactic = safe(ctx, threads, raw, rule)
    assert tactic.claims == rule.claims and tactic.question is None


def test_usable_claims_when_an_id_was_cited_leaves_that_argument_out():
    ctx, threads = setup([supplier(), buyer(refs=["idx:A@2025-08"])])
    assert [i for i, _ in usable_claims(ctx, SID, {"idx:A@2025-08"})] == [1, 2]


def test_refined_read_bounds_the_model_numbers_and_keeps_only_real_quotes():
    events = [supplier(body="Faster payment would help us.")]
    rule = BehaviourRead(SID, 0.5, 0.2, ("payment_days",), ())
    signals = ["Faster payment would help us.", "invented quote"]
    raw = {"p_more": 7, "walk_risk": -1, "wants": ["payment_days", "colour"], "signals": signals}
    read = refined_read(SID, raw, rule, events)
    assert read.p_more == 1.0 and read.walk_risk == 0.0 and read.wants == ("payment_days",)
    assert read.signals == ("Faster payment would help us.",)


# ---- RuleStrategist: the planner's step and the rules' arguments ------------------------------------------------


def test_rule_strategist_when_first_round_sends_two_arguments_no_question():
    ctx, threads = setup([supplier()])
    tactic = rule_tactic(ctx, threads)
    assert tactic.action == "send" and tactic.price == "follow" and tactic.question is None
    assert tactic.claims == (0, 1)


def test_rule_strategist_when_supplier_argues_with_costs_leads_with_a_falling_index():
    ctx, threads = setup([supplier(), buyer(refs=["fc:1@2026-09"]), supplier(body="Our material costs rose.", n=1)])
    tactic = rule_tactic(ctx, threads)
    assert tactic.claims[0] == 0 and 2 not in tactic.claims  # the cited forecast is never repeated


def test_rule_strategist_never_accepts_or_walks_the_policy_closes():
    ctx, threads = setup([supplier(), buyer(), supplier(status="final_offer", n=1)])
    assert rule_tactic(ctx, threads).action == "send"


def test_wants_from_reads_supplier_words():
    text = "Faster payment would help our cash flow. With a firm commitment on volumes we can review the price."
    assert set(wants_from(text)) >= {"payment_days", "volume_per_year"}


def test_rule_analyst_when_supplier_irritated_raises_walk_risk():
    ctx, threads = setup(
        [supplier(), buyer(), supplier(n=1, body="Without any movement on your side, this is our last price.")]
    )
    read = rule_read(ctx, threads)[SID]
    assert read.walk_risk > 0.2 and any("without any movement" in s.lower() for s in read.signals)


# ---- LLMStrategist: one call, parsed and bounded ----------------------------------------------------------------


def test_llm_strategist_when_model_answers_uses_its_read_and_tactic():
    ctx, threads = setup([supplier(), buyer(), supplier(price=17.0, n=1)])
    reads = rule_read(ctx, threads)
    read = {"p_more": 0.8, "walk_risk": 0.1, "wants": ["payment_days"], "signals": []}
    answer = {"read": read, "tactic": {"action": "send", "price": "push", "claims": [2], "rationale": "room"}}
    tactic = LLMStrategist(lambda s, u: json.dumps(answer)).choose(ctx, threads, reads)
    assert tactic.action == "send" and tactic.price == "push" and tactic.claims == rule_tactic(ctx, threads).claims
    assert reads[SID].p_more == 0.8 and reads[SID].wants == ("payment_days",)


def test_llm_strategist_input_carries_the_fair_price_and_no_removed_fields():
    ctx, threads = setup([supplier()])
    ctx = replace(ctx, fair={SID: 16.1})
    seen = {}

    def model(system, user):
        seen.update(json.loads(user))
        return "{}"

    LLMStrategist(model).choose(ctx, threads, rule_read(ctx, threads))
    thread = seen["thread"]
    assert thread["fair_price"] == 16.1 and "band" not in thread and "trades" not in thread
    assert '"price": "follow" | "trim" | "push"' in LLMStrategist().system


def test_llm_strategist_when_reply_is_garbage_returns_rule():
    ctx, threads = setup([supplier()])
    tactic = LLMStrategist(lambda s, u: "I think you should negotiate harder.").choose(
        ctx, threads, rule_read(ctx, threads)
    )
    assert tactic == rule_tactic(ctx, threads)


def test_llm_strategist_when_model_is_late_returns_rule_in_time():
    ctx, threads = setup([supplier()])

    def slow(system, user):
        time.sleep(2)
        return "{}"

    t0 = time.monotonic()
    tactic = LLMStrategist(slow, timeout_s=0.2).choose(ctx, threads, rule_read(ctx, threads))
    assert time.monotonic() - t0 < 1.5 and tactic.rationale.startswith("rule:")


def test_llm_strategist_when_fields_are_malformed_never_raises():
    ctx, threads = setup([supplier(), buyer(), supplier(n=1)])
    bad = [
        {"read": [1, 2], "tactic": {"action": "send", "claims": 3, "price": ["push"]}},
        {"read": {"wants": 5, "signals": None}, "tactic": {"supplier_id": ["x"], "action": "send"}},
        {"read": "open", "tactic": {"action": "send", "claims": [True, None, 2.5]}},
    ]
    for answer in bad:
        reads = rule_read(ctx, threads)
        tactic = LLMStrategist(lambda s, u, a=answer: json.dumps(a)).choose(ctx, threads, reads)
        assert tactic.supplier_id == SID and tactic.action in ("send", "accept") and tactic.price == "follow"
        assert all(isinstance(i, int) and not isinstance(i, bool) for i in tactic.claims)


def test_llm_strategist_when_model_adds_prose_after_its_json_still_parses_it():
    ctx, threads = setup([supplier(), buyer(), supplier(price=17.0, n=1)])
    answer = json.dumps({"tactic": {"action": "send", "price": "push", "claims": []}})
    reply = answer + "\n\nOn this supplier I push because {it still moves}."  # sweep 1: 3 answers lost to this
    tactic = LLMStrategist(lambda s, u: reply).choose(ctx, threads, rule_read(ctx, threads))
    assert tactic.price == "push"


def test_safe_passes_the_agent_package_on_only_with_a_message():
    ctx, threads = setup([supplier(), buyer(), supplier(16.9, n=1)])
    rule = rule_tactic(ctx, threads)
    sent = safe(ctx, threads, {"action": "send", "package": {"payment_days": 60}}, rule)
    assert sent.package == {"payment_days": 60}
    accepted = safe(ctx, threads, {"action": "accept", "package": {"payment_days": 60}}, rule)
    assert accepted.package is None
    assert safe(ctx, threads, {"action": "send", "package": ["payment_days"]}, rule).package is None
