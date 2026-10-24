"""v2 deterministic core: the switch, term labels, thread facts (acceptance by terms), the guard's hard limits on
counters and e-mails. No network, no model."""

import copy
import math

import pytest
from v2_support import PRACTICE, S01, S02, SID, ours, quote, state_for

from arena_kit.agents.buyer_v2 import facts as F
from arena_kit.agents.buyer_v2 import guard
from arena_kit.agents.buyer_v2 import terms as T
from arena_kit.agents.buyer_v2.config import V2Config, agentic_structure

# ---- the switch ------------------------------------------------------------------------------------------------


def test_agentic_structure_defaults_to_v2_and_follows_the_environment(monkeypatch):
    for name in ("AGENTIC_STRUCTURE", "Agentic_structure", "agentic_structure"):
        monkeypatch.delenv(name, raising=False)
    assert agentic_structure() == 1
    monkeypatch.setenv("AGENTIC_STRUCTURE", "0")
    assert agentic_structure() == 0
    monkeypatch.setenv("AGENTIC_STRUCTURE", "1")
    assert agentic_structure() == 1


def test_cli_buyer_follows_the_switch(monkeypatch):
    from arena_kit.cli import buyer_version

    monkeypatch.setenv("AGENTIC_STRUCTURE", "1")
    assert buyer_version("buyer") == 2
    monkeypatch.setenv("AGENTIC_STRUCTURE", "0")
    assert buyer_version("buyer") == 1
    assert buyer_version("buyer_v2") == 2 and buyer_version("buyer_v1") == 1 and buyer_version("naive") is None


def test_config_reads_v2_environment_overrides(monkeypatch):
    monkeypatch.setenv("V2_DECIDE_BY_S", "40")
    monkeypatch.setenv("V2_ANALYST_EFFORT", "medium")
    cfg = V2Config.from_env()
    assert cfg.decide_by_s == 40.0 and cfg.analyst_effort == "medium"
    assert cfg.analysts_by_s < cfg.decide_by_s < cfg.move_budget_s < 60


# ---- term labels ---------------------------------------------------------------------------------------------


P02 = next(c for c in PRACTICE if c["case_id"] == "case:P02")


def test_labels_round_trip_for_every_option_of_every_practice_case():
    for case in PRACTICE:
        for field, options in (case.get("term_options") or {}).items():
            for value in options:
                parsed, problem = T.parse_value(case, field, T.label(value), {}, {})
                assert problem is None and parsed == value, (case["case_id"], field, value)


def test_parse_value_accepts_the_suppliers_standard_and_refuses_the_rest():
    std = {"payment_days": 21}
    assert T.parse_value(P02, "payment_days", "21", {}, std) == (21, None)
    assert T.parse_value(P02, "payment_days", "as supplier", {"payment_days": 45}, std) == (45, None)
    value, problem = T.parse_value(P02, "payment_days", "75", {}, std)
    assert value is None and "not an allowed value" in problem
    assert T.parse_value(P02, "index_clause", "none", {}, {}) == (None, None)


def test_parse_sheet_keeps_supplier_values_for_fields_not_named():
    sheet, problems = T.parse_sheet(S01, 16.5, [], {"unit_price": 17.4, "payment_days": 45}, {})
    assert sheet == {"payment_days": 45, "unit_price": 16.5} and not problems
    sheet, problems = T.parse_sheet(S01, "x", [{"field": "colour", "value": "red"}], {}, {})
    assert sheet["unit_price"] is None and len(problems) == 2


# ---- facts: acceptance is recognised by terms ------------------------------------------------------------


def facts_for(case, events, sid=SID, **config):
    st = state_for(case, {sid: events}, **config)
    return st, F.thread_facts(st, sid, events)


def test_agreed_status_is_an_acceptance():
    events = [quote(SID, 0, 17.4), ours({"unit_price": 16.5, "payment_days": 30}), quote(SID, 1, 16.5, "agreed")]
    _, f = facts_for(S01, events)
    assert f.accepted_ours and f.acceptable


def test_open_status_with_our_terms_is_an_acceptance():
    mine = {"unit_price": 16.5, "payment_days": 60}
    events = [quote(SID, 0, 17.4), ours(mine, {"unit_price": 16.3, "payment_days": 30}),
              quote(SID, 1, 16.3, "open", body="We can accept your second package.")]  # fmt: skip
    _, f = facts_for(S01, events)
    assert f.accepted_ours, "a supplier that copies our package accepted it, whatever its status"


def test_a_sheet_cheaper_for_us_than_our_offer_is_an_acceptance():
    events = [quote(SID, 0, 17.4), ours({"unit_price": 16.5, "payment_days": 30}), quote(SID, 1, 16.4, "final_offer")]
    _, f = facts_for(S01, events)
    assert f.accepted_ours


def test_a_higher_counter_is_not_an_acceptance():
    events = [quote(SID, 0, 17.4), ours({"unit_price": 16.5, "payment_days": 30}), quote(SID, 1, 17.0)]
    _, f = facts_for(S01, events)
    assert not f.accepted_ours and f.accepted_alternative is None


def test_our_package_as_its_alternative_sheet_is_recognised():
    mine = {"unit_price": 16.5, "payment_days": 30}
    events = [quote(SID, 0, 17.4), ours(mine), quote(SID, 1, 17.0, extra_sheets=[mine])]
    _, f = facts_for(S01, events)
    assert not f.accepted_ours and f.accepted_alternative is not None and f.accepted_alternative.price == 16.5


def test_acceptability_refuses_dearer_than_opening_alternative_must_have_or_refused():
    events = [quote(SID, 0, 17.4), ours({"unit_price": 15.0, "payment_days": 30}), quote(SID, 1, 17.6, "final_offer")]
    _, f = facts_for(S01, events)
    assert not f.acceptable and "best opening" in f.why_not
    alt = copy.deepcopy(S01)
    alt["buyer"]["alternative"] = {"kind": "quote", "ref": None, "terms": {"unit_price": 16.0, "payment_days": 30}}
    events = [quote(SID, 0, 17.4), ours({"unit_price": 15.0, "payment_days": 30}), quote(SID, 1, 16.9)]
    _, f = facts_for(alt, events)
    assert not f.acceptable and "alternative" in f.why_not
    must = copy.deepcopy(S01)
    must["buyer"]["must_haves"] = [{"field": "payment_days", "op": ">=", "value": 45}]
    _, f = facts_for(must, [quote(SID, 0, 17.4), ours({"unit_price": 15.0, "payment_days": 60}), quote(SID, 1, 16.9)])
    assert not f.acceptable and "must-have" in f.why_not
    st, f = facts_for(S01, [quote(SID, 0, 17.4), ours({"unit_price": 15.0, "payment_days": 30}), quote(SID, 1, 16.9)])
    st.refused.add(f.standing.id)
    assert not F.thread_facts(
        st, SID, [quote(SID, 0, 17.4), ours({"unit_price": 15.0}), quote(SID, 1, 16.9)]
    ).acceptable


def test_the_opening_quote_is_never_accepted_before_our_first_answer():
    st, f = facts_for(S01, [quote(SID, 0, 17.4)])
    ok, why = guard.accept_ok(st, f)
    assert not ok and "opening" in why


@pytest.mark.parametrize("case", PRACTICE, ids=lambda c: c["case_id"])
def test_facts_on_every_practice_case_price_the_opening_and_never_crash(case):
    threads = {sid: [quote(sid, 0, 100.0)] for sid in case["suppliers"]}
    st = state_for(case, threads)
    for sid, events in threads.items():
        f = F.thread_facts(st, sid, events)
        assert f.standing is not None and not math.isinf(f.standing.c)
        assert f.our_rounds == 0 and not f.accepted_ours


# ---- the guard: counters -----------------------------------------------------------------------------------


def counter_facts(**config):
    events = [quote(SID, 0, 17.4), ours({"unit_price": 15.8, "payment_days": 30}), quote(SID, 1, 17.0)]
    return facts_for(S01, events, **config)


def test_counter_above_the_suppliers_price_is_capped():
    st, f = counter_facts()
    checked = guard.check_counter(st, f, [{"unit_price": 19.0, "payment_days": 60}])
    assert checked.sheets and checked.sheets[0]["unit_price"] <= 17.0
    assert any("above its offer" in n for n in checked.notes)


def test_every_counter_costs_us_less_than_its_standing_sheet():
    st, f = counter_facts()
    checked = guard.check_counter(st, f, [{"unit_price": 17.0, "payment_days": 14}, {"unit_price": 16.2}])
    for sheet in checked.sheets:
        assert F.cost(st, SID, sheet, f.std) < f.standing.c and sheet["unit_price"] <= f.standing.price
    assert any("not less than its own offer" in n or "step capped" in n for n in checked.notes)


def test_counter_never_takes_back_our_current_offer():
    st, f = counter_facts()
    checked = guard.check_counter(st, f, [{"unit_price": 14.0, "payment_days": 30}])
    assert checked.sheets and F.cost(st, SID, checked.sheets[0], f.std) >= f.our_last_c * (1 - 1e-3)
    assert checked.sheets[0]["unit_price"] >= 15.8 - 0.01


def test_counter_respects_the_credible_floor():
    st, f = facts_for(S01, [quote(SID, 0, 17.4)])
    floor = st.ctx.credibility[SID].lowest_price
    checked = guard.check_counter(st, f, [{"unit_price": round(floor * 0.7, 2), "payment_days": 30}])
    assert checked.sheets and checked.sheets[0]["unit_price"] >= floor


def test_counter_breaking_a_must_have_is_dropped_and_at_most_three_distinct_sheets_go_out():
    must = copy.deepcopy(S01)
    must["buyer"]["must_haves"] = [{"field": "payment_days", "op": ">=", "value": 45}]
    st, f = facts_for(must, [quote(SID, 0, 17.4, days=60)])
    proposals = [{"unit_price": 16.9, "payment_days": d} for d in (14, 30, 45, 60, 90, 60)]
    checked = guard.check_counter(st, f, proposals)
    assert all(s["payment_days"] >= 45 for s in checked.sheets) and 1 <= len(checked.sheets) <= 3


@pytest.mark.parametrize("case", PRACTICE, ids=lambda c: c["case_id"])
def test_guard_properties_on_every_practice_case(case):
    sid = case["suppliers"][0]
    threads = {s: [quote(s, 0, 100.0)] for s in case["suppliers"]}
    st = state_for(case, threads)
    f = F.thread_facts(st, sid, threads[sid])
    proposals = [{"unit_price": p} for p in (40.0, 80.0, 99.0, 100.0, 130.0)]
    for sheet in guard.check_counter(st, f, proposals).sheets:
        assert set(sheet) == set(case["negotiable"])
        assert sheet["unit_price"] <= 100.0
        c = F.cost(st, sid, sheet, f.std)
        assert c < f.standing.c and c <= st.c_open
        assert st.c_alt is None or c <= st.c_alt


# ---- the guard: e-mails ------------------------------------------------------------------------------------------


def email_state():
    st, f = facts_for(S02, [quote(S02["suppliers"][0], 0, 17.2, lead_time_days=42)], sid=S02["suppliers"][0])
    return st, f


def test_email_tokens_become_the_verified_sentence_and_unknown_ids_leaks_rivals_and_figures_go():
    st, f = email_state()
    args = F.arguments(st, f.supplier_id)
    assert args, "S02 has verified arguments"
    body = (
        "Dear Sir or Madam,\n\nthank you for your quote and the reliable cooperation over the years.\n"
        f"[[{args[0].token}]]\n"
        "The index idx:FAKE@2026-01 shows 12.5% more.\n"
        "We need this urgently.\n"
        "Another supplier offered us less.\n"
        "Our volume will grow by 37 percent.\n"
        "We propose the packages attached and look forward to your answer.\n\n"
        "Kind regards\nHollerau Pumpenwerke, Purchasing"
    )
    sheets = [{"unit_price": 16.5, "payment_days": 60, "lead_time_days": 42}]
    cleaned, refs, notes = guard.clean_email(st, f, [], body, [args[0].token], sheets, ["Motorenwerk Rivale"])
    assert args[0].sentence in cleaned and set(refs) == set(args[0].refs)
    for gone in ("idx:FAKE", "urgently", "Another supplier", "37 percent"):
        assert gone not in cleaned, gone
    assert len(notes) >= 4


def test_email_missing_token_is_inserted_and_a_gutted_body_is_refused():
    st, f = email_state()
    token = F.arguments(st, f.supplier_id)[0].token
    body = "Dear Sir or Madam,\n\nthank you for your offer; we have considered it with care and propose the attached packages for your review.\n\nKind regards\nHollerau Pumpenwerke, Purchasing"  # noqa: E501
    cleaned, _, _ = guard.clean_email(st, f, [], body, [token], [{"unit_price": 16.5}], [])
    assert F.arguments(st, f.supplier_id)[0].sentence in cleaned
    assert guard.clean_email(st, f, [], "Urgent! Deadline tomorrow.", [], [], [])[0] is None


def test_reserve_drops_an_argument_another_thread_cited_meanwhile():
    st, f = email_state()
    arg = F.arguments(st, f.supplier_id)[0]
    st.burned |= set(arg.refs)
    message = {"body": f"Hello.\n{arg.sentence}\nRegards", "refs": list(arg.refs), "term_sheets": []}
    out = guard.reserve(st, message, list(arg.refs))
    assert arg.sentence not in out["body"] and not out["refs"]
