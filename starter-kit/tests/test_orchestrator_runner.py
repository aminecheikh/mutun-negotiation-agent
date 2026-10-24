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
from arena_kit.agents.buyer.roles import LLMWriter, PassthroughProfiler, RuleAnalyst, RuleStrategist
from arena_kit.agents.buyer.runner import play_case, play_final
from arena_kit.agents.buyer.telemetry import Telemetry
from arena_kit.agents.buyer.tools import DataTools
from arena_kit.client import ArenaError
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
    config = dict(close_margin_s=4.0, freeze_s=5.0, last_call_s=1.5, round_margin_s=0.5, keepalive_after_s=60.0)
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


def test_final_polling_recovers_after_transient_server_failure(live_sim, tmp_path, monkeypatch):
    client, state = live_sim(rehearsal=(0, 5, 1))
    real_cases = client.cases
    calls = 0

    def flaky(set_):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ArenaError(503, "temporary outage")
        return real_cases(set_)

    monkeypatch.setattr(client, "cases", flaky)
    out = play_final(
        client,
        lambda: make_agent(tmp_path, close_margin_s=2, freeze_s=3, last_call_s=1, round_margin_s=0.1),
        waves=2,
        poll=0.1,
    )
    assert len(out) == 6 and all(isinstance(r, dict) and r.get("ended") for r in out.values())
    assert all(s["status"] == "ended" for s in state.sessions.values())


def test_lost_post_response_recovers_without_sending_that_move_twice(live_sim, tmp_path):
    import httpx

    client, state = live_sim()

    class LostReply(RemoteTable):
        lost = False

        def send(self, supplier_id, message):
            reply = super().send(supplier_id, message)
            if not self.lost:
                self.lost = True
                raise httpx.ReadError("response lost after processing")
            return reply

    table = LostReply(client)
    result = play_case(table, "case:S01", make_agent(tmp_path))
    assert result["ended"] and table.lost
    assert state.sessions[table.session_id]["status"] == "ended"
    assert all(th["late"] == [] for th in sim_threads(state, table).values())


def test_sweep_records_each_session_and_comparison(live_sim, tmp_path, monkeypatch):
    import runpy
    import sys

    client, state = live_sim()
    script = Path(__file__).parents[1] / "scripts/sweep.py"
    data = Path(__file__).parents[2] / "data"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARENA_URL", str(client.http.base_url).removesuffix("/v1/"))
    monkeypatch.setenv("ARENA_API_KEY", client.http.headers["X-API-Key"])
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr(sys, "argv", [str(script), "--set", "sandbox", "--label", "smoke", "--data", str(data)])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(script), run_name="__main__")
    assert exit_info.value.code == 0
    report = json.loads((tmp_path / "runs/sweep-smoke/report.json").read_text(encoding="utf-8"))
    assert len(report["results"]) == len(report["sessions"]) == 3
    assert all(s["status"] == "ended" for s in state.sessions.values())
