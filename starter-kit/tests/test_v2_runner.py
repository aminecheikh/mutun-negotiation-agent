"""The v2 runner end to end against arena-sim on a real clock: every thread answered within its limit (rules only,
a scripted model, a slow model), an agreement accepted the moment it arrives and nothing sent after it, no offer
ever above the supplier's own, a refused accept re-read and retried once, and a two-wave final rehearsal that
ends every case before its cap."""

import json
import secrets
import socket
import threading
import time
from pathlib import Path

import pytest
from v2_support import S01, SID, ours, quote, scripted

import arena_kit.sim as sim_module
from arena_kit import ArenaClient
from arena_kit.agents.buyer.tools import DataTools
from arena_kit.agents.buyer_v2.brain import Brain
from arena_kit.agents.buyer_v2.config import V2Config
from arena_kit.agents.buyer_v2.llm import LLM
from arena_kit.agents.buyer_v2.runner import CaseRun, V2Agent, play_case, play_final
from arena_kit.agents.buyer_v2.trail import Trail
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
def sim(monkeypatch):
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
        key = ArenaClient(url).register(f"v2-{secrets.token_hex(3)}", "sim")["api_key"]
        return ArenaClient(url, key, rate=1000), state

    yield start
    for server, thread in running:
        server.should_exit = True
        thread.join(timeout=5)


def agent(log_dir, model=None, **config) -> V2Agent:
    cfg = V2Config(log_dir=str(log_dir), **({"analyst_model": "m", "negotiator_model": "m"} | config))
    trail = Trail(str(log_dir))
    llm = LLM(model, cfg, recorder=trail.llm_call) if model is not None else None
    return V2Agent(Brain(DataTools(), llm, cfg, None, trail), cfg, trail)


def rows(log_dir, name="v2-rounds.jsonl") -> list[dict]:
    path = Path(log_dir) / name
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def sim_threads(state, table) -> dict:
    return state.sessions[table.session_id]["threads"]


def assert_clean_threads(state, table):
    """No timeouts; no buyer offer above the supplier's standing price; nothing sent after it agreed."""
    for sup, th in sim_threads(state, table).items():
        assert th["late"] == [], f"{sup} timed out: {th['late']}"
        standing = None
        for m in th["messages"]:
            if m.get("from") != "buyer":
                standing = m
                continue
            assert standing is not None
            assert standing.get("status") != "agreed", f"{sup}: we wrote after it agreed"
            theirs = standing["term_sheets"][0]["terms"]["unit_price"] if standing.get("term_sheets") else None
            for ts in m.get("term_sheets") or []:
                assert theirs is None or ts["terms"]["unit_price"] <= theirs + 1e-9, f"{sup}: offer above its price"


@pytest.mark.parametrize("case_id", ["case:S01", "case:S02", "case:S03"])
def test_rules_only_every_case_ends_in_a_deal_with_clean_threads(sim, tmp_path, case_id):
    client, state = sim()
    table = RemoteTable(client)
    result = play_case(table, case_id, agent(tmp_path))
    assert result["ended"] and result["deal"]
    assert_clean_threads(state, table)


def test_with_the_agents_an_rfq_ends_in_a_deal_and_every_move_is_traced(sim, tmp_path):
    client, state = sim()
    table = RemoteTable(client)
    result = play_case(table, "case:S02", agent(tmp_path, scripted()))
    assert result["ended"] and result["deal"]
    assert_clean_threads(state, table)
    sent = [r for r in rows(tmp_path) if r["kind"] == "send"]
    assert sent and any(r["detail"]["path"] == "agents" for r in sent)
    assert rows(tmp_path, "v2-llm.jsonl"), "every model call is recorded"
    accepts = [r for r in rows(tmp_path) if r["kind"] == "accept"]
    assert accepts, "the case closed with an accept"


def test_a_slow_model_never_costs_a_timeout(sim, tmp_path):
    client, state = sim(move_s=6)
    table = RemoteTable(client)
    slow = scripted(delay={"negotiator": 4.0, "reader": 0.5, "deal": 0.5})
    a = agent(tmp_path, slow, move_budget_s=4.5, analysts_by_s=1.5, decide_by_s=3.0, min_llm_window_s=0.5)
    result = play_case(table, "case:S01", a)
    assert result["ended"]
    assert_clean_threads(state, table)
    assert all(r["detail"]["path"] in ("rules", "fast") for r in rows(tmp_path) if r["kind"] == "send")


def test_final_rehearsal_two_waves_every_case_ends_before_its_cap(sim, tmp_path):
    client, state = sim(rehearsal=(1, 12, 1))
    config = {"close_margin_s": 4.0, "last_call_s": 1.5, "round_margin_s": 0.5}
    out = play_final(client, lambda: agent(tmp_path, **config), waves=2, parallel=10, poll=0.3)
    assert len(out) == 6
    assert all(isinstance(r, dict) and r.get("ended") for r in out.values()), out
    assert all(s["end_reason"] != "wave_cap" for s in state.sessions.values())


# ---- a refused accept --------------------------------------------------------------------------------------------


class RefusingOnceTable:
    """The arena at the boundary: the first accept names a stale sheet (422); the thread then shows the supplier's
    answer to a timeout repeat with a new sheet ID; the second accept succeeds."""

    session_id = None

    def __init__(self, fresh_events):
        self.fresh, self.closes = fresh_events, []

    def close(self, sid, message):
        self.closes.append(message["action"].get("term_sheet_id"))
        if len(self.closes) == 1:
            raise ArenaError(422, "unknown or stale term_sheet_id")
        return {"ended": True, "deal": True}

    def thread(self, sid):
        return self.fresh

    def attachment(self, attachment_id):
        return b""

    def result(self):
        return {"ended": True, "deal": True}


def test_a_refused_accept_rereads_the_thread_and_retries_once_with_the_fresh_sheet(tmp_path):
    base = [quote(SID, 0, 17.4), ours({"unit_price": 16.5, "payment_days": 30})]
    events = [*base, quote(SID, 1, 16.5, "agreed")]
    fresh = [*events, ours({"unit_price": 16.5, "payment_days": 30}), quote(SID, 2, 16.5, "agreed")]
    table = RefusingOnceTable(fresh)
    run = CaseRun(agent(tmp_path), table, S01, {SID: events})
    assert run.accept_best(prefer=SID, reason="test")
    assert table.closes == [f"offer:{SID[4:]}-1", f"offer:{SID[4:]}-2"] and run.result["deal"]


def test_the_cheapest_acceptable_sheet_wins_across_threads(tmp_path):
    from v2_support import S02

    a, b = S02["suppliers"]
    threads = {
        a: [quote(a, 0, 17.2, lead_time_days=42), ours({"unit_price": 16.0, "payment_days": 30, "lead_time_days": 42}),
            quote(a, 1, 16.0, "agreed", lead_time_days=42)],
        b: [quote(b, 0, 17.0, lead_time_days=42), ours({"unit_price": 15.5, "payment_days": 30, "lead_time_days": 42}),
            quote(b, 1, 15.8, lead_time_days=42)],
    }  # fmt: skip

    class Table(RefusingOnceTable):
        def close(self, sid, message):
            self.closes.append((sid, message["action"].get("term_sheet_id")))
            return {"ended": True, "deal": True}

    table = Table([])
    run = CaseRun(agent(tmp_path), table, S02, threads)
    assert run.accept_best(prefer=a, reason="a agreed")
    assert table.closes == [(b, f"offer:{b[4:]}-1")], "b's standing sheet is acceptable and cheaper"
