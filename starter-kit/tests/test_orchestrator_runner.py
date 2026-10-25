"""The runner end to end against arena-sim on a real clock: every thread answered within its limit, slow or broken
roles and tools never cost a timeout, a crashed final case resumes, and a two-wave final rehearsal finishes every
case before its cap. The sim's 60-second rule is shortened so that a late thread really times out."""

import json
import secrets
import socket
import threading
import time
from pathlib import Path

import pytest

import arena_kit.sim as sim_module
from arena_kit import ArenaClient
from arena_kit.agents.buyer.agent import BuyerAgent
from arena_kit.agents.buyer.config import OrchestratorConfig
from arena_kit.agents.buyer.contracts import Tactic
from arena_kit.agents.buyer.roles import LLMWriter, PassthroughProfiler, RuleAnalyst, RuleStrategist
from arena_kit.agents.buyer.runner import play_case, play_final
from arena_kit.agents.buyer.telemetry import Telemetry
from arena_kit.agents.buyer.tools import DataTools
from arena_kit.play import RemoteTable


def _serve(state):
    import uvicorn

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(sim_module.create_app(state), port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    return f"http://127.0.0.1:{port}", server, thread


@pytest.fixture
def live_sim(monkeypatch):
    """start(move_s=None, rehearsal=None) -> (client, Sim): arena-sim with a real clock on a free port."""
    pytest.importorskip("fastapi")
    running = []

    def start(move_s=None, rehearsal=None):
        if move_s is not None:
            monkeypatch.setattr(sim_module, "MOVE_S", move_s)
        state = sim_module.Sim(sim_module.load_cases(), clock=time.time)
        if rehearsal:
            state.rehearse(*rehearsal)
        url, server, thread = _serve(state)
        running.append((server, thread))
        key = ArenaClient(url).register(f"test-{secrets.token_hex(3)}", "sim")["api_key"]
        return ArenaClient(url, key, rate=1000), state

    yield start
    for server, thread in running:
        server.should_exit = True
        thread.join(timeout=5)


class SlowStrategist(RuleStrategist):
    def __init__(self, delay: float):
        self.delay = delay

    def choose(self, ctx, threads, reads):
        time.sleep(self.delay)
        return super().choose(ctx, threads, reads)


class BrokenOffers(DataTools):
    def plan_offers(self, *args, **kwargs):
        raise RuntimeError("offer engine down")


def make_agent(log_dir, strategist=None, tools=None, **config) -> BuyerAgent:
    return BuyerAgent(
        tools or DataTools(), PassthroughProfiler(), RuleAnalyst(), strategist or RuleStrategist(), LLMWriter(None),
        None, OrchestratorConfig(**config), Telemetry(log_dir),
    )  # fmt: skip


def rounds(log_dir) -> list[dict]:
    path = Path(log_dir) / "buyer-rounds.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def sim_threads(state, client_table) -> dict:
    return state.sessions[client_table.session_id]["threads"]


def test_rfq_when_strategist_takes_a_second_every_bidder_is_answered_in_time(live_sim, tmp_path):
    client, state = live_sim(move_s=4)
    table = RemoteTable(client)
    agent = make_agent(tmp_path, SlowStrategist(1.0), move_budget_s=3.2, strategist_timeout_s=2.0, min_llm_window_s=0.1)
    result = play_case(table, "case:S02", agent)
    threads = sim_threads(state, table)
    assert result["ended"] and len(threads) == 2
    for sup, th in threads.items():
        assert th["late"] == [], f"{sup} timed out: {th['late']}"  # D02 lost its second bidder exactly here
        assert sum(m.get("from") == "buyer" for m in th["messages"]) >= 1


def test_case_when_the_rules_decide_every_e_mail_is_the_deterministic_one(live_sim, tmp_path):
    from arena_kit.agents.buyer.tools import message

    client, state = live_sim()
    table = RemoteTable(client)
    for case_id in ("case:S01", "case:S02"):
        assert play_case(table, case_id, make_agent(tmp_path))["ended"]
        for th in sim_threads(state, table).values():
            sent = [m for m in th["messages"] if m.get("from") == "buyer" and m.get("term_sheets")]
            assert sent
            for m in sent:  # the guard and the citation reserve kept every line as composed
                lines = m["body"].split("\n")
                assert lines[0] == message.INTRO and lines[-1] == message.SIGN_OFF, m["body"]
                assert lines[1] in message.NARRATIVES.values() and lines[2].startswith("Our lead package commits")


class PushingStrategist(RuleStrategist):
    def choose(self, ctx, threads, reads):
        tactic = super().choose(ctx, threads, reads)
        return Tactic(tactic.supplier_id, "send", tactic.claims, None, "always push", "push")


def test_case_when_the_agent_always_pushes_code_keeps_it_to_real_steps_never_twice_in_a_row(
    live_sim, tmp_path, monkeypatch
):
    monkeypatch.setenv("BUYER_PUSH", "1")
    from arena_kit.agents.buyer.roles.writer import PUSH_LINE

    client, state = live_sim()
    table = RemoteTable(client)
    assert play_case(table, "case:S01", make_agent(tmp_path, PushingStrategist()))["ended"]
    sent = [r for r in rounds(tmp_path) if r.get("kind") == "counter" and r.get("tactic")]
    prices = [r["tactic"]["price"] for r in sent]
    assert prices[0] == "follow"  # never in our first message
    assert all(not (a == b == "push") for a, b in zip(prices, prices[1:], strict=False))  # never twice in a row
    assert 0 < prices.count("push") <= 2  # the simulated supplier moves in real steps: the agent may press
    for r in sent:
        assert (PUSH_LINE in r["body"]) == (r["tactic"]["price"] == "push")


class PackageStrategist(RuleStrategist):
    def choose(self, ctx, threads, reads):
        tactic = super().choose(ctx, threads, reads)
        return Tactic(tactic.supplier_id, "send", tactic.claims, None, "it asked for fast payment", "follow",
                      {"payment_days": 14})  # fmt: skip


def test_case_when_the_agent_proposes_a_package_it_goes_second_never_dearer_than_the_plan(live_sim, tmp_path):
    client, state = live_sim()
    table = RemoteTable(client)
    assert play_case(table, "case:S01", make_agent(tmp_path, PackageStrategist()))["ended"]
    sent = [r for r in rounds(tmp_path) if r.get("kind") == "counter" and r.get("sheets")]
    assert any(r["sheets"][1]["terms"]["payment_days"] == 14 for r in sent if len(r["sheets"]) > 1)
    for r in sent:
        lead = r["sheets"][0]["c"]
        assert all(s["c"] <= lead + 1 for s in r["sheets"][1:])  # rounded to whole euros in the log


def test_case_when_strategist_hangs_the_rule_twin_answers_within_the_limit(live_sim, tmp_path):
    client, state = live_sim(move_s=4)
    table = RemoteTable(client)
    agent = make_agent(tmp_path, SlowStrategist(6.0), move_budget_s=3.2, strategist_timeout_s=1.0, min_llm_window_s=0.1)
    result = play_case(table, "case:S01", agent)
    assert result["ended"]
    assert all(th["late"] == [] for th in sim_threads(state, table).values())
    stages = [r["stages"]["choose"] for r in rounds(tmp_path) if r.get("stages")]
    assert stages and set(stages) == {"timeout"}


def test_case_when_the_offer_engine_fails_still_negotiates_to_an_end(live_sim, tmp_path):
    client, state = live_sim()
    table = RemoteTable(client)
    result = play_case(table, "case:S01", make_agent(tmp_path, tools=BrokenOffers()))
    assert result["ended"]
    sent = [r for r in rounds(tmp_path) if r.get("kind") == "counter"]
    assert sent and all(r["stages"]["plan"] == "error" for r in sent)
    assert all(r["sheets"] for r in sent)  # the fallback plan still offered guard-clean sheets


def test_final_case_when_runner_restarts_resumes_the_same_session(live_sim, tmp_path):
    client, state = live_sim(rehearsal=(0, 60, 5))
    case = next(c for c in client.cases("final") if c["case_id"].endswith("S01"))
    opened = client.open_session(case["case_id"])  # the crashed runner had opened it and sent one message
    sup = case["suppliers"][0]
    client.send_message(opened["session_id"], sup, {
        "subject": "Re", "body": "Thank you for your quote. Please find our proposal attached.",
        "term_sheets": [{"terms": {"unit_price": 15.5, "payment_days": 60}}],
    })  # fmt: skip
    table = RemoteTable(client, case)
    result = play_case(table, case["case_id"], make_agent(tmp_path))
    assert table.session_id == opened["session_id"]
    assert result["ended"]


def test_final_rehearsal_when_two_waves_run_every_case_ends_before_its_cap(live_sim, tmp_path):
    client, state = live_sim(rehearsal=(1, 10, 1))
    config = dict(close_margin_s=4.0, last_call_s=1.5, round_margin_s=0.5)
    out = play_final(client, lambda: make_agent(tmp_path, **config), waves=2, parallel=10, poll=0.3)
    assert len(out) == 6  # three demo cases per wave
    assert all(isinstance(r, dict) and r.get("ended") for r in out.values()), out
    assert all(s["end_reason"] != "wave_cap" for s in state.sessions.values())


def test_case_when_a_thread_keeps_failing_the_runner_gives_up_and_closes_the_case(live_sim, tmp_path):
    client, state = live_sim()
    table = RemoteTable(client)
    agent = make_agent(tmp_path)

    def broken(*args, **kwargs):
        raise RuntimeError("pipeline down")

    agent.pipeline.run = broken
    result = play_case(table, "case:S01", agent)
    assert result["ended"]  # the session is closed (walked away), not left open on the arena
    assert state.sessions[table.session_id]["status"] == "ended"
