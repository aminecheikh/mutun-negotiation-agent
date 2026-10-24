"""The improvements from the replay study: arguments ranked by what changes a supplier's calculation, concession
discipline in code (relative tit-for-tat, half the gap, a visible step, AC_next), precomputed specialist inputs,
anchor and lever guidance in the frame, and our enquiry's own figures allowed in e-mails."""

import json
import time

from v2_support import S01, SID, ours, quote, scripted, state_for

from arena_kit.agents.buyer.contracts import Claim
from arena_kit.agents.buyer.tools import DataTools
from arena_kit.agents.buyer_v2 import agents as A
from arena_kit.agents.buyer_v2 import facts as F
from arena_kit.agents.buyer_v2 import guard
from arena_kit.agents.buyer_v2.brain import Brain
from arena_kit.agents.buyer_v2.config import V2Config
from arena_kit.agents.buyer_v2.llm import LLM
from arena_kit.agents.buyer_v2.toolbox import ToolContext, toolset


def claim(kind, strength, ref):
    return Claim(f"Fact about {kind} ({ref}).", (ref,), kind, -1.0, True, strength)  # an index that fell


def test_arguments_rank_what_moves_suppliers_first():
    st = state_for(S01, {SID: [quote(SID, 0, 17.4)]})
    st.ctx = st.ctx.__class__(**{**st.ctx.__dict__, "claims": {SID: (
        claim("po_history", 1.0, "po:1"), claim("scorecard", 0.9, "sc:1"), claim("index_change", 0.6, "idx:A@2026-01"),
        claim("should_cost", 0.5, "cost:1"),
    )}})  # fmt: skip
    kinds = [a.kind for a in F.arguments(st, SID)]
    assert kinds == ["index_change", "should_cost", "scorecard", "po_history"]


def test_a_rising_cost_driver_ranks_below_a_falling_one():
    st = state_for(S01, {SID: [quote(SID, 0, 17.4)]})
    rose = Claim("Index A changed +2.7% (idx:A@2026-01).", ("idx:A@2026-01",), "index_change", 2.7, True, 0.9)
    fell = Claim("Index B changed -3.7% (idx:B@2026-01).", ("idx:B@2026-01",), "index_change", -3.7, True, 0.5)
    st.ctx = st.ctx.__class__(**{**st.ctx.__dict__, "claims": {SID: (rose, fell)}})
    assert [a.refs[0] for a in F.arguments(st, SID)] == ["idx:B@2026-01", "idx:A@2026-01"]


# ---- concession discipline ----------------------------------------------------------------------------------------


def two_rounds(sup_second=17.0, our_first=16.0):
    events = [quote(SID, 0, 17.4), ours({"unit_price": our_first, "payment_days": 30}), quote(SID, 1, sup_second)]
    st = state_for(S01, {SID: events})
    return st, F.thread_facts(st, SID, events)


def test_no_step_limit_before_our_first_offer():
    st = state_for(S01, {SID: [quote(SID, 0, 17.4)]})
    assert guard.step_limit(st, F.thread_facts(st, SID, [quote(SID, 0, 17.4)])) is None


def test_our_step_is_at_most_its_last_step_and_half_the_gap():
    st, f = two_rounds(sup_second=17.0, our_first=16.0)
    its_step = f.supplier_cs[-2] - f.supplier_cs[-1]
    gap = f.standing.c - f.our_last_c
    limit = guard.step_limit(st, f)
    assert its_step > 0 and limit == f.our_last_c + min(its_step, 0.5 * gap)
    checked = guard.check_counter(st, f, [{"unit_price": 16.95, "payment_days": 30}])  # almost its price
    assert checked.sheets and F.cost(st, SID, checked.sheets[0], f.std) <= limit + 1.0
    assert any("step capped" in n for n in checked.notes)


def test_a_visible_step_is_allowed_when_it_stood_still():
    events = [quote(SID, 0, 17.4), ours({"unit_price": 16.0, "payment_days": 30}), quote(SID, 1, 17.4)]
    st = state_for(S01, {SID: events})
    f = F.thread_facts(st, SID, events)
    limit = guard.step_limit(st, f)
    assert f.our_last_c < limit <= f.our_last_c + st.config.step_min_rel * f.standing.c + 1e-6


def test_a_step_inside_the_limit_is_left_alone():
    st, f = two_rounds(sup_second=17.0, our_first=16.0)
    checked = guard.check_counter(st, f, [{"unit_price": 16.1, "payment_days": 30}])
    assert checked.sheets[0]["unit_price"] == 16.1 and not any("step capped" in n for n in checked.notes)


def test_ac_next_accepts_when_our_next_offer_would_be_no_better_than_its_sheet():
    def meet_it(messages):
        body = (
            "Dear Sir or Madam,\n\nthank you for your offer, which we have reviewed carefully and attach our "
            "proposal.\n\nKind regards"
        )
        return {"decision": "counter", "reasoning": "close the gap", "arguments": [], "email_subject": "Re",
                "email_body": body, "counter_offers": [{"unit_price": 17.0, "terms": [], "label": "x"}]}  # fmt: skip

    model = scripted(answers={"negotiator": meet_it})
    cfg = V2Config(log_dir="off", analyst_model="m", negotiator_model="m")
    brain = Brain(DataTools(), LLM(model, cfg), cfg)
    events = [quote(SID, 0, 17.4), ours({"unit_price": 16.0, "payment_days": 30}), quote(SID, 1, 17.0)]
    st = brain.open_case(S01, {SID: events})
    move = brain.decide(st, SID, {SID: events}, time.monotonic())
    assert move.kind == "accept" and "AC_next" in move.reason


# ---- precomputed inputs and the frame -------------------------------------------------------------------------------


def test_specialists_get_their_numbers_precomputed_and_one_tool_turn():
    events = [quote(SID, 0, 17.4), ours({"unit_price": 16.0, "payment_days": 30}), quote(SID, 1, 17.0)]
    st = state_for(S01, {SID: events})
    f = F.thread_facts(st, SID, events)
    ctx = ToolContext(st, {SID: events}, None, focus=SID)
    for role, spec in A.SPECS.items():
        pre = A.precomputed(role, toolset(list(spec.tools), ctx), f)
        text = A.analyst_input(role, st, f, events, pre)
        for name in spec.preload:
            assert f"[{name}(" in text, (role, name)
        assert spec.tool_turns == 1
    deal = A.analyst_input("deal", st, f, events, A.precomputed("deal", toolset(list(A.SPECS["deal"].tools), ctx), f))
    assert "LEVERS WORTH THE MOST TO US" in deal and "[propose_counter(" in deal


def test_the_negotiator_sees_the_offer_engines_own_counter():
    events = [quote(SID, 0, 17.4)]
    st = state_for(S01, {SID: events})
    f = F.thread_facts(st, SID, events)
    ctx = ToolContext(st, {SID: events}, None, focus=SID)
    pre = A.precomputed("negotiator", toolset(list(A.NEGOTIATOR_TOOLS), ctx), f)
    text = A.negotiator_input(st, f, events, {}, pre)
    assert "[propose_counter(" in text and "LOWEST CREDIBLE PRICE" in text


def test_the_frame_gives_anchor_guidance_in_round_one_and_the_allowed_step_later():
    st = state_for(S01, {SID: [quote(SID, 0, 17.4)]})
    first = A.decision_frame(st, F.thread_facts(st, SID, [quote(SID, 0, 17.4)]))
    assert "YOUR FIRST COUNTER" in first and "LOWEST CREDIBLE PRICE" in first and "LEVERS WORTH THE MOST" in first
    st, f = two_rounds()
    later = A.decision_frame(st, f)
    assert "THIS ROUND'S STEP" in later and "may cost us at most" in later and "never did" in later


def test_the_models_json_record_carries_the_precomputed_block(tmp_path, monkeypatch):
    monkeypatch.setenv("BUYER_LLM_LOG_DIR", str(tmp_path))
    from arena_kit.agents.buyer_v2.trail import Trail

    model = scripted()
    cfg = V2Config(log_dir="off", analyst_model="m", negotiator_model="m")
    trail = Trail("off")
    brain = Brain(DataTools(), LLM(model, cfg, recorder=trail.llm_call), cfg, None, trail)
    events = [quote(SID, 0, 17.4)]
    st = brain.open_case(S01, {SID: events})
    brain.decide(st, SID, {SID: events}, time.monotonic())
    recs = [json.loads(p.read_text(encoding="utf-8")) for p in tmp_path.glob("*/*-v2-deal-*.json")]
    assert recs and "TOOL RESULTS ALREADY FETCHED" in recs[0]["user"]


# ---- a self-contradicting cost floor, and downloads off the move's path -----------------------------------------


def test_a_floor_above_the_quote_is_not_treated_as_evidence_where_we_are_told_to_push():
    import copy

    case = copy.deepcopy(S01)
    case["buyer"]["category_goal"] = "leverage"
    st = state_for(case, {SID: [quote(SID, 0, 17.4)]})
    band = st.ctx.bands[SID]
    st.ctx = st.ctx.__class__(**{**st.ctx.__dict__, "bands": {SID: band.__class__(**{**band.__dict__, "floor": 99.0})}})
    assert not F.floor_is_credible(st, SID, 17.4), "a floor above its own quote contradicts itself"
    assert F.floor_is_credible(st, SID, 120.0), "below the quote it is ordinary evidence"
    f = F.thread_facts(st, SID, [quote(SID, 0, 17.4)])
    assert "CAUTION" in A.decision_frame(st, f)


def test_a_squeezed_supplier_in_a_relationship_case_keeps_its_floor():
    import copy

    case = copy.deepcopy(S01)
    case["buyer"]["category_goal"] = "stabilise"
    st = state_for(case, {SID: [quote(SID, 0, 17.4)]})
    band = st.ctx.bands[SID]
    st.ctx = st.ctx.__class__(**{**st.ctx.__dict__, "bands": {SID: band.__class__(**{**band.__dict__, "floor": 99.0})}})
    assert F.floor_is_credible(st, SID, 17.4), "C charges us for squeezing here, so the floor stands"


def test_a_slow_document_download_never_delays_the_decision():
    """Run 4: one attachment GET blocked a worker 76 s between the reply and the decision."""
    import threading
    import time as _t

    from arena_kit.agents.buyer_v2.runner import CaseRun, V2Agent
    from arena_kit.agents.buyer_v2.trail import Trail

    started, release = threading.Event(), threading.Event()

    class SlowDocs:
        session_id = "x"

        def attachment(self, attachment_id):
            started.set()
            release.wait(timeout=5)
            return b"late"

        def result(self):
            return {"ended": False}

    cfg = V2Config(log_dir="off")
    run = CaseRun(V2Agent(Brain(DataTools(), None, cfg), cfg, Trail("off")), SlowDocs(), S01,
                  {SID: [quote(SID, 0, 17.4)]})  # fmt: skip
    reply = {"attachments": [{"attachment_id": "a1", "name": "quote.pdf"}]}
    t0 = _t.monotonic()
    run.fetch_later(reply)
    assert _t.monotonic() - t0 < 0.5, "the download must not run on the caller's thread"
    assert started.wait(timeout=5)
    release.set()


# ---- hallucination: a figure an agent states is never taken as fact -------------------------------------------


def test_invented_specialist_figures_are_replaced_by_the_computed_ones():
    events = [quote(SID, 0, 17.4), ours({"unit_price": 16.0, "payment_days": 30}), quote(SID, 1, 17.0)]
    st = state_for(S01, {SID: events})
    f = F.thread_facts(st, SID, events)
    band = st.ctx.bands[SID]
    reports = {
        "market": {"fair_price": 999.0, "floor_estimate": band.floor, "credible_lowest_price": 1.0,
                   "ambitious_target_price": None},
        "deal": {"standing_c": 12345.0, "accepted_our_offer": True, "standing_acceptable": False},
    }  # fmt: skip
    notes = A.check_numbers(st, f, reports)
    # fair is either computed (then the invented 999 is replaced) or unknown (then it is dropped, never trusted)
    assert reports["market"]["fair_price"] == (round(band.fair, 4) if band.fair else None)
    assert reports["market"]["floor_estimate"] == band.floor, "a figure that already matches is left alone"
    assert reports["market"]["credible_lowest_price"] == round(st.ctx.credibility[SID].lowest_price, 4)
    assert reports["deal"]["standing_c"] == round(f.standing.c, 4)
    assert reports["deal"]["accepted_our_offer"] is False and reports["deal"]["standing_acceptable"] is f.acceptable
    assert len(notes) >= 4 and all("corrected" in n or "dropped" in n for n in notes)


def test_a_figure_code_cannot_compute_is_dropped_not_trusted():
    events = [quote(SID, 0, 17.4)]
    st = state_for(S01, {SID: events})
    f = F.thread_facts(st, SID, events)
    st.ctx = st.ctx.__class__(**{**st.ctx.__dict__, "bands": {}, "credibility": {}})
    reports = {"market": {"fair_price": 42.0}}
    notes = A.check_numbers(st, f, reports)
    assert reports["market"]["fair_price"] is None and "not computable" in notes[0]


def test_corrections_are_recorded_in_the_trail(tmp_path, monkeypatch):
    from arena_kit.agents.buyer_v2.trail import Trail

    def wrong_market(messages):
        from v2_support import MARKET

        return {**MARKET, "fair_price": 999.0}

    model = scripted(answers={"market": wrong_market})
    cfg = V2Config(log_dir="off", analyst_model="m", negotiator_model="m")
    brain = Brain(DataTools(), LLM(model, cfg), cfg, None, Trail("off"))
    events = [quote(SID, 0, 17.4)]
    st = brain.open_case(S01, {SID: events})
    move = brain.decide(st, SID, {SID: events}, time.monotonic())
    assert any("fair_price" in c for c in move.detail["agents"].get("corrected_figures", []))


# ---- e-mails: our enquiry's figures -------------------------------------------------------------------------------


def test_our_enquiry_figures_may_be_stated_in_an_email():
    case = dict(S01, fixed_terms={**S01.get("fixed_terms", {}), "volume_per_year": 6110})
    st = state_for(case, {SID: [quote(SID, 0, 17.4)]})
    f = F.thread_facts(st, SID, [quote(SID, 0, 17.4)])
    body = (
        "Dear Sir or Madam,\n\nthank you for your question about the volume. We can commit to the full 6110 pieces "
        "a year of our enquiry, as our packages state, and look forward to your view on them.\n\nKind regards"
    )
    cleaned, _, notes = guard.clean_email(st, f, [], body, [], [{"unit_price": 16.5}], [])
    assert cleaned and "6110" in cleaned and not any("figures" in n for n in notes)
