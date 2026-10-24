"""v2 agents with a scripted model: the bounded tool loop, the fast paths that never ask a model, the negotiator kept
inside the hard limits, the rule fallback when a model is slow or wrong, and the prompts and schemas themselves."""

import re
import time

from v2_support import S01, S02, SID, ScriptedModel, negotiator_counter, ours, quote, scripted

from arena_kit.agents.buyer.tools import DataTools
from arena_kit.agents.buyer_v2 import agents as A
from arena_kit.agents.buyer_v2 import facts as F
from arena_kit.agents.buyer_v2.brain import Brain
from arena_kit.agents.buyer_v2.config import V2Config
from arena_kit.agents.buyer_v2.llm import LLM, AgentCall, parse_json


def brain_with(model, **config) -> Brain:
    cfg = V2Config(log_dir="off", **({"analyst_model": "m", "negotiator_model": "m"} | config))
    return Brain(DataTools(), LLM(model, cfg), cfg)


def decide(brain, case, threads, sid=SID, received_at=None):
    st = brain.open_case(case, threads)
    return st, brain.decide(st, sid, threads, received_at if received_at is not None else time.monotonic())


# ---- the model layer -------------------------------------------------------------------------------------


class Tools:
    schemas = [{"name": "echo", "description": "echo", "input_schema": {"type": "object", "properties": {}}}]

    def __init__(self):
        self.calls = []

    def run(self, name, args):
        self.calls.append(name)
        if name == "boom":
            raise ValueError("bad argument")
        return {"echo": args}


def test_tool_loop_runs_the_tools_then_answers_with_the_schema():
    model = ScriptedModel({"deal": {"ok": True}}, tool_calls={"deal": [("echo", {"x": 1}), ("boom", {})]})
    llm = LLM(model, V2Config(analyst_model="m"))
    tools = Tools()
    r = llm.run(AgentCall("deal", A.system_prompt("deal"), "go", time.monotonic() + 30, {"type": "object"}, tools, 2))
    assert r.ok and r.data == {"ok": True} and r.tool_calls == ["echo", "boom"]
    results = model.requests[1]["messages"][-1]["content"]
    assert [x["is_error"] for x in results] == [False, True] and "bad argument" in results[1]["content"]


def test_the_last_turn_cannot_call_tools_and_late_calls_return_nothing():
    model = ScriptedModel({"reader": {"ok": 1}}, tool_calls={"reader": [("echo", {})]})
    llm = LLM(model, V2Config(analyst_model="m"))
    r = llm.run(AgentCall("reader", A.system_prompt("reader"), "go", time.monotonic() + 30, None, Tools(), 1))
    assert r.ok and model.requests[-1]["tool_choice"] == {"type": "none"}
    slow = LLM(ScriptedModel({"reader": {"ok": 1}}, delay={"reader": 0.5}), V2Config(analyst_model="m"))
    t0 = time.monotonic()
    results = slow.run_many([AgentCall("reader", A.system_prompt("reader"), "go", time.monotonic() + 0.3)])
    assert not results["reader"].ok and time.monotonic() - t0 < 1.5


def test_a_tool_turn_is_skipped_when_there_is_no_time_for_a_second_call():
    model = ScriptedModel({"deal": {"ok": 1}}, tool_calls={"deal": [("echo", {})]})
    llm = LLM(model, V2Config(analyst_model="m", tool_turn_min_s=14.0))
    r = llm.run(AgentCall("deal", A.system_prompt("deal"), "go", time.monotonic() + 8, None, Tools(), 1))
    assert r.ok and len(model.requests) == 1 and model.requests[0]["tool_choice"] == {"type": "none"}


def test_a_refusal_or_broken_json_is_an_error_not_a_crash():
    def refusing(**kw):
        return [], "refusal", {}

    assert (
        LLM(refusing, V2Config(analyst_model="m"))
        .run(AgentCall("deal", A.system_prompt("deal"), "go", time.monotonic() + 5))
        .error
        == "RuntimeError: the model refused"
    )

    def chatty(**kw):
        return [{"type": "text", "text": 'Sure! {"a": 1} done'}], "end_turn", {}

    assert LLM(chatty, V2Config(analyst_model="m")).run(
        AgentCall("deal", A.system_prompt("deal"), "go", time.monotonic() + 5)
    ).data == {"a": 1}
    assert parse_json('{"x": [1]}') == {"x": [1]}


# ---- fast paths: no model needed ----------------------------------------------------------------------------


def test_an_agreement_is_accepted_at_once_without_asking_any_model():
    model = scripted()
    events = [quote(SID, 0, 17.4), ours({"unit_price": 16.5, "payment_days": 30}), quote(SID, 1, 16.5, "agreed")]
    _, move = decide(brain_with(model), S01, {SID: events})
    assert move.kind == "accept" and move.term_sheet_id == f"offer:{SID[4:]}-1" and not model.requests


def test_an_accepted_offer_is_taken_even_if_our_estimate_of_c_drifted_above_the_opening():
    model = scripted()
    brain = brain_with(model)
    events = [quote(SID, 0, 17.4), ours({"unit_price": 16.5, "payment_days": 30}), quote(SID, 1, 16.5, "agreed")]
    st = brain.open_case(S01, {SID: events})
    st.c_open = 1.0  # as if a new estimate of the supplier had moved every C above the opening
    move = brain.decide(st, SID, {SID: events}, time.monotonic())
    assert move.kind == "accept" and not model.requests


def test_our_package_copied_with_status_open_is_accepted():
    model = scripted()
    mine = {"unit_price": 16.5, "payment_days": 60}
    events = [quote(SID, 0, 17.4), ours(mine), quote(SID, 1, 16.5, days=60, body="We accept your proposal.")]
    _, move = decide(brain_with(model), S01, {SID: events})
    assert move.kind == "accept" and not model.requests


def test_our_package_as_its_alternative_is_confirmed_alone():
    mine = {"unit_price": 16.5, "payment_days": 30}
    events = [quote(SID, 0, 17.4), ours(mine), quote(SID, 1, 17.0, extra_sheets=[mine])]
    _, move = decide(brain_with(scripted()), S01, {SID: events})
    assert move.kind == "send" and [ts["terms"]["unit_price"] for ts in move.message["term_sheets"]] == [16.5]


def test_no_messages_left_accepts_a_signable_sheet():
    case = dict(S01, round_limit=1)
    events = [quote(SID, 0, 17.4), ours({"unit_price": 15.5, "payment_days": 30}), quote(SID, 1, 17.0)]
    _, move = decide(brain_with(scripted()), case, {SID: events})
    assert move.kind == "accept"


# ---- the agents path ---------------------------------------------------------------------------------------------


def test_round_one_runs_all_specialists_in_parallel_then_the_negotiator():
    model = scripted(delay={"reader": 0.3, "deal": 0.3, "counterpart": 0.3, "market": 0.3})
    t0 = time.monotonic()
    st, move = decide(brain_with(model), S02, {s: [quote(s, 0, 17.2, lead_time_days=42)] for s in S02["suppliers"]},
                      sid=S02["suppliers"][0])  # fmt: skip
    roles = [r["role"] for r in model.requests]
    assert set(roles) == {"reader", "deal", "counterpart", "market", "negotiator"} and roles[-1] == "negotiator"
    assert time.monotonic() - t0 < 1.0, "specialists run in parallel"
    assert move.kind == "send" and move.detail["path"] == "agents"
    negotiator_input = model.requests[-1]["messages"][0]["content"]
    assert "THE WHOLE E-MAIL THREAD AS WRITTEN" in negotiator_input and "Our offer." in negotiator_input
    assert "[reader]" in negotiator_input and "DECISION FRAME" in negotiator_input


def test_the_counter_stays_inside_the_hard_limits_whatever_the_model_proposes():
    model = scripted(answers={"negotiator": negotiator_counter(price_factor=1.3)})  # 30 % above its price
    events = [quote(SID, 0, 17.4)]
    st, move = decide(brain_with(model), S01, {SID: events})
    f = F.thread_facts(st, SID, events)
    for ts in move.message["term_sheets"]:
        assert ts["terms"]["unit_price"] <= 17.4 and F.cost(st, SID, ts["terms"], f.std) < f.standing.c
    assert any("above its offer" in n for n in move.detail["guard"])


def test_the_email_carries_the_verified_argument_verbatim_and_its_ids():
    s = S02["suppliers"][0]
    threads = {x: [quote(x, 0, 17.2, lead_time_days=42)] for x in S02["suppliers"]}
    st, move = decide(brain_with(scripted()), S02, threads, sid=s)
    arg = F.arguments(st, s)[0]
    assert arg.sentence in move.message["body"] and set(arg.refs) <= set(move.message["refs"])
    assert "[[" not in move.message["body"]


def test_static_specialists_run_once_per_thread_unless_new_documents_arrive():
    model = scripted()
    brain = brain_with(model)
    events = [quote(SID, 0, 17.4)]
    st = brain.open_case(S01, {SID: events})
    move = brain.decide(st, SID, {SID: events}, time.monotonic())
    events = events + [{**move.message, "from": "buyer"}, quote(SID, 1, 17.1)]
    model.requests.clear()
    brain.decide(st, SID, {SID: events}, time.monotonic())
    assert {r["role"] for r in model.requests} == {"reader", "deal", "negotiator"}
    events = events + [
        {"from": "buyer", "body": "q", "term_sheets": [{"terms": {"unit_price": 16.0}}]},
        {**quote(SID, 2, 17.0), "attachments": [{"name": "cost-breakdown.xlsx", "text": "x"}]},
    ]
    model.requests.clear()
    brain.decide(st, SID, {SID: events}, time.monotonic())
    assert {"counterpart", "market"} <= {r["role"] for r in model.requests}


def test_an_accept_of_the_opening_quote_is_overruled():
    def accept(messages):
        return {"decision": "accept", "reasoning": "fine", "counter_offers": [], "arguments": [],
                "email_subject": "", "email_body": ""}  # fmt: skip

    _, move = decide(brain_with(scripted(answers={"negotiator": accept})), S01, {SID: [quote(SID, 0, 17.4)]})
    assert move.kind == "send" and "accept refused" in move.detail.get("overruled", "")


def test_walking_away_from_a_signable_sheet_is_overruled():
    def walk(messages):
        return {"decision": "walk_away", "reasoning": "bored", "counter_offers": [], "arguments": [],
                "email_subject": "", "email_body": ""}  # fmt: skip

    events = [quote(SID, 0, 17.4), ours({"unit_price": 15.5, "payment_days": 30}), quote(SID, 1, 17.0)]
    _, move = decide(brain_with(scripted(answers={"negotiator": walk})), S01, {SID: events})
    assert move.kind != "stop"


def test_a_late_negotiator_falls_back_to_the_rules_in_time():
    model = scripted(delay={"negotiator": 3.0})
    brain = brain_with(model, decide_by_s=1.5, analysts_by_s=0.8)
    t0 = time.monotonic()
    _, move = decide(brain, S01, {SID: [quote(SID, 0, 17.4)]}, received_at=t0)
    assert move.kind == "send" and move.detail["path"] == "rules" and time.monotonic() - t0 < 3.0
    assert move.message["term_sheets"], "the rules still send an offer"


def test_slow_specialists_are_dropped_and_the_negotiator_still_decides():
    model = scripted(delay={"counterpart": 3.5, "market": 3.5})
    brain = brain_with(model, analysts_by_s=2.2, static_analysts_by_s=2.2, decide_by_s=8.0)
    t0 = time.monotonic()
    _, move = decide(brain, S01, {SID: [quote(SID, 0, 17.4)]}, received_at=t0)
    assert move.detail["path"] == "agents" and not move.detail["agents"]["market"]["ok"]
    assert move.detail["agents"]["negotiator"]["ok"] and time.monotonic() - t0 < 4.5


def test_a_hung_transport_cannot_hold_the_move():
    model = scripted(delay={"negotiator": 6.0})
    brain = brain_with(model, decide_by_s=4.0, analysts_by_s=1.0)
    t0 = time.monotonic()
    _, move = decide(brain, S01, {SID: [quote(SID, 0, 17.4)]}, received_at=t0)
    assert move.detail["path"] == "rules" and time.monotonic() - t0 < 5.0


def test_without_a_model_the_rules_negotiate():
    brain = Brain(DataTools(), None, V2Config(log_dir="off"))
    _, move = decide(brain, S01, {SID: [quote(SID, 0, 17.4)]})
    assert move.kind == "send" and move.detail["path"] == "rules" and move.message["term_sheets"]


# ---- prompts and schemas ------------------------------------------------------------------------------------


def test_every_agent_prompt_has_the_house_rules_the_playbook_and_its_role():
    for role in [*A.SPECS, "negotiator"]:
        system = A.system_prompt(role)
        assert "Rules for our side - never broken" in system and "Negotiation playbook" in system
        assert ScriptedModel.ROLE_MARKERS[role] in system


def test_prompts_name_no_practice_supplier_part_or_case():
    text = "\n".join(p.read_text(encoding="utf-8") for p in A.PROMPTS.glob("*.md"))
    assert not re.search(r"\bsup:\d|\bpart:\d|\bcase:[PSD]\d|\bP\d{2}\b|\bD0\d\b", text)
    for name in ("Brandlhuber", "Dralveck", "Aichbauer", "Konvertek"):
        assert name not in text


def test_schemas_are_valid_for_structured_outputs():
    def walk(schema):
        if schema.get("type") == "object":
            assert schema.get("additionalProperties") is False and set(schema["required"]) == set(schema["properties"])
            for sub in schema["properties"].values():
                walk(sub)
        if schema.get("type") == "array":
            walk(schema["items"])
        for sub in schema.get("anyOf", []):
            walk(sub)

    for schema in A.SCHEMAS.values():
        walk(schema)
