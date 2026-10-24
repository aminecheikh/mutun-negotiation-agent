"""Regressions from the independent review of v2: each test pins one finding (409 loops, the confirm path, accepts
answered 409, stable argument tokens, sheet-less fallbacks, caps without a sheet in the latest e-mail, the e-mail
checks) and the hardening that came with them (timeouts in a re-read thread, transient accept errors, the
coordinator surviving an error)."""

import time

import pytest
from v2_support import DATA, S01, S02, SID, ours, quote, state_for

from arena_kit.agents.buyer.tools import DataTools
from arena_kit.agents.buyer_v2 import facts as F
from arena_kit.agents.buyer_v2 import guard
from arena_kit.agents.buyer_v2.brain import Brain
from arena_kit.agents.buyer_v2.config import V2Config
from arena_kit.agents.buyer_v2.runner import CaseRun, V2Agent
from arena_kit.agents.buyer_v2.trail import Trail
from arena_kit.client import ArenaError


def agent() -> V2Agent:
    cfg = V2Config(log_dir="off", poll_s=0.02)
    return V2Agent(Brain(DataTools(), None, cfg), cfg, Trail("off"))


class Table:
    """A scripted arena: `send` and `close` behaviours, a thread view for re-reads, a session state."""

    session_id = None

    def __init__(self, thread=None, send=None, close=None, ended=False):
        self.thread_view, self.on_send, self.on_close, self.ended = thread, send, close, ended
        self.sent, self.closes = [], []

    def send(self, sid, message):
        self.sent.append(message)
        return self.on_send(message)

    def close(self, sid, message):
        self.closes.append(message["action"])
        return self.on_close(message)

    def thread(self, sid):
        return self.thread_view

    def attachment(self, attachment_id):
        return b""

    def result(self):
        return {"ended": self.ended, "deal": False}


def conflict(*_):
    raise ArenaError(409, "round limit reached")


# ---- 1: a repeated 409 never loops --------------------------------------------------------------------------


def test_a_409_with_an_unchanged_thread_stops_writing_to_that_thread():
    events = [quote(SID, 0, 17.4)]
    table = Table(thread={"messages": events, "status": "open", "closed": None, "timeouts": []},
                  send=conflict, close=lambda m: {"ended": True, "deal": False})  # fmt: skip
    run = CaseRun(agent(), table, S01, {SID: events})
    t0 = time.monotonic()
    result = run.play()
    assert len(table.sent) == 1 and run.runs[SID].done and result is not None and time.monotonic() - t0 < 5


def test_a_re_read_thread_counts_timeouts_as_rounds_and_takes_the_timeout_answer():
    events = [quote(SID, 0, 17.4), ours({"unit_price": 15.8, "payment_days": 30}), quote(SID, 1, 17.0)]
    view = {"messages": events, "status": "open", "closed": None, "offer_id": "offer:70270-9",
            "timeouts": [{"offer_id": "offer:70270-9", "status": "open", "terms": {"unit_price": 16.9}}]}  # fmt: skip
    run = CaseRun(agent(), Table(thread=view), S01, {SID: events})
    assert run._resync(run.runs[SID], "test") == "changed"
    f = run._facts(run.runs[SID])
    assert f.our_rounds == 2 and f.standing.id == "offer:70270-9" and f.standing.price == 16.9


# ---- 2 and 3: the confirm path -----------------------------------------------------------------------------------


def test_confirm_is_not_sent_with_no_messages_left():
    mine = {"unit_price": 16.5, "payment_days": 30}
    case = dict(S01, round_limit=1)
    events = [quote(SID, 0, 17.4), ours(mine), quote(SID, 1, 17.0, extra_sheets=[mine])]
    brain = Brain(DataTools(), None, V2Config(log_dir="off"))
    st = brain.open_case(case, {SID: events})
    move = brain.decide(st, SID, {SID: events}, time.monotonic())
    assert move.kind in ("accept", "stop")


def test_confirm_never_offers_above_the_suppliers_standing_price():
    mine = {"unit_price": 17.0, "payment_days": 60}
    events = [quote(SID, 0, 17.4), ours(mine), quote(SID, 1, 16.9, days=14, extra_sheets=[mine])]
    brain = Brain(DataTools(), None, V2Config(log_dir="off"))
    st = brain.open_case(S01, {SID: events})
    move = brain.decide(st, SID, {SID: events}, time.monotonic())
    if move.kind == "send":
        assert all(ts["terms"]["unit_price"] <= 16.9 for ts in move.message["term_sheets"])


# ---- 4: an accept answered 409 does not end the case locally -------------------------------------------------------


def test_an_accept_answered_409_closes_the_thread_but_not_the_case():
    a, b = S02["suppliers"]
    threads = {
        a: [quote(a, 0, 17.2, lead_time_days=42), ours({"unit_price": 16.0, "payment_days": 30, "lead_time_days": 42}),
            quote(a, 1, 16.0, "agreed", lead_time_days=42)],
        b: [quote(b, 0, 17.4, lead_time_days=42)],
    }  # fmt: skip
    table = Table(thread={"messages": threads[a], "status": "agreed", "closed": "timeout", "timeouts": []},
                  close=conflict)  # fmt: skip
    run = CaseRun(agent(), table, S02, threads)
    assert run.accept_best(prefer=a, reason="test") is False
    assert not run.ended and run.runs[a].closed


def test_a_transient_accept_error_does_not_refuse_the_sheet_for_good():
    events = [quote(SID, 0, 17.4), ours({"unit_price": 16.5, "payment_days": 30}), quote(SID, 1, 16.5, "agreed")]

    def unavailable(_):
        raise ArenaError(503, "busy")

    run = CaseRun(agent(), Table(close=unavailable), S01, {SID: events})
    assert run.accept_best(prefer=SID, reason="test") is False and not run.state.refused


# ---- 5: argument tokens are stable for the whole case -----------------------------------------------------------


def test_argument_tokens_do_not_shift_when_another_argument_is_cited():
    from arena_kit.data import DataPackage

    if not (DATA / "world.sqlite").is_file():
        pytest.skip("needs the data package")
    s = S02["suppliers"][0]
    threads = {x: [quote(x, 0, 17.2, lead_time_days=42)] for x in S02["suppliers"]}
    st = state_for(S02, threads, package=DataPackage(DATA))
    before = {a.token: a.sentence for a in F.arguments(st, s)}
    assert len(before) >= 2
    first = F.arguments(st, s)[0]
    st.burned |= set(first.refs)
    after = {a.token: a.sentence for a in F.arguments(st, s)}
    assert first.token not in after
    assert all(before[t] == sentence for t, sentence in after.items())


def test_bracketed_tokens_from_the_model_are_understood():
    s = S02["suppliers"][0]
    st = state_for(S02, {x: [quote(x, 0, 17.2, lead_time_days=42)] for x in S02["suppliers"]})
    f = F.thread_facts(st, s, [quote(s, 0, 17.2, lead_time_days=42)])
    arg = F.arguments(st, s)[0]
    body = "Dear Sir or Madam,\n\nthank you for your offer; we have reviewed it with care and attach our proposal now.\n\nKind regards"  # noqa: E501
    cleaned, refs, _ = guard.clean_email(st, f, [], body, [f"[[{arg.token.lower()}]]"], [{"unit_price": 16.5}], [])
    assert arg.sentence in cleaned and refs


# ---- 6: a counter always carries a sheet when one is possible ---------------------------------------------------


def test_the_bridge_counter_sits_between_our_offer_and_its_offer():
    events = [quote(SID, 0, 17.4), ours({"unit_price": 15.8, "payment_days": 30}), quote(SID, 1, 17.0)]
    st = state_for(S01, {SID: events})
    f = F.thread_facts(st, SID, events)
    sheet = guard.bridge_sheet(st, f)
    assert sheet is not None and sheet["unit_price"] <= 17.0
    c = F.cost(st, SID, sheet, f.std)
    assert f.our_last_c * (1 - 1e-3) <= c < f.standing.c


# ---- 7: the caps hold when the latest e-mail has no sheet ------------------------------------------------------


def test_caps_come_from_its_latest_sheet_when_its_latest_email_has_none():
    note = {"from": "sales@70270", "status": "open", "term_sheets": [], "attachments": [], "body": "We will check."}
    events = [quote(SID, 0, 17.4), ours({"unit_price": 16.2, "payment_days": 30}), quote(SID, 1, 16.5), note]
    st = state_for(S01, {SID: events})
    f = F.thread_facts(st, SID, events)
    assert f.standing is not None and f.standing.price == 16.5
    checked = guard.check_counter(st, f, [{"unit_price": 17.2, "payment_days": 30}, {"unit_price": 15.0}])
    for sheet in checked.sheets:
        assert sheet["unit_price"] <= 16.5 and F.cost(st, SID, sheet, f.std) >= f.our_last_c * (1 - 1e-3)


# ---- 8: other suppliers by short name, dependence wording ---------------------------------------------------------


def test_short_names_of_other_suppliers_and_dependence_wording_are_removed():
    s = S02["suppliers"][0]
    st = state_for(S02, {x: [quote(x, 0, 17.2, lead_time_days=42)] for x in S02["suppliers"]})
    f = F.thread_facts(st, s, [quote(s, 0, 17.2, lead_time_days=42)])
    body = (
        "Dear Sir or Madam,\n\nthank you for your offer, which we have reviewed with care.\n"
        "Brandlhuber has offered us better terms.\n"
        "We depend on your parts for our line.\n"
        "We attach our proposal and look forward to your answer on the packages.\n\nKind regards"
    )
    rival = ["Gießerei Brandlhuber GmbH & Co. KG"]
    cleaned, _, notes = guard.clean_email(st, f, [], body, [], [{"unit_price": 16.5}], rival, "Metallbau Example GmbH")
    assert "Brandlhuber" not in cleaned and "depend on" not in cleaned and len(notes) >= 2


def test_a_word_the_addressee_shares_with_a_rival_is_not_removed():
    marks = guard.name_marks(["Gießerei Brandlhuber GmbH"], own="Gießerei Huber KG")
    assert "brandlhuber" in marks and "gießerei" not in marks


# ---- RFQ latency: an agreement is never left waiting -------------------------------------------------------------


def test_the_coordinator_accepts_an_agreement_without_its_worker():
    """P05 lost an agreed supplier because the accept went out 70 s after its reply: the coordinator now takes it."""
    from v2_support import S02

    a_sid, b_sid = S02["suppliers"]
    mine = {"unit_price": 16.0, "payment_days": 30, "lead_time_days": 42}
    threads = {
        a_sid: [quote(a_sid, 0, 17.2, lead_time_days=42), ours(mine),
                quote(a_sid, 1, 16.0, "agreed", lead_time_days=42)],
        b_sid: [quote(b_sid, 0, 17.4, lead_time_days=42)],
    }  # fmt: skip
    table = Table(close=lambda m: {"ended": True, "deal": True})
    run = CaseRun(agent(), table, S02, threads)
    run.step()  # no worker runs at all
    assert run.ended and table.closes and table.closes[0]["type"] == "accept"


def test_a_closing_action_never_holds_the_case_lock():
    """A slow arena close must not freeze the other threads of the case (the P05 cause)."""
    import threading

    events = [quote(SID, 0, 17.4), ours({"unit_price": 16.5, "payment_days": 30}), quote(SID, 1, 16.5, "agreed")]
    started, release = threading.Event(), threading.Event()

    def slow_close(message):
        started.set()
        release.wait(timeout=5)
        return {"ended": True, "deal": True}

    run = CaseRun(agent(), Table(close=slow_close), S01, {SID: events})
    t = threading.Thread(target=run.accept_best, args=(SID, "test"), daemon=True)
    t.start()
    assert started.wait(timeout=5), "the close was never attempted"
    got = threading.Event()

    def take_lock():
        with run.lock:
            got.set()

    other = threading.Thread(target=take_lock, daemon=True)
    other.start()
    assert got.wait(timeout=2), "the case lock was held across the closing POST"
    release.set()
    t.join(timeout=5)


# ---- hardening: the coordinator survives an error ------------------------------------------------------------------


def test_the_coordinator_survives_an_error_and_still_closes_the_case():
    # the supplier walked away: only the coordinator can close the case (walk away), and its first step fails
    events = [quote(SID, 0, 17.4), ours({"unit_price": 15.0, "payment_days": 30}), quote(SID, 1, 17.4, "walked_away")]
    run = CaseRun(agent(), Table(close=lambda m: {"ended": True, "deal": False}), S01, {SID: events})
    calls = {"n": 0}
    real = run.step

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return real(**kw)

    run.step = flaky
    result = run.play()
    assert result["ended"] and calls["n"] >= 2
