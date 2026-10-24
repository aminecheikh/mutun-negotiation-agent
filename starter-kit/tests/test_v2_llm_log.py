"""v2's model in/out log: every agent call leaves a full JSON record (whole input, every turn with its tool calls,
inputs and results, the raw reply, the parsed answer, tokens, case/supplier/round) in the team's format, each system
prompt once - and logging never breaks a decision."""

import json
import time

from v2_support import S01, SID, quote, scripted

from arena_kit.agents.buyer.tools import DataTools
from arena_kit.agents.buyer_v2.brain import Brain
from arena_kit.agents.buyer_v2.config import V2Config
from arena_kit.agents.buyer_v2.llm import LLM
from arena_kit.agents.buyer_v2.trail import Trail


def decide_once(tmp_path, monkeypatch, model):
    monkeypatch.setenv("BUYER_LLM_LOG_DIR", str(tmp_path / "llm"))
    cfg = V2Config(log_dir=str(tmp_path / "trail"), analyst_model="m", negotiator_model="m")
    trail = Trail(str(tmp_path / "trail"))
    brain = Brain(DataTools(), LLM(model, cfg, recorder=trail.llm_call), cfg, None, trail)
    events = [quote(SID, 0, 17.4, body="Unser Angebot steht.")]
    st = brain.open_case(S01, {SID: events})
    return brain.decide(st, SID, {SID: events}, time.monotonic())


def records(tmp_path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((tmp_path / "llm").glob("*/*-v2-*.json"))]


def test_every_agent_call_leaves_a_full_in_and_out_record(tmp_path, monkeypatch):
    model = scripted(tool_calls={"deal": [("supplier_offers", {"supplier_id": SID})]})
    move = decide_once(tmp_path, monkeypatch, model)
    assert move.detail["path"] == "agents"
    recs = records(tmp_path)
    roles = {r["role"] for r in recs}
    assert {"v2-reader", "v2-deal", "v2-counterpart", "v2-market", "v2-negotiator"} <= roles
    for r in recs:
        assert r["case_id"] == S01["case_id"] and r["supplier_id"] == SID and r["round"] == 1
        assert "Unser Angebot steht." in r["user"], "the whole input is kept"
        assert r["outcome"] == "ok" and r["reply"] and r["answer"] and r["turns"]
        assert (tmp_path / "llm" / r["system_prompt"]).is_file(), "the system prompt is stored once"
    deal = next(r for r in recs if r["role"] == "v2-deal")
    call = deal["turns"][0]["tool_calls"][0]
    assert (
        call["name"] == "supplier_offers"
        and call["input"] == {"supplier_id": SID}
        and "standing_sheet" in call["result"]
    )
    negotiator = next(r for r in recs if r["role"] == "v2-negotiator")
    assert negotiator["answer"]["decision"] == "counter"
    summary = [json.loads(x) for x in (tmp_path / "trail" / "v2-llm.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {s["role"] for s in summary} >= {"reader", "negotiator"} and all(s["supplier_id"] == SID for s in summary)


def test_a_failed_call_is_recorded_with_its_error(tmp_path, monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("provider down")

    move = decide_once(tmp_path, monkeypatch, broken)
    assert move.detail["path"] == "rules", "the rules decide when every model call fails"
    recs = records(tmp_path)
    assert recs and all("provider down" in r["outcome"] for r in recs)


def test_logging_off_writes_nothing_and_a_broken_log_folder_never_costs_the_move(tmp_path, monkeypatch):
    monkeypatch.setenv("BUYER_LLM_LOG_DIR", "off")
    cfg = V2Config(log_dir="off", analyst_model="m", negotiator_model="m")
    trail = Trail("off")
    brain = Brain(DataTools(), LLM(scripted(), cfg, recorder=trail.llm_call), cfg, None, trail)
    events = [quote(SID, 0, 17.4)]
    st = brain.open_case(S01, {SID: events})
    assert brain.decide(st, SID, {SID: events}, time.monotonic()).kind == "send"
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    monkeypatch.setenv("BUYER_LLM_LOG_DIR", str(blocker))
    assert brain.decide(st, SID, {SID: events}, time.monotonic()).kind == "send"
