"""The strategist calls tools (roles/llm.py tool loop + roles/toolbox.py): bounded, read-only, falls back on time.

The transport is faked with the shape the Anthropic SDK returns (content blocks with type/name/input/id), so the
loop, the tools and the strategist's parsing run for real; no network."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

from arena_kit.agents.buyer.contracts import BehaviourRead
from arena_kit.agents.buyer.roles import LLMStrategist, RuleStrategist
from arena_kit.agents.buyer.roles.llm import call_json_with_tools, converse_from_env
from arena_kit.agents.buyer.roles.toolbox import StrategistToolbox
from arena_kit.agents.buyer.tools import DataTools
from arena_kit.cost import total_cost

CASE = json.loads((Path(__file__).parents[1] / "arena_kit/sim_cases/S01.json").read_text())
SID = "sup:70270"


def supplier(price=17.4, status="open", body="Our offer stands.", n=0):
    sheet = {"term_sheet_id": f"offer:70270-{n}", "terms": {"unit_price": price, "payment_days": 30}}
    return {"from": "sales@70270", "status": status, "term_sheets": [sheet], "attachments": [], "body": body}


def buyer(price=15.0):
    return {"from": "buyer", "body": "Our proposal.", "refs": [], "term_sheets": [{"terms": {"unit_price": price}}]}


def setup(events):
    tools = DataTools()
    ctx = tools.prepare(CASE, {SID: events[:1]})
    reads = {SID: BehaviourRead(SID, 0.5, 0.2, ("payment_days",), ())}
    return tools, ctx, {SID: events}, reads


def text(s):
    return SimpleNamespace(type="text", text=s)


def tool_use(name, args, id_="t1"):
    return SimpleNamespace(type="tool_use", name=name, input=args, id=id_)


ANSWER = json.dumps(
    {
        "read": {"p_more": 0.7, "walk_risk": 0.1, "wants": ["payment_days"], "signals": []},
        "tactic": {"supplier_id": SID, "action": "send", "levers": ["unit_price", "payment_days"], "claims": [],
                   "question": None, "rationale": "price_terms: 14 days is worth it"},
    }
)  # fmt: skip


class FakeConverse:
    """Turn 1: the scripted tool calls; turn 2: the answer. Records what the loop sent."""

    def __init__(self, calls, answer=ANSWER, delay_s=0.0):
        self.calls, self.answer, self.delay_s = calls, answer, delay_s
        self.seen = []

    def __call__(self, system, messages, tools, final, timeout_s):
        self.seen.append({"messages": list(messages), "tools": [t["name"] for t in tools], "final": final})
        time.sleep(self.delay_s)
        if not final and self.calls:
            return [text("checking"), *self.calls], "tool_use"
        return [text(self.answer)], "end_turn"


# ---- the loop ----------------------------------------------------------------------------------


def test_loop_runs_the_tool_and_returns_the_final_json():
    tools, ctx, threads, reads = setup([supplier(), buyer(), supplier(17.0, n=1)])
    box = StrategistToolbox(ctx, threads, reads, tools.plan_offers, tools.supplier_from_thread)
    fake = FakeConverse([tool_use("price_terms", {"supplier_id": SID, "changes": {"payment_days": 14}})])
    reply = call_json_with_tools(fake, "sys", "user", box, timeout_s=30.0)
    assert reply["tactic"]["supplier_id"] == SID
    assert box.calls == ["price_terms"]
    assert [s["final"] for s in fake.seen] == [False, True]  # the answer turn cannot call tools again
    result = fake.seen[1]["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "t1" and not result["is_error"]
    assert json.loads(result["content"])["terms"]["payment_days"] == 14


def test_loop_without_tool_calls_is_one_model_call():
    tools, ctx, threads, reads = setup([supplier()])
    fake = FakeConverse([])
    reply = call_json_with_tools(fake, "sys", "user", StrategistToolbox(ctx, threads, reads), timeout_s=5.0)
    assert reply["tactic"]["action"] == "send" and len(fake.seen) == 1


def test_loop_turns_a_bad_argument_into_an_error_result():
    tools, ctx, threads, reads = setup([supplier()])
    box = StrategistToolbox(ctx, threads, reads)
    fake = FakeConverse([tool_use("price_terms", {"supplier_id": SID, "changes": {"colour": "red"}})])
    assert call_json_with_tools(fake, "sys", "user", box, timeout_s=30.0) is not None
    result = fake.seen[1]["messages"][-1]["content"][0]
    assert result["is_error"] and "not negotiable" in result["content"]


def test_loop_returns_none_when_the_deadline_passes():
    tools, ctx, threads, reads = setup([supplier()])
    fake = FakeConverse([tool_use("read_thread", {"supplier_id": SID})], delay_s=0.6)
    t0 = time.monotonic()
    assert call_json_with_tools(fake, "sys", "user", StrategistToolbox(ctx, threads, reads), timeout_s=0.5) is None
    assert time.monotonic() - t0 < 0.9


def test_no_transport_means_no_call():
    tools, ctx, threads, reads = setup([supplier()])
    assert call_json_with_tools(None, "sys", "user", StrategistToolbox(ctx, threads, reads), 5.0) is None


def test_converse_from_env_only_for_anthropic_and_not_when_switched_off(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert converse_from_env(10.0) is None
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("BUYER_LLM_TOOLS", "0")
    assert converse_from_env(10.0) is None


# ---- the tools ---------------------------------------------------------------------------------


def test_price_terms_prices_the_standing_sheet_with_the_change():
    tools, ctx, threads, reads = setup([supplier()])
    box = StrategistToolbox(ctx, threads, reads, supplier_of=tools.supplier_from_thread)
    out = box.price_terms(SID, {"payment_days": 14})
    assert out["terms"]["unit_price"] == 17.4 and out["terms"]["payment_days"] == 14
    same = box.price_terms(SID, {})
    assert same["c"] == same["c_standing"] and same["saves_vs_standing_eur"] == 0
    std_supplier = tools.supplier_from_thread(CASE, SID, threads[SID])
    assert out["c"] == round(total_cost(CASE, out["terms"], {}, std_supplier))
    assert out["c_target"] == round(ctx.bands[SID].c_target)


def test_price_terms_snaps_to_the_allowed_options():
    tools, ctx, threads, reads = setup([supplier()])
    out = StrategistToolbox(ctx, threads, reads).price_terms(SID, {"payment_days": 50})
    assert out["terms"]["payment_days"] in CASE["term_options"]["payment_days"]


def test_preview_offer_is_what_the_offer_engine_sends():
    tools, ctx, threads, reads = setup([supplier()])
    box = StrategistToolbox(ctx, threads, reads, tools.plan_offers, tools.supplier_from_thread)
    out = box.preview_offer(SID, ["payment_days", "colour"])
    assert out["levers"] == ["payment_days"]
    assert 1 <= len(out["sheets"]) <= 3 and all(s["terms"]["unit_price"] < 17.4 for s in out["sheets"])


def test_read_thread_returns_every_message_in_order():
    tools, ctx, threads, reads = setup([supplier(), buyer(), supplier(17.0, body="Final.", n=1)])
    msgs = StrategistToolbox(ctx, threads, reads).read_thread(SID)["messages"]
    assert [m["from"] for m in msgs] == ["supplier", "us", "supplier"] and msgs[-1]["body"] == "Final."


def test_tools_without_their_dependency_are_not_offered():
    tools, ctx, threads, reads = setup([supplier()])
    names = [s["name"] for s in StrategistToolbox(ctx, threads, reads).schemas]
    assert names == ["price_terms", "read_thread"]
    full = StrategistToolbox(ctx, threads, reads, tools.plan_offers, tools.supplier_from_thread, package=object())
    assert [s["name"] for s in full.schemas] == ["price_terms", "preview_offer", "read_thread", "look_up_data"]


def test_unknown_thread_is_an_error():
    tools, ctx, threads, reads = setup([supplier()])
    box = StrategistToolbox(ctx, threads, reads)
    try:
        box.run("price_terms", {"supplier_id": "sup:1", "changes": {}})
    except ValueError as e:
        assert "no open thread" in str(e)
    else:
        raise AssertionError("expected a ValueError")


# ---- the strategist ----------------------------------------------------------------------------


def test_strategist_with_tools_uses_the_tool_loop_and_its_answer():
    tools, ctx, threads, reads = setup([supplier(), buyer(), supplier(17.0, n=1)])
    fake = FakeConverse([tool_use("preview_offer", {"supplier_id": SID, "levers": ["payment_days"]})])
    strategist = LLMStrategist(converse=fake, tools=tools)
    tactic = strategist.choose(ctx, threads, reads)
    assert tactic.levers == ("unit_price", "payment_days") and "price_terms" in tactic.rationale
    assert "preview_offer" in fake.seen[0]["tools"]
    assert "TOOLS YOU CAN CALL" in strategist.system and reads[SID].p_more == 0.7


def test_strategist_without_tools_keeps_the_plain_prompt():
    assert "TOOLS YOU CAN CALL" not in LLMStrategist(complete=lambda s, u: "{}").system


def test_strategist_falls_back_to_the_rule_when_the_tool_loop_is_late():
    tools, ctx, threads, reads = setup([supplier()])
    fake = FakeConverse([tool_use("read_thread", {"supplier_id": SID})], delay_s=0.4)
    tactic = LLMStrategist(converse=fake, tools=tools, timeout_s=0.3).choose(ctx, threads, reads)
    rule = RuleStrategist().choose(ctx, threads, reads)
    assert tactic == rule


def test_strategist_answer_still_goes_through_safe():
    tools, ctx, threads, reads = setup([supplier()])
    accept = json.dumps({"tactic": {"supplier_id": SID, "action": "accept", "levers": [], "claims": []}})
    tactic = LLMStrategist(converse=FakeConverse([], answer=accept), tools=tools).choose(ctx, threads, reads)
    assert tactic.action == "send"  # never accept the opening quote, tools or not


def test_tools_timeout_is_longer_than_the_single_call():
    from arena_kit.agents.buyer.config import OrchestratorConfig

    assert LLMStrategist(converse=FakeConverse([])).timeout_s > LLMStrategist().timeout_s
    config = OrchestratorConfig()
    assert config.strategist_tools_timeout_s + config.writer_timeout_s < config.move_budget_s


# ---- Saed (sweep e848872): 3 tool calls returned no JSON, 4 took over 15 s -----------------------------------------


def test_loop_when_the_answer_turn_comes_asks_for_the_json_only():
    tools, ctx, threads, reads = setup([supplier(), buyer(), supplier(17.0, n=1)])
    box = StrategistToolbox(ctx, threads, reads, tools.plan_offers, tools.supplier_from_thread)
    fake = FakeConverse([tool_use("price_terms", {"supplier_id": SID, "changes": {"payment_days": 14}})])
    call_json_with_tools(fake, "sys", "user", box, timeout_s=30.0)
    last = fake.seen[1]["messages"][-1]["content"]
    assert any(b.get("type") == "text" and "JSON" in b.get("text", "") for b in last)


def test_loop_when_the_clock_is_short_skips_the_tools():
    tools, ctx, threads, reads = setup([supplier()])
    fake = FakeConverse([tool_use("price_terms", {"supplier_id": SID, "changes": {"payment_days": 14}})])
    call_json_with_tools(fake, "sys", "user", StrategistToolbox(ctx, threads, reads), timeout_s=8.0)
    assert [s["final"] for s in fake.seen] == [True]  # no time for a tool turn and an answer: answer at once


def test_strategist_with_tools_finishes_before_the_orchestrator_stops_waiting():
    from arena_kit.agents.buyer.config import OrchestratorConfig

    assert LLMStrategist(converse=FakeConverse([])).timeout_s < OrchestratorConfig().strategist_timeout_s
