"""The orchestrator's deterministic parts: acceptability, verdicts, walk-away, tactic clamping, the guard, the
data-package lock, and the rule that agent code never reads the live feed. No network, no LLM."""

import copy
import json
import re
from pathlib import Path

import pytest

from arena_kit.agents.buyer import guard, observe, policy
from arena_kit.agents.buyer.agent import BuyerAgent
from arena_kit.agents.buyer.arbiter import Arbiter
from arena_kit.agents.buyer.config import OrchestratorConfig
from arena_kit.agents.buyer.contracts import ConcessionFit, Email, OfferPlan, Tactic
from arena_kit.agents.buyer.telemetry import Telemetry
from arena_kit.agents.buyer.tools import DataTools
from arena_kit.client import ArenaError
from arena_kit.data import DataPackage

KIT = Path(__file__).parents[1]
DATA = KIT.parent / "data"
S01 = json.loads((KIT / "arena_kit/sim_cases/S01.json").read_text(encoding="utf-8"))  # one supplier
S02 = json.loads((KIT / "arena_kit/sim_cases/S02.json").read_text(encoding="utf-8"))  # RFQ, two suppliers
A, B = S02["suppliers"]
SID = S01["suppliers"][0]


def quote(sid: str, n: int, price: float, status: str = "open", days: int = 30) -> dict:
    sheet = {"term_sheet_id": f"offer:{sid[4:]}-{n}", "terms": {"unit_price": price, "payment_days": days}}
    return {"from": f"sales@{sid[4:]}", "status": status, "term_sheets": [sheet], "attachments": [], "body": "Offer."}


def rfq_quote(sid: str, n: int, price: float, status: str = "open") -> dict:
    msg = quote(sid, n, price, status)
    msg["term_sheets"][0]["terms"]["lead_time_days"] = 42
    return msg


def ours(price: float, days: int = 30, extra: dict | None = None) -> dict:
    terms = {"unit_price": price, "payment_days": days, **(extra or {})}
    return {"from": "buyer", "subject": "Re", "body": "Our proposal.", "term_sheets": [{"terms": terms}], "refs": []}


# A thread whose supplier went final, got our one counter, and went final again: no further counter is allowed.
A_COUNTERED = [
    rfq_quote(A, 0, 17.2),
    ours(15.0, extra={"lead_time_days": 42}),
    rfq_quote(A, 1, 16.7, "final_offer"),
    ours(15.5, extra={"lead_time_days": 42}),
    rfq_quote(A, 2, 16.6, "final_offer"),
]


def session_for(case: dict, threads: dict, **config):
    agent = BuyerAgent(DataTools(), None, None, None, None, None, OrchestratorConfig(**config), Telemetry("off"))
    return agent.open_case(case, threads)


def with_alternative(case: dict, price: float) -> dict:
    out = copy.deepcopy(case)
    out["buyer"]["alternative"] = {"kind": "quote", "ref": None, "terms": {"unit_price": price, "payment_days": 30}}
    return out


class FakeTable:
    session_id = "fake"

    def __init__(self):
        self.closed = []

    def close(self, supplier_id, message):
        self.closed.append((supplier_id, message["action"]))
        return {"ended": True, "deal": message["action"]["type"] == "accept"}

    def result(self):
        return {"ended": True, "deal": False}


# ---- acceptability and the verdict (the closing rules, per thread) -------------------------------------------------


def test_verdict_when_only_the_opening_quote_exists_never_accepts():
    s = session_for(S01, {SID: [quote(SID, 0, 17.4)]})
    v = policy.verdict(s, s.threads[SID])
    assert v.kind == "counter" and not v.accept_permitted


def test_acceptable_when_final_offer_is_dearer_than_its_own_opening_refuses_it():
    # sandbox D02: a final offer at 16.10 was accepted although the same supplier had opened at 16.00
    s = session_for(S01, {SID: [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 17.6, "final_offer")]})
    th = s.threads[SID]
    assert not policy.acceptable(s, th) and policy.verdict(s, th).kind == "dead"


def test_verdict_when_one_supplier_gives_its_final_offer_accepts_it_never_counters():
    s = session_for(S01, {SID: [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 16.9, "final_offer")]})
    assert policy.verdict(s, s.threads[SID]).kind == "accept"


def test_verdict_when_supplier_agrees_to_our_sheet_accepts():
    s = session_for(S01, {SID: [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 15.0, "agreed")]})
    assert policy.verdict(s, s.threads[SID]).kind == "accept"


def test_verdict_when_only_one_message_is_left_closes():
    case = {**S01, "round_limit": 2}
    s = session_for(case, {SID: [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 16.9)]})
    assert policy.last_word(s, s.threads[SID]) and policy.verdict(s, s.threads[SID]).kind == "accept"


def plan_at(sid: str, *prices: float) -> OfferPlan:
    return OfferPlan(sid, tuple({"unit_price": p, "payment_days": 30} for p in prices), (), "")


def test_dominates_when_its_sheet_costs_no_more_than_our_next_offer_lets_the_agent_accept():
    s = session_for(S01, {SID: [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 15.6)]})
    th = s.threads[SID]
    v = policy.verdict(s, th)
    assert v.kind == "counter" and v.accept_permitted  # acceptable, so the agent may ask...
    assert not policy.dominates(s, th, plan_at(SID, 15.3))  # ...but our next offer may still get it cheaper
    assert policy.dominates(s, th, plan_at(SID, 15.6, 15.7))  # it already gives what our offer would
    assert not policy.dominates(s, th, None)


def test_dominates_when_another_bidder_stands_cheaper_refuses():
    b_events = [rfq_quote(B, 0, 17.5), ours(15.0, extra={"lead_time_days": 42}), rfq_quote(B, 1, 15.1)]
    a_events = [rfq_quote(A, 0, 17.2), ours(15.0, extra={"lead_time_days": 42}), rfq_quote(A, 1, 15.4)]
    s = session_for(S02, {A: a_events, B: b_events})
    plan = OfferPlan(A, ({"unit_price": 15.6, "payment_days": 30, "lead_time_days": 42},), (), "")
    assert not policy.dominates(s, s.threads[A], plan)  # B's standing sheet is cheaper than A's


def test_verdict_when_an_acceptable_final_offer_waits_for_a_bidder_in_flight_at_most_the_age_guard():
    b_events = [rfq_quote(B, 0, 17.5), ours(15.0, extra={"lead_time_days": 42}), rfq_quote(B, 1, 17.0)]
    s = session_for(S02, {A: A_COUNTERED, B: b_events})
    a, b = s.threads[A], s.threads[B]
    b.in_flight = True  # our message to B is out, its reply not back yet
    assert policy.verdict(s, a, now=a.received_at + 5).kind == "park"
    assert policy.verdict(s, a, now=a.received_at + policy.AGE_GUARD_S + 1).kind == "accept"  # never lose it


def test_verdict_when_a_competitive_bidder_is_open_it_gets_one_final_challenge():
    b_events = [rfq_quote(B, 0, 17.5), ours(15.0, extra={"lead_time_days": 42}), rfq_quote(B, 1, 16.7)]
    s = session_for(S02, {A: A_COUNTERED, B: b_events})
    a, b = s.threads[A], s.threads[B]
    assert b.c <= a.c * policy.CHALLENGE_SHARE
    challenge = policy.verdict(s, b)
    assert challenge.kind == "counter" and challenge.challenge == (a.standing_id, B)
    assert policy.verdict(s, a, now=a.received_at + 1).kind == "park"  # A's last word waits for the challenge
    s.challenged.add(challenge.challenge)
    assert policy.verdict(s, b).kind == "park"  # one challenge per bidder and last word
    assert policy.verdict(s, a, now=a.received_at + 1).kind == "accept"


def test_verdict_when_a_bidder_is_more_than_3_percent_dearer_it_is_not_challenged():
    b_events = [rfq_quote(B, 0, 19.5), ours(15.0, extra={"lead_time_days": 42}), rfq_quote(B, 1, 19.0)]
    s = session_for(S02, {A: A_COUNTERED, B: b_events})
    assert policy.verdict(s, s.threads[B]).kind == "park"
    assert policy.verdict(s, s.threads[A]).kind == "accept"


def test_verdict_when_a_bidder_met_our_own_package_after_two_messages_takes_it():
    ours_terms = {"unit_price": 15.5, "payment_days": 30, "lead_time_days": 42}
    b_events = [rfq_quote(B, 0, 17.5), ours(15.0, extra={"lead_time_days": 42}), rfq_quote(B, 1, 16.9),
                ours(15.5, extra={"lead_time_days": 42}), rfq_quote(B, 2, 15.5)]  # fmt: skip
    b_events[-1]["term_sheets"][0]["terms"] = dict(ours_terms)
    a_events = [rfq_quote(A, 0, 17.2), ours(15.0, extra={"lead_time_days": 42}), rfq_quote(A, 1, 16.8)]
    s = session_for(S02, {A: a_events, B: b_events})
    assert policy.met_our_bid(s, s.threads[B]) and policy.verdict(s, s.threads[B]).kind == "accept"


def test_walk_reason_when_alternative_beats_a_supplier_at_its_last_word_walks():
    s = session_for(
        with_alternative(S01, 15.0), {SID: [quote(SID, 0, 17.4), ours(14.5), quote(SID, 1, 16.9, "final_offer")]}
    )
    assert policy.walk_reason(s) is not None


def test_walk_reason_when_an_acceptable_sheet_exists_never_walks():
    s = session_for(with_alternative(S01, 17.3), {SID: [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 16.0)]})
    assert policy.acceptable(s, s.threads[SID]) and policy.walk_reason(s) is None


def test_walk_reason_without_alternative_only_when_nothing_can_move():
    moving = session_for(S01, {SID: [quote(SID, 0, 17.4), ours(14.0), quote(SID, 1, 17.6)]})
    assert policy.walk_reason(moving) is None
    dead = session_for(S01, {SID: [quote(SID, 0, 17.4), ours(14.0), quote(SID, 1, 17.6, "final_offer")]})
    assert policy.walk_reason(dead) == "nothing acceptable and no thread can move"


def test_coerce_tactic_keeps_the_decision_agent_inside_its_permissions(monkeypatch):
    monkeypatch.setenv("BUYER_PUSH", "1")
    s = session_for(S01, {SID: [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 16.9)]})
    th = s.threads[SID]
    th.fit = MOVING
    v = policy.Verdict("counter", "negotiate", accept_permitted=False)
    t = policy.coerce_tactic(s, th, Tactic(SID, "accept", (0, 5, 0, True), price="push"), v, 2)
    assert t.action == "send" and t.claims == (0,) and t.price == "push"
    walked = policy.coerce_tactic(s, th, Tactic(SID, "walk_away"), v, 0)
    assert walked.action == "send"
    other = policy.coerce_tactic(s, th, Tactic("sup:other", "accept", claims=(0,), price="push"),
                                 policy.Verdict("counter", "x", True), 2)  # fmt: skip
    assert other.supplier_id == SID and other.action == "send" and other.claims == () and other.price == "follow"
    final = session_for(S01, {SID: [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 16.9, "final_offer")]})
    pushed = policy.coerce_tactic(final, final.threads[SID], Tactic(SID, "send", price="push"), v, 0)
    assert pushed.price == "follow"  # never push after its last word


def test_coerce_tactic_keeps_the_agent_package_to_negotiable_non_price_terms():
    s = session_for(S01, {SID: [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 16.9)]})
    th = s.threads[SID]
    v = policy.Verdict("counter", "negotiate")
    proposed = {"payment_days": 14, "unit_price": 12.0, "incoterm": "EXW", "lead_time_days": None}
    t = policy.coerce_tactic(s, th, Tactic(SID, "send", package=proposed), v, 0)
    assert t.package == {"payment_days": 14}  # code sets every price; fixed terms stay fixed
    assert policy.coerce_tactic(s, th, Tactic(SID, "send", package={"unit_price": 1.0}), v, 0).package is None
    assert policy.coerce_tactic(s, th, Tactic(SID, "send", package="cheaper"), v, 0).package is None


def test_arbiter_when_the_case_cap_is_near_accepts_the_best_sheet():
    import time as _time

    from arena_kit.agents.buyer.arbiter import Arbiter

    s = session_for(S01, {SID: [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 16.9)]}, close_margin_s=60)
    s.table, s.deadline = FakeTable(), (lambda: _time.time() + 30)
    assert policy.acceptable(s, s.threads[SID])
    Arbiter(s).step()
    assert s.table.closed == [(SID, {"type": "accept", "term_sheet_id": s.threads[SID].standing_id})] and s.ended


# ---- guard ---------------------------------------------------------------------------------------------


def test_check_sheets_drops_sheets_we_would_not_want_accepted():
    case = copy.deepcopy(S01)
    case["buyer"]["must_haves"] = [{"field": "payment_days", "op": ">=", "value": 30}]
    s = session_for(case, {SID: [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 16.5)]})
    th = s.threads[SID]
    sheets = [
        {"unit_price": 16.0, "payment_days": 60},  # fine
        {"unit_price": 15.0, "payment_days": 14},  # breaks the must-have
        {"unit_price": 16.8, "payment_days": 30},  # dearer than its own standing sheet
    ]
    kept, problems = guard.check_sheets(s, th, sheets)
    assert kept == [{"unit_price": 16.0, "payment_days": 60}] and len(problems) == 2
    assert guard.check_sheets(s, th, [{"unit_price": 0, "payment_days": 30}]) == ([], ["sheet dropped: no valid price"])
    assert len(guard.check_sheets(s, th, sheets + [{"unit_price": 15.9, "payment_days": 60}])[0]) == 1  # max 3 read


def test_normalise_sheet_keeps_the_suppliers_standard_value_and_snaps_others():
    s = session_for(S01, {SID: [quote(SID, 0, 17.4)]})
    th = s.threads[SID]
    th.std = {"payment_days": 21}  # its quote PDF: a standard term outside our options (the arena allows it)
    assert guard.normalise_sheet(s, th, {"unit_price": 16.004, "payment_days": 21}) == {
        "unit_price": 16.0,
        "payment_days": 21,
    }
    assert guard.normalise_sheet(s, th, {"unit_price": 16.0, "payment_days": 20})["payment_days"] == 14


def test_clean_body_removes_unknown_and_duplicate_ids_extra_arguments_and_leaks():
    s = session_for(S01, {SID: [quote(SID, 0, 17.4)]})
    known = {f"idx:PPI-C2431@202{y}-08" for y in (5, 6)} | {"sc:70270@2026Q3", "po:1-10", "po:2-10"}
    known |= {"fc:320050@2026-09", "cost:320050"}
    s.set_lookup(lambda ref: ref in known)
    s.burned = {"sc:70270@2026Q3"}
    body = (
        "Dear Sir or Madam,\n\nthank you for your offer. "
        "Bars rose 12.3% from 2025-08 to 2026-08 (idx:PPI-C2431@2025-08, idx:PPI-C2431@2026-08). "
        "Your on-time delivery was 84.6% (sc:70270@2026Q3). "
        "We cite a fact that does not exist (idx:FAKE@2026-08). "
        "Your orders were at 15.50 and 15.60 (po:1-10, po:2-10). "
        "Our forecast is up 9.4% (fc:320050@2026-09). "
        "Our cost estimate is EUR 14.10 (cost:320050). "
        "Wir brauchen das dringend. "
        "We look forward to your answer."
    )
    text, cited, problems = guard.clean_body(s, s.threads[SID], body)
    assert "idx:FAKE" not in text and "sc:70270" not in text and "dringend" not in text
    assert "We look forward to your answer." in text and "thank you for your offer." in text
    # three usable arguments go out (index, orders, forecast); the fourth (cost) is cut by the per-message limit
    assert cited == {"idx:PPI-C2431@2025-08", "idx:PPI-C2431@2026-08", "po:1-10", "po:2-10", "fc:320050@2026-09"}
    assert "cost:320050" not in text
    assert any("already cited" in p for p in problems) and any("unknown ID" in p for p in problems)
    assert any("more than 3 arguments" in p for p in problems) and any("leak" in p for p in problems)


def test_clean_body_cites_a_rivals_live_quote_never_the_suppliers_own_offer():
    # the user allowed rival quotes in a case with several bidders; its own offer proves nothing
    s = session_for(S02, {A: [rfq_quote(A, 0, 17.2)], B: [rfq_quote(B, 0, 17.5)]})
    rival, own = f"offer:{A[4:]}-0", f"offer:{B[4:]}-0"
    body = f"Another supplier currently quotes EUR 17.20 per price unit ({rival}). Your offer is at EUR 17.50 ({own})."
    text, cited, _ = guard.clean_body(s, s.threads[B], body)
    assert rival in text and own not in text and cited == {rival}
    s.threads[B].cited_offers.add(rival)  # once per supplier
    assert rival not in guard.clean_body(s, s.threads[B], body)[0]


def test_build_message_when_the_body_is_cut_too_short_returns_none():
    s = session_for(S01, {SID: [quote(SID, 0, 17.4)]})
    email = Email("Re: x", "Wir brauchen das dringend.")
    assert guard.build_message(s, s.threads[SID], [{"unit_price": 16.0, "payment_days": 30}], email) is None


def test_observe_rebuild_derives_burned_ids_offers_and_language():
    events = [
        quote(SID, 0, 17.4),
        {**ours(16.0), "body": "Prices fell -3.7% (idx:PPI-D351@2025-08, idx:PPI-D351@2026-08)."},
    ]
    events.append({**quote(SID, 1, 17.0), "body": "Guten Tag, wir prüfen Ihr Angebot und das Zahlungsziel mit Ihnen."})
    s = session_for(S01, {SID: events})
    assert s.burned == {"idx:PPI-D351@2025-08", "idx:PPI-D351@2026-08"}
    assert s.offers == {f"offer:{SID[4:]}-0", f"offer:{SID[4:]}-1"}
    assert s.threads[SID].c_history[0] > s.threads[SID].c_history[1]
    assert observe.detect_language(events[-1]["body"]) == "de"
    assert observe.detect_language("Dear Sir, thank you for your offer and the payment terms.") == "en"


# ---- infrastructure ------------------------------------------------------------------------------------


@pytest.mark.skipif(not (DATA / "world.sqlite").exists(), reason="data package not present")
def test_buyer_factory_shares_one_thread_safe_package_and_live_supplier_estimates():
    from arena_kit.agents.buyer.agent import buyer_factory
    from arena_kit.agents.buyer.tools.safe_package import ThreadSafePackage

    agent = buyer_factory(DataPackage(DATA))()
    assert isinstance(agent.package, ThreadSafePackage)
    assert agent.tools.package is agent.package  # one lock for the tools and the orchestrator, not two
    case = json.loads((Path(__file__).parent / "fixtures/practice_cases.json").read_text(encoding="utf-8"))[0]
    sid = case["suppliers"][0]
    quote_terms = {f: (case.get("term_options", {}).get(f) or [None])[0] for f in case["negotiable"]}
    quote_terms["unit_price"] = case["buyer"]["current_unit_price"] * 1.1
    events = [{"from": f"sales@{sid[4:]}", "status": "open", "attachments": [], "body": "Quote.",
               "term_sheets": [{"term_sheet_id": f"offer:{sid[4:]}-0", "terms": quote_terms}]}]  # fmt: skip
    s = agent.open_case(case, {sid: events})
    expected = agent.tools.supplier_from_thread(case, sid, events)
    assert s.estimates[sid] == expected  # the orchestrator prices C with the tools' own estimate


def test_agent_code_never_reads_the_live_feed():
    # brief §7: reading /v1/events, /screen, /leaderboard or /results during the final disqualifies
    forbidden = re.compile(r"leaderboard|/events|/screen|/results")
    roots = [KIT / "arena_kit/agents/buyer", KIT / "scripts"]
    hits = [
        f"{p.relative_to(KIT)}:{n}"
        for root in roots
        for p in root.rglob("*.py")
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if forbidden.search(line)
    ]
    assert hits == []


# ---- competing sheets and the arbiter ------------------------------------------------------------------


def test_arbiter_when_nothing_beats_the_alternative_walks_once():
    from arena_kit.agents.buyer.arbiter import Arbiter

    s = session_for(
        with_alternative(S01, 15.0), {SID: [quote(SID, 0, 17.4), ours(14.5), quote(SID, 1, 16.9, "final_offer")]}
    )
    s.table = FakeTable()
    arbiter = Arbiter(s)
    arbiter.step()
    arbiter.step()
    assert s.table.closed == [(SID, {"type": "walk_away"})] and s.ended


def test_reserve_when_two_threads_cite_the_same_id_only_the_first_keeps_it():
    # integration run with the real data: two RFQ bidders were sent the same index pair in the same second
    s = session_for(S02, {A: [rfq_quote(A, 0, 17.2)], B: [rfq_quote(B, 0, 17.5)]})
    known = {"idx:LCI-C@2025-06", "idx:LCI-C@2026-06"}
    s.set_lookup(lambda ref: ref in known)
    claim = "Labour costs changed +2.7% from 2025-06 to 2026-06 (idx:LCI-C@2025-06, idx:LCI-C@2026-06)."
    body = f"Dear Sir or Madam,\n\nthank you for your offer. {claim} Please find our proposal attached. Kind regards"
    message = {"subject": "Re", "body": body, "term_sheets": [], "refs": sorted(known)}
    first = guard.reserve(s, s.threads[A], message)  # both were built before either was sent
    second = guard.reserve(s, s.threads[B], message)
    assert claim in first["body"] and first["refs"] == sorted(known)
    assert claim not in second["body"] and second["refs"] == [] and "proposal attached" in second["body"]
    assert known <= s.burned


# ---- the supplier's alternative sheets are offers too ---------------------------------------------------------


def test_rebuild_when_supplier_sends_an_alternative_stands_on_its_first_sheet():
    # practice P18 (5fe255084dc62c8c): accepting an alternative's ID got "422 not a standing offer in this
    # session" (api.md: accept takes the supplier's standing term sheet) - the standing sheet is its first one
    s = session_for(S01, {SID: [quote(SID, 0, 10.0)]})
    th = s.threads[SID]
    reply = quote(SID, 1, 9.8)
    reply["term_sheets"].append({"term_sheet_id": f"offer:{SID[4:]}-1-a", "terms": {"unit_price": 9.1}})
    th.events += [ours(8.5), reply]
    observe.rebuild(s, th)
    assert th.standing_id == f"offer:{SID[4:]}-1"


class RefusingTable:
    """The arena at the system boundary: refuses every closing action like P18's alternative accept."""

    def __init__(self):
        self.closes = 0

    def close(self, supplier_id, message):
        self.closes += 1
        raise ArenaError(422, "not a standing offer in this session")

    def result(self):
        return None


def test_try_accept_when_the_arena_refuses_never_offers_that_sheet_again():
    # P18 (5fe255084dc62c8c): a refused accept was retried in a tight loop until the supplier's clock ran out
    s = session_for(S01, {SID: [quote(SID, 0, 10.0), ours(8.5), quote(SID, 1, 9.0, "final_offer")]})
    th = s.threads[SID]
    s.table = RefusingTable()
    assert policy.acceptable(s, th)
    assert not Arbiter(s).try_accept(th, "test")
    assert not policy.acceptable(s, th) and policy.verdict(s, th).kind != "accept"
    assert not Arbiter(s).try_accept(th, "again") and s.table.closes == 1


class AgreeingTable:
    """The arena at the system boundary: the supplier agrees to our sheet; the accept succeeds."""

    def __init__(self):
        self.closed = []

    def send(self, supplier_id, message):
        sheet = {"term_sheet_id": f"offer:{supplier_id[4:]}-9", "terms": message["term_sheets"][0]["terms"]}
        return {"from": "sales", "status": "agreed", "term_sheets": [sheet], "attachments": [], "body": "Agreed."}

    def close(self, supplier_id, message):
        self.closed.append(message["action"])
        return {"ended": True, "deal": True}

    def result(self):
        return {"ended": True, "deal": True}


def test_send_when_the_supplier_agrees_leaves_the_decision_to_the_verdict():
    # sweep e848872: accepting an agreement inside send() signed P05's dearer supplier (0.07) a second before the
    # cheaper one answered; the user: no instant accept - accept only what meets our expectation (the verdict)
    from arena_kit.agents.buyer.runner import ThreadWorker

    agent = BuyerAgent(DataTools(), None, None, None, None, None, OrchestratorConfig(), Telemetry("off"))
    s = agent.open_case(S01, {SID: [quote(SID, 0, 10.0)]})
    s.table = AgreeingTable()
    th = s.threads[SID]
    message = {"subject": "Re", "body": "Our proposal.", "term_sheets": [{"terms": {"unit_price": 9.0}}], "refs": []}
    ThreadWorker(agent, s, Arbiter(s)).send(th, message, "counter")
    assert not s.ended and s.table.closed == [] and th.status == "agreed"


class RoundLimitTable:
    """The arena when a thread has used its rounds: every message is refused, the standing sheet can be accepted."""

    def __init__(self):
        self.sent, self.closed = 0, []

    def send(self, supplier_id, message):
        self.sent += 1
        raise ArenaError(422, "round limit reached: accept the standing term sheet or walk away")

    def close(self, supplier_id, message):
        self.closed.append(message["action"])
        return {"ended": True, "deal": True}

    def result(self):
        return {"ended": True, "deal": True}


def test_send_when_the_round_limit_is_reached_stops_sending_and_lets_the_policy_close():
    # P05 (02111fa2d1c4eb09): the arena said "round limit reached: accept the standing term sheet or walk away";
    # we re-sent three times, gave up on the thread, and the case timed out at 0
    from arena_kit.agents.buyer.runner import ThreadWorker

    agent = BuyerAgent(DataTools(), None, None, None, None, None, OrchestratorConfig(), Telemetry("off"))
    s = agent.open_case(S01, {SID: [quote(SID, 0, 10.0), ours(8.0), quote(SID, 1, 9.0)]})
    s.table = RoundLimitTable()
    th = s.threads[SID]
    message = {"subject": "Re", "body": "Our proposal.", "term_sheets": [{"terms": {"unit_price": 8.2}}], "refs": []}
    ThreadWorker(agent, s, Arbiter(s)).send(th, message, "counter")
    assert s.table.sent == 1 and th.moves_left(S01) == 0
    assert policy.verdict(s, th).kind in ("accept", "dead")  # no messages left: take it if acceptable, never resend


def test_close_when_the_arena_says_the_thread_ended_marks_it_dead():
    # P05: accepts on a supplier that had walked were retried every second ("the thread ... has ended")
    s = session_for(S01, {SID: [quote(SID, 0, 10.0)]})
    th = s.threads[SID]

    class EndedTable(RoundLimitTable):
        def close(self, supplier_id, message):
            raise ArenaError(422, f"the thread with {supplier_id} has ended (supplier_walked)")

    s.table = EndedTable()
    Arbiter(s)._close(th, {"action": {"type": "accept", "term_sheet_id": "offer:x"}}, "test")
    assert not th.alive


# ---- the decision agent's push: limits kept in code --------------------------------------------------------------

MOVING = ConcessionFit(SID, (17.4, 16.9), None, 2.9, False)  # its last step: 2.9 %, still a real step


def test_push_only_while_the_supplier_still_moves_in_real_steps(monkeypatch):
    monkeypatch.setenv("BUYER_PUSH", "1")
    s = session_for(S01, {SID: [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 17.35)]})
    th = s.threads[SID]
    assert not policy.push_allowed(s, th)  # no fit of its price path yet
    th.fit = ConcessionFit(SID, (17.4, 17.35), None, 0.3, False)
    assert not policy.push_allowed(s, th)  # a token step
    th.fit = ConcessionFit(SID, (17.4, 16.9), None, 2.9, False, p_room=0.1)
    assert not policy.push_allowed(s, th)  # its price path says it stopped
    th.fit = MOVING
    assert policy.push_allowed(s, th)


def test_push_when_our_last_message_pushed_follows_the_planner(monkeypatch):
    monkeypatch.setenv("BUYER_PUSH", "1")
    # practice run 2026-10-25: four pushes in a row (P07) and the supplier went final a round earlier
    s = session_for(S01, {SID: [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 16.9)]})
    th = s.threads[SID]
    th.fit = MOVING
    v = policy.Verdict("counter", "negotiate")
    assert policy.coerce_tactic(s, th, Tactic(SID, "send", price="push"), v, 0).price == "push"
    th.pushed = (1,)  # our first message pushed
    assert policy.coerce_tactic(s, th, Tactic(SID, "send", price="push"), v, 0).price == "follow"


def test_push_at_most_twice_per_supplier_and_never_with_the_switch_off(monkeypatch):
    monkeypatch.setenv("BUYER_PUSH", "1")
    events = [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 16.9), ours(15.2), quote(SID, 2, 16.7),
              ours(15.4), quote(SID, 3, 16.5)]  # fmt: skip
    s = session_for(S01, {SID: events})
    th = s.threads[SID]
    th.fit = ConcessionFit(SID, (17.4, 16.9, 16.7, 16.5), None, 1.2, False)
    v = policy.Verdict("counter", "negotiate")
    th.pushed = (1,)
    assert policy.push_allowed(s, th)  # message 3 follows a non-pushed message 2
    th.pushed = (1, 2)
    assert not policy.push_allowed(s, th)  # two already
    th.pushed = ()
    monkeypatch.delenv("BUYER_PUSH")  # off by default
    assert policy.coerce_tactic(s, th, Tactic(SID, "send", price="push"), v, 0).price == "follow"


def test_reserve_arguments_when_two_bidders_pick_the_same_one_the_second_takes_the_next_free():
    # local simulator, real model: two RFQ threads chose the same index pair at once and the guard dropped it from
    # the second e-mail, which went out one argument short
    from dataclasses import replace as _replace

    from arena_kit.agents.buyer.contracts import Claim
    from arena_kit.agents.buyer.pipeline import RolePipeline

    s = session_for(S02, {A: [rfq_quote(A, 0, 17.2)], B: [rfq_quote(B, 0, 17.5)]})
    shared = (
        Claim("i", ("idx:x@1", "idx:x@2"), "index", -2.0, True, 1.0),
        Claim("v", ("fc:1",), "volume", 5.0, True, 0.8),
        Claim("d", ("sc:1",), "delivery", 80.0, True, 0.6),
    )
    ctx = _replace(s.ctx, claims={A: shared, B: shared})
    a = RolePipeline.reserve_arguments(s, s.threads[A], ctx, Tactic(A, "send", (0, 1)))
    b = RolePipeline.reserve_arguments(s, s.threads[B], ctx, Tactic(B, "send", (0,)))
    assert a.claims == (0, 1) and b.claims == (2,)  # one argument each time, never the same data twice
    again = RolePipeline.reserve_arguments(s, s.threads[A], ctx, Tactic(A, "send", (0,)))
    assert again.claims == (0,)  # its own reservation stays its own


def test_wait_turn_lets_the_thread_ahead_in_the_claiming_order_choose_first():
    # P05 practice runs: three bidders drew from one argument pool in whatever order their threads ran
    import threading as _threading
    import time as _time

    s = session_for(S02, {A: [rfq_quote(A, 0, 17.2)], B: [rfq_quote(B, 0, 17.5)]})
    s.choosing = {A: (0, 100.0, A), B: (0, 200.0, B)}  # A is cheaper: it claims first
    done = []

    def ahead():
        _time.sleep(0.2)
        with s.lock:
            s.choosing.pop(A)
        s.notify()

    _threading.Thread(target=ahead).start()
    t0 = _time.monotonic()
    s.wait_turn(B, timeout=5.0)
    done.append(_time.monotonic() - t0)
    assert 0.1 < done[0] < 2.0  # B waited for A, not for the timeout
    t0 = _time.monotonic()
    s.wait_turn(A, timeout=5.0)  # nothing ahead of A (it is not choosing any more): no wait
    assert _time.monotonic() - t0 < 0.1
    s.choosing = {A: (0, 100.0, A), B: (0, 200.0, B)}
    t0 = _time.monotonic()
    s.wait_turn(B, timeout=0.3)  # A never claims: B goes after the timeout, never stuck
    assert 0.25 < _time.monotonic() - t0 < 1.0


def test_trim_allowed_while_it_moves_in_real_steps_even_twice_in_a_row_push_stays_opt_in(monkeypatch):
    monkeypatch.delenv("BUYER_PUSH", raising=False)
    monkeypatch.setenv("BUYER_TRIM", "1")
    s = session_for(S01, {SID: [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 16.9)]})
    th = s.threads[SID]
    v = policy.Verdict("counter", "negotiate")
    assert not policy.margin_allowed(s, th, "trim")  # no fit of its price path yet
    th.fit = MOVING
    th.pushed = (1,)  # a push in our last message does not stop a trim: a trim still moves
    assert policy.margin_allowed(s, th, "trim") and not policy.margin_allowed(s, th, "push")
    assert policy.coerce_tactic(s, th, Tactic(SID, "send", price="trim"), v, 0).price == "trim"
    assert policy.coerce_tactic(s, th, Tactic(SID, "send", price="push"), v, 0).price == "follow"
    final = session_for(S01, {SID: [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 16.9, "final_offer")]})
    final.threads[SID].fit = MOVING
    assert not policy.margin_allowed(final, final.threads[SID], "trim")  # never after its last word


def test_trim_is_off_by_default(monkeypatch):
    monkeypatch.delenv("BUYER_TRIM", raising=False)
    s = session_for(S01, {SID: [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 16.9)]})
    s.threads[SID].fit = MOVING
    assert not policy.margin_allowed(s, s.threads[SID], "trim")


# ---- review fixes (fix/hybrid-review): alternatives at a last word, 409 round limit, no e-mail twice ----------------


def with_alternative_sheet(message: dict, price: float, days: int = 30) -> dict:
    out = copy.deepcopy(message)
    tag = out["term_sheets"][0]["term_sheet_id"]
    out["term_sheets"].append({"term_sheet_id": f"{tag}-a", "terms": {"unit_price": price, "payment_days": days}})
    return out


class SendingTable(FakeTable):
    """The arena at the system boundary: records every message, the supplier agrees to our first sheet."""

    def __init__(self):
        super().__init__()
        self.sent = []

    def send(self, supplier_id, message):
        self.sent.append(message)
        sheet = {"term_sheet_id": f"offer:{supplier_id[4:]}-9", "terms": message["term_sheets"][0]["terms"]}
        return {"from": "sales", "status": "agreed", "term_sheets": [sheet], "attachments": [], "body": "Agreed."}


def rule_agent() -> BuyerAgent:
    from arena_kit.agents.buyer.roles import LLMWriter, PassthroughProfiler, RuleAnalyst, RuleStrategist

    return BuyerAgent(
        DataTools(), PassthroughProfiler(), RuleAnalyst(), RuleStrategist(), LLMWriter(None), None,
        OrchestratorConfig(), Telemetry("off"),
    )  # fmt: skip


def test_verdict_when_a_final_offer_has_a_cheaper_alternative_sends_that_alternative_back():
    # P18 (sweep 4c30c05): the main sheet was accepted at C 294k next to its own alternative at C 274k
    final = with_alternative_sheet(quote(SID, 1, 9.0, "final_offer"), 8.7)
    s = session_for(S01, {SID: [quote(SID, 0, 10.0), ours(8.5), final]})
    v = policy.verdict(s, s.threads[SID])
    assert v.kind == "counter" and v.send_back == {"unit_price": 8.7, "payment_days": 30}


def test_verdict_when_its_cheaper_alternative_was_already_sent_back_accepts_the_standing_sheet():
    final = with_alternative_sheet(quote(SID, 1, 9.0, "final_offer"), 8.7)
    again = with_alternative_sheet(quote(SID, 2, 9.0, "final_offer"), 8.7)
    s = session_for(S01, {SID: [quote(SID, 0, 10.0), ours(8.5), final, ours(8.7), again]})
    v = policy.verdict(s, s.threads[SID])
    assert v.send_back is None and v.kind == "accept"


def test_worker_when_a_final_offer_has_a_cheaper_alternative_sends_exactly_that_alternative():
    from arena_kit.agents.buyer.runner import ThreadWorker

    agent = rule_agent()
    final = with_alternative_sheet(quote(SID, 1, 9.0, "final_offer"), 8.7)
    s = agent.open_case(S01, {SID: [quote(SID, 0, 10.0), ours(8.5), final]})
    s.table = SendingTable()
    ThreadWorker(agent, s, Arbiter(s)).iteration(s.threads[SID])
    assert [ts["terms"] for ts in s.table.sent[0]["term_sheets"]] == [{"unit_price": 8.7, "payment_days": 30}]
    assert s.table.closed == []


def test_try_accept_when_another_bidder_has_a_cheaper_alternative_to_send_back_waits_for_it():
    a_final = rfq_quote(A, 1, 16.6, "final_offer")
    b_final = with_alternative_sheet(rfq_quote(B, 1, 16.9, "final_offer"), 15.9)
    b_final["term_sheets"][1]["terms"]["lead_time_days"] = 42
    s = session_for(S02, {A: [rfq_quote(A, 0, 17.2), ours(15.0, extra={"lead_time_days": 42}), a_final],
                          B: [rfq_quote(B, 0, 17.4), ours(15.0, extra={"lead_time_days": 42}), b_final]})  # fmt: skip
    s.table = FakeTable()
    assert policy.verdict(s, s.threads[B]).send_back is not None
    assert not Arbiter(s).try_accept(s.threads[A], "test") and s.table.closed == []


class RoundLimit409Table(RoundLimitTable):
    def send(self, supplier_id, message):
        self.sent += 1
        raise ArenaError(409, "round limit reached: accept the standing term sheet or walk away")


def test_send_when_the_round_limit_comes_as_409_stops_sending_and_lets_the_policy_close():
    # api.md: "409 ... you reached the round limit (you may still accept)"
    from arena_kit.agents.buyer.runner import ThreadWorker

    agent = BuyerAgent(DataTools(), None, None, None, None, None, OrchestratorConfig(), Telemetry("off"))
    s = agent.open_case(S01, {SID: [quote(SID, 0, 10.0), ours(8.0), quote(SID, 1, 9.0)]})
    s.table = RoundLimit409Table()
    th = s.threads[SID]
    message = {"subject": "Re", "body": "Our proposal.", "term_sheets": [{"terms": {"unit_price": 8.2}}], "refs": []}
    ThreadWorker(agent, s, Arbiter(s)).send(th, message, "counter")
    assert s.table.sent == 1 and th.exhausted and th.moves_left(S01) == 0
    assert policy.verdict(s, th).kind in ("accept", "dead")


def test_build_message_when_identical_to_our_last_message_returns_none():
    # sandbox D02 (18:22): the same keep-alive went out 3x - "your email system is stuck"
    body = "Thank you for your offer. We propose the attached package and look forward to your answer soon."
    last = {"from": "buyer", "subject": "Re", "body": body, "refs": [],
            "term_sheets": [{"terms": {"unit_price": 8.5, "payment_days": 30}}]}  # fmt: skip
    s = session_for(S01, {SID: [quote(SID, 0, 10.0), last, quote(SID, 1, 9.6)]})
    th = s.threads[SID]
    sheets = [{"unit_price": 8.5, "payment_days": 30}]
    assert guard.build_message(s, th, sheets, Email("Re", body, ())) is None
    assert guard.build_message(s, th, sheets, Email("Re", body + " Which terms matter most to you?", ())) is not None


def test_worker_when_the_plan_repeats_our_last_e_mail_asks_a_new_question_instead():
    from arena_kit.agents.buyer.pipeline import RoleOutput
    from arena_kit.agents.buyer.runner import ThreadWorker

    agent = rule_agent()
    last = ours(8.5)
    last["body"] = "Thank you for your offer. We propose the attached package and look forward to your answer soon."
    s = agent.open_case(S01, {SID: [quote(SID, 0, 10.0), last, quote(SID, 1, 9.6)]})
    s.table = SendingTable()
    th = s.threads[SID]
    same = OfferPlan(SID, ({"unit_price": 8.5, "payment_days": 30},), (1.0,), "step 0")
    fit = ConcessionFit(SID, (), None, None, False)
    email = Email("Re", last["body"], ())
    agent.pipeline.run = lambda *a, **k: RoleOutput(Tactic(SID, "send"), same, email, fit, None)
    agent.pipeline.rule_email = lambda *a, **k: email  # the deterministic composer writes the same e-mail again
    ThreadWorker(agent, s, Arbiter(s)).iteration(th)
    sent = s.table.sent[0]
    assert sent["body"] != last["body"] and "?" in sent["body"] and s.table.closed == []


def test_repeat_message_asks_a_new_question_each_time_and_stops_when_the_supplier_stands_still():
    from arena_kit.agents.buyer.pipeline import repeat_message

    s = session_for(S01, {SID: [quote(SID, 0, 10.0), ours(8.5), quote(SID, 1, 9.6)]})
    th = s.threads[SID]
    first = repeat_message(s, th)
    assert first is not None and first["body"] != "Our proposal."
    th.events += [{**first, "from": "buyer"}, quote(SID, 2, 9.5)]  # it still moved: one more, with a new question
    observe.rebuild(s, th)
    second = repeat_message(s, th)
    assert second is not None and second["body"] != first["body"]
    th.events += [{**second, "from": "buyer"}, quote(SID, 3, 9.5)]  # it stood still after our repeat: stop
    observe.rebuild(s, th)
    assert repeat_message(s, th) is None


def test_stage_when_the_role_pool_is_shut_down_uses_the_rule_twin(monkeypatch):
    # sandbox D02: "RuntimeError: cannot schedule new futures after shutdown" from a worker still running at exit
    from concurrent.futures import ThreadPoolExecutor

    from arena_kit.agents.buyer import pipeline

    dead = ThreadPoolExecutor(max_workers=1)
    dead.shutdown()
    monkeypatch.setattr(pipeline, "_POOL", dead)
    used, timings = {}, {}
    out = pipeline.RolePipeline(DataTools(), None, None, None)._stage("x", lambda: 1, lambda: 2, 5.0, used, timings)
    assert out == 2 and used["x"] == "skipped"


def test_fetch_all_when_an_attachment_is_slow_returns_within_its_budget():
    # runner.py: one attachment GET took 76 s (archived v2, run 4) - the opening must not wait for it
    import time

    from arena_kit.agents.buyer import runner

    class SlowDocs:
        def attachment(self, attachment_id):
            time.sleep(1.0)
            return b"# quote\n"

    msg = quote(SID, 0, 10.0)
    msg["attachments"] = [{"attachment_id": "att1", "name": "quote.md", "type": "md"}]
    t0 = time.monotonic()
    pending = runner._fetch_all(SlowDocs(), {SID: [msg]}, budget_s=0.1)
    assert time.monotonic() - t0 < 0.5 and pending
    for future, _ in pending:
        future.result(timeout=5)
    assert "text" in msg["attachments"][0]
