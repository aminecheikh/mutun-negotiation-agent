"""Regression tests for races, recoverable HTTP conflicts and deadline handling."""

import copy
import threading
import time
from types import SimpleNamespace

import pytest
from test_orchestrator import S01, S02, SID, A, B, FakeTable, ours, quote, rfq_quote, session_for

from arena_kit.agents.buyer import guard
from arena_kit.agents.buyer.arbiter import Arbiter
from arena_kit.agents.buyer.runner import ThreadWorker
from arena_kit.agents.buyer.telemetry import Telemetry
from arena_kit.client import ArenaError


def single_session():
    return session_for(S01, {SID: [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 16.5)]})


def test_numeric_wave_deadline_is_honoured():
    s = single_session()
    s.deadline = time.time() + 20
    assert 0 < s.seconds_left() <= 20
    assert s.in_close_window()


def test_supplier_reply_stays_in_flight_until_published(monkeypatch):
    s = single_session()
    th = s.threads[SID]
    new_reply = quote(SID, 2, 16.0)
    new_reply["attachments"] = [{"name": "quote.pdf", "attachment_id": "att:test"}]
    s.table = SimpleNamespace(send=lambda sid, message: new_reply)
    arbiter = Arbiter(s)
    observed = []

    def documents(table, reply):
        observed.append(th.in_flight)
        return reply

    monkeypatch.setattr("arena_kit.agents.buyer.runner.fetch_documents", documents)
    worker = ThreadWorker(SimpleNamespace(telemetry=Telemetry("off")), s, arbiter)
    worker.send(th, ours(15.5), "counter")
    assert observed == [True]
    assert not th.in_flight and th.standing_id == new_reply["term_sheets"][0]["term_sheet_id"]


def test_attachment_failure_does_not_discard_an_acknowledged_move(monkeypatch):
    s = single_session()
    th = s.threads[SID]
    before = th.our_rounds
    reply = quote(SID, 2, 16.0)
    reply["attachments"] = [{"name": "quote.pdf", "attachment_id": "att:test"}]
    s.table = SimpleNamespace(send=lambda sid, message: reply)

    def broken(table, reply):
        raise OSError("attachment unavailable")

    monkeypatch.setattr("arena_kit.agents.buyer.runner.fetch_documents", broken)
    worker = ThreadWorker(SimpleNamespace(telemetry=Telemetry("off")), s, Arbiter(s))
    worker.send(th, ours(15.5), "counter")
    assert th.our_rounds == before + 1
    assert th.standing_terms["unit_price"] == 16.0 and not th.in_flight


def test_close_409_does_not_end_an_open_case():
    s = single_session()

    class Conflict(FakeTable):
        def close(self, sid, message):
            raise ArenaError(409, "late; GET the thread for the supplier's answer")

        def result(self):
            return {"status": "open", "deal": False, "end_reason": None}

        def missed(self, sid, error):
            return quote(sid, 2, 16.3)

    s.table = Conflict()
    assert not Arbiter(s).try_accept(s.threads[SID], "test")
    assert not s.ended
    assert s.threads[SID].standing_terms["unit_price"] == 16.3


def test_last_call_can_close_idle_offer_while_cheaper_rival_is_stuck_in_flight():
    s = session_for(
        S02,
        {
            A: [rfq_quote(A, 0, 17.2), ours(15), rfq_quote(A, 1, 16.4)],
            B: [rfq_quote(B, 0, 17.5), ours(15), rfq_quote(B, 1, 16.0)],
        },
    )
    s.table = FakeTable()
    s.threads[B].in_flight = True
    s.deadline = lambda: time.time() + 5
    Arbiter(s).step()
    assert s.ended and s.table.closed[0][0] == A


def test_reservation_never_restores_a_body_removed_by_guard():
    s = single_session()
    s.set_lookup(lambda ref: ref == "cost:123")
    s.burned.add("cost:123")
    message = {"subject": "Re", "body": "The cost is 12 EUR (cost:123).", "refs": ["cost:123"]}
    reserved = guard.reserve(s, s.threads[SID], message)
    assert "cost:123" not in reserved["body"]
    assert len(reserved["body"].split()) >= guard.MIN_WORDS


def test_unknown_standard_term_cannot_be_guessed_when_accepting():
    s = single_session()
    th = s.threads[SID]
    th.events[-1]["missing_fields"] = ["payment_days"]
    th.events[-1]["term_sheets"][0]["terms"].pop("payment_days")
    from arena_kit.agents.buyer import observe

    observe.rebuild(s, th)
    assert not guard.accept_ok(s, th)


def test_recovery_uses_authoritative_history_and_timeout_rounds():
    from arena_kit.agents.buyer.recovery import refresh_thread

    s = single_session()
    messages = copy.deepcopy(s.threads[SID].events) + [ours(15.5), quote(SID, 2, 16.0)]
    client = SimpleNamespace(
        session=lambda sid: {"status": "open", "threads": {SID: {"seconds_left": 12}}},
        thread=lambda sid, sup: {
            "messages": messages,
            "timeouts": [{"offer_id": "offer:old"}],
            "closed": None,
            "status": "open",
            "offer_id": messages[-1]["term_sheets"][0]["term_sheet_id"],
        },
    )
    s.table = SimpleNamespace(client=client, session_id="session:test")
    refresh_thread(s, s.threads[SID])
    th = s.threads[SID]
    assert th.our_rounds == 2 and th.moves_left(s.case) == s.case["round_limit"] - 3
    assert th.standing_terms["unit_price"] == 16.0
    assert time.monotonic() - th.received_at >= 48


@pytest.mark.parametrize("status", [409, 422])
def test_failed_close_refreshes_stale_offer_before_retry(status):
    s = single_session()

    class Changed(FakeTable):
        def close(self, sid, message):
            raise ArenaError(status, "not a standing offer")

        def missed(self, sid, error):
            return quote(sid, 2, 16.2)

        def result(self):
            return {"status": "open", "deal": False}

    s.table = Changed()
    assert not Arbiter(s).try_accept(s.threads[SID], "test")
    assert not s.ended and s.threads[SID].standing_terms["unit_price"] == 16.2


def test_slow_attachment_returns_on_budget_without_late_mutation(monkeypatch):
    from arena_kit.agents.buyer.runner import enrich_documents

    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def slow(table, message):
        started.set()
        release.wait(2)
        message["attachments"][0]["text"] = "late text"
        finished.set()
        return message

    monkeypatch.setattr("arena_kit.agents.buyer.runner.fetch_documents", slow)
    message = {"attachments": [{"name": "quote.pdf"}]}
    try:
        before = time.monotonic()
        result = enrich_documents(None, message, 0.03)
        assert time.monotonic() - before < 0.5 and started.is_set()
    finally:
        release.set()
    assert finished.wait(1)
    assert result == message and "text" not in result["attachments"][0]


def test_expired_queued_role_call_is_cancelled(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from arena_kit.agents.buyer.roles.llm import call_json

    block = threading.Event()
    calls = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr("arena_kit.agents.buyer.roles.llm._POOL", pool)
        pool.submit(block.wait, 2)
        try:
            assert call_json(lambda s, u: calls.append(u), "system", "stale", 0.02) is None
        finally:
            block.set()
    assert calls == []


def test_walk_is_rechecked_when_an_acceptable_reply_arrives():
    s = single_session()
    s.table = FakeTable()
    assert Arbiter(s).try_walk("stale earlier walk verdict")
    assert s.table.closed[0][1]["type"] == "accept"
