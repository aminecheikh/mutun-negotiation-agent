"""v2 remembers the whole negotiation: every agent reads every e-mail of the thread (all rounds), and our negotiation
log - each round's decision, its reasoning, the packages with C, the data cited, how we read the supplier - goes to
every agent in every later round."""

import time

from v2_support import S01, SID, quote, scripted

from arena_kit.agents.buyer.tools import DataTools
from arena_kit.agents.buyer_v2 import agents as A
from arena_kit.agents.buyer_v2 import facts as F
from arena_kit.agents.buyer_v2.brain import Brain
from arena_kit.agents.buyer_v2.config import V2Config
from arena_kit.agents.buyer_v2.llm import LLM

BODIES = ["Round zero: our quote, valid 30 days.", "Round one: we can move a little on price.",
          "Round two: with faster payment we could go lower.", "Round three: this is close to our limit."]  # fmt: skip
PRICES = [17.4, 17.1, 16.9, 16.8]


def play_rounds(model, rounds=3):
    """Drive the brain through `rounds` supplier replies; returns the brain, its case state and the thread."""
    cfg = V2Config(log_dir="off", analyst_model="m", negotiator_model="m")
    brain = Brain(DataTools(), LLM(model, cfg), cfg)
    events = [quote(SID, 0, PRICES[0], body=BODIES[0])]
    st = brain.open_case(S01, {SID: events})
    for n in range(1, rounds + 1):
        move = brain.decide(st, SID, {SID: events}, time.monotonic())
        assert move.kind == "send", move.reason
        events = [*events, {**move.message, "from": "buyer"}, quote(SID, n, PRICES[n], body=BODIES[n])]
    return brain, st, events


def last_inputs(model) -> dict[str, str]:
    """The newest user input each role received."""
    out = {}
    for r in model.requests:
        if len(r["messages"]) == 1:  # the first turn carries the input; later turns are tool results
            out[r["role"]] = r["messages"][0]["content"]
    return out


def test_every_agent_reads_every_email_of_every_round():
    model = scripted()
    _, _, events = play_rounds(model)
    model.requests.clear()
    brain_events = events
    cfg = V2Config(log_dir="off", analyst_model="m", negotiator_model="m")
    brain = Brain(DataTools(), LLM(model, cfg), cfg)
    st = brain.open_case(S01, {SID: brain_events})
    st.notes[SID] = {"counterpart": {}, "market": {}}
    brain.decide(st, SID, {SID: brain_events}, time.monotonic())
    inputs = last_inputs(model)
    assert {"reader", "deal", "negotiator"} <= set(inputs)
    our_bodies = [m["body"] for m in brain_events if m.get("from") == "buyer"]
    for role, text in inputs.items():
        for body in BODIES:
            assert body in text, f"{role} does not see: {body}"
        for body in our_bodies:
            assert body.split("\n")[0] in text, f"{role} does not see our earlier e-mail"


def test_static_specialists_also_get_the_whole_thread_when_they_run_again():
    f_events = [quote(SID, 0, 17.4, body=BODIES[0]), {"from": "buyer", "body": "Our first answer.", "term_sheets": [
        {"terms": {"unit_price": 15.8, "payment_days": 30}}]}, quote(SID, 1, 17.1, body=BODIES[1])]  # fmt: skip
    cfg = V2Config(log_dir="off")
    brain = Brain(DataTools(), None, cfg)
    st = brain.open_case(S01, {SID: f_events})
    f = F.thread_facts(st, SID, f_events)
    for role in A.SPECS:
        text = A.analyst_input(role, st, f, f_events)
        assert BODIES[0] in text and BODIES[1] in text and "Our first answer." in text, role
        assert "OUR NEGOTIATION LOG" in text, role


def test_the_log_carries_each_rounds_decision_reasoning_packages_and_reading_into_later_rounds():
    model = scripted()
    brain, st, events = play_rounds(model, rounds=2)
    log = st.notes[SID]["log"]
    assert [e["round"] for e in log] == [1, 2]
    assert all(e["decision"] == "counter" and "trade payment for price" in e["why"] for e in log)
    assert all(e["sheets"] and e["sheets"][0]["c"] for e in log)
    assert log[0]["its_message"]["offer_summary"] == "It holds its price."
    model.requests.clear()
    brain.decide(st, SID, {SID: events}, time.monotonic())
    negotiator = last_inputs(model)["negotiator"]
    assert "Round 1 (agents): counter" in negotiator and "Round 2 (agents): counter" in negotiator
    assert "Its price path: 17.40 -> 17.10 (-1.7 %) -> 16.90 (-1.2 %)" in negotiator
    assert "data we cited (never again)" in negotiator


def test_rounds_decided_by_the_rules_are_remembered_too():
    brain = Brain(DataTools(), None, V2Config(log_dir="off"))
    events = [quote(SID, 0, 17.4)]
    st = brain.open_case(S01, {SID: events})
    move = brain.decide(st, SID, {SID: events}, time.monotonic())
    entry = st.notes[SID]["log"][0]
    assert entry["path"] == "rules" and entry["round"] == 1 and len(entry["sheets"]) == len(move.message["term_sheets"])


def test_price_path_shows_every_step_and_its_size():
    assert A.price_path([17.4, 17.0, 16.85]) == "17.40 -> 17.00 (-2.3 %) -> 16.85 (-0.9 %)"
    assert A.price_path([]) == "none yet"
