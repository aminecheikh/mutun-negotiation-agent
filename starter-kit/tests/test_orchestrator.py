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
from arena_kit.agents.buyer.contracts import ConcessionFit, Email, Tactic
from arena_kit.agents.buyer.pipeline import keepalive_email, keepalive_message
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


# ---- acceptability and verdicts ------------------------------------------------------------------------


def test_verdict_when_only_the_opening_quote_exists_never_accepts():
    s = session_for(S01, {SID: [quote(SID, 0, 17.4)]})
    v = policy.verdict(s, s.threads[SID])
    assert v.kind == "counter" and not v.accept_permitted


def test_acceptable_when_final_offer_is_dearer_than_its_own_opening_refuses_it():
    # sandbox D02: a final offer at 16.10 was accepted although the same supplier had opened at 16.00
    s = session_for(S01, {SID: [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 17.6, "final_offer")]})
    th = s.threads[SID]
    assert not policy.acceptable(s, th)
    assert policy.verdict(s, th).kind != "accept"


def test_verdict_when_supplier_agreed_to_our_sheet_accepts():
    s = session_for(S01, {SID: [quote(SID, 0, 17.4), ours(16.5), quote(SID, 1, 16.5, "agreed")]})
    v = policy.verdict(s, s.threads[SID])
    assert v.kind == "accept" and "agreed" in v.reason


def test_verdict_when_another_supplier_may_still_go_lower_accepts_this_final_offer():
    # no inter-supplier comparison (organisers): a thread is never held back by what another supplier might do
    s = session_for(
        S02,
        {
            A: A_COUNTERED,
            B: [rfq_quote(B, 0, 17.5), ours(15.0, extra={"lead_time_days": 42}), rfq_quote(B, 1, 17.0)],
        },
    )
    v = policy.verdict(s, s.threads[A])
    assert policy.acceptable(s, s.threads[A])
    assert v.kind == "accept"


def test_verdict_when_rival_is_at_its_final_offer_too_accepts_the_cheaper():
    s = session_for(
        S02,
        {
            A: A_COUNTERED,
            B: [rfq_quote(B, 0, 17.5), ours(15.0, extra={"lead_time_days": 42}), rfq_quote(B, 1, 17.0, "final_offer")],
        },
    )
    assert policy.verdict(s, s.threads[A]).kind == "accept"
    assert policy.best_acceptable(s) is s.threads[A]


def test_counter_after_final_offer_when_far_above_target_is_allowed_once():
    case = with_alternative(S01, 17.0)
    events = [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 16.9, "final_offer")]
    s = session_for(case, {SID: events})
    first = policy.verdict(s, s.threads[SID])
    assert first.kind == "counter" and first.accept_permitted and "final offer" in first.reason
    s = session_for(case, {SID: events + [ours(15.5), quote(SID, 2, 16.9, "final_offer")]})
    assert s.threads[SID].final_countered
    assert policy.verdict(s, s.threads[SID]).kind == "accept"


def test_walk_reason_when_alternative_beats_a_stuck_supplier_walks():
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


def test_coerce_tactic_keeps_the_strategist_inside_its_permissions():
    s = session_for(S01, {SID: [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 16.9)]})
    th = s.threads[SID]
    v = policy.Verdict("counter", "negotiate", accept_permitted=False)
    t = policy.coerce_tactic(
        s, th, Tactic(SID, "accept", ("unit_price", "bogus", "payment_days"), (0, 5, 0, True)), v, 2
    )
    assert t.action == "send" and t.claims == (0,) and t.levers == ("unit_price", "payment_days")
    walked = policy.coerce_tactic(s, th, Tactic(SID, "walk_away"), v, 0)
    assert walked.action == "send" and th.walk_hint
    other = policy.coerce_tactic(
        s, th, Tactic("sup:other", "accept", claims=(0,)), policy.Verdict("counter", "x", True), 2
    )
    assert other.supplier_id == SID and other.action == "send" and other.claims == ()


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


def test_clean_body_never_cites_another_suppliers_offer_nor_its_own():
    # no inter-supplier comparison (organisers): another bidder's offer never reaches a supplier
    s = session_for(S02, {A: [rfq_quote(A, 0, 17.2)], B: [rfq_quote(B, 0, 17.5)]})
    rival, own = f"offer:{A[4:]}-0", f"offer:{B[4:]}-0"
    body = f"We hold an offer at EUR 17.20 ({rival}). Your offer stands at EUR 17.50 ({own}). Please review it."
    text, cited, _ = guard.clean_body(s, s.threads[B], body)
    assert rival not in text and own not in text and cited == set()


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


class FakeTable:
    session_id = "fake"

    def __init__(self):
        self.closed = []

    def close(self, supplier_id, message):
        self.closed.append((supplier_id, message["action"]))
        return {"ended": True, "deal": message["action"]["type"] == "accept"}

    def result(self):
        return {"ended": True, "deal": False}


def test_verdict_when_a_cheaper_acceptable_sheet_exists_never_accepts_the_dearer():
    a_events = [rfq_quote(A, 0, 17.2), ours(15.0, extra={"lead_time_days": 42}), rfq_quote(A, 1, 16.0, "agreed")]
    b_events = [rfq_quote(B, 0, 17.5), ours(15.0, extra={"lead_time_days": 42}), rfq_quote(B, 1, 16.4, "agreed")]
    s = session_for(S02, {A: a_events, B: b_events})
    v = policy.verdict(s, s.threads[B])
    assert v.kind == "park"  # B agreed, but A's acceptable sheet is cheaper: we sign A, not B
    assert policy.verdict(s, s.threads[A]).kind == "accept"


def test_verdict_when_dearer_thread_can_still_beat_the_cheaper_keeps_negotiating():
    from arena_kit.agents.buyer.contracts import ConcessionFit

    b_events = [rfq_quote(B, 0, 17.5), ours(15.0, extra={"lead_time_days": 42}), rfq_quote(B, 1, 16.0)]
    s = session_for(S02, {A: A_COUNTERED, B: b_events})
    s.threads[B].c = s.threads[A].c * 1.001  # B just above A
    s.threads[B].fit = ConcessionFit(B, (17.5, 16.0), 14.0, 8.6, False)  # its path says it can go far lower
    v = policy.verdict(s, s.threads[B])
    assert v.kind == "counter" and not v.accept_permitted


def test_arbiter_when_every_thread_waits_in_the_freeze_accepts_the_best_sheet():
    import time as _time

    from arena_kit.agents.buyer.arbiter import Arbiter

    s = session_for(S01, {SID: [quote(SID, 0, 17.4), ours(15.0), quote(SID, 1, 16.9)]}, close_margin_s=10, freeze_s=100)
    s.table, s.deadline = FakeTable(), (lambda: _time.time() + 50)  # inside the freeze, outside the close margin
    assert policy.acceptable(s, s.threads[SID])
    Arbiter(s).step()
    assert s.table.closed == [(SID, {"type": "accept", "term_sheet_id": s.threads[SID].standing_id})]
    assert s.ended and "negotiating" in s.end_reason


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


def test_verdict_when_another_supplier_has_room_does_not_hold_up_an_accept():
    b_events = [rfq_quote(B, 0, 17.5), ours(15.0, extra={"lead_time_days": 42}), rfq_quote(B, 1, 17.0)]
    s = session_for(S02, {A: A_COUNTERED, B: b_events})
    s.threads[B].fit = ConcessionFit(B, (17.5, 17.0), 14.0, 2.9, False, p_room=0.9)  # B could go far lower
    assert policy.verdict(s, s.threads[A]).kind == "accept"


# ---- keep-alive e-mails ---------------------------------------------------------------------------------------


def keepalive_thread():
    s = session_for(S01, {SID: [quote(SID, 0, 10.0)]})
    return s, s.threads[SID]


def test_keepalive_email_when_one_was_already_sent_never_repeats_the_body():
    # sandbox D02 (ed152d4335edfbca): the same keep-alive went out three times; the supplier saw a stuck system
    s, th = keepalive_thread()
    bodies = []
    for n in range(4):
        email = keepalive_email(s, th)
        assert email.body not in bodies
        bodies.append(email.body)
        th.events.append({"from": "buyer", "body": email.body, "term_sheets": []})
        th.events.append(quote(SID, n + 1, 10.0))


def test_keepalive_email_when_strategist_asked_a_plain_question_uses_it():
    s, th = keepalive_thread()
    question = "Which payment term would let you move on price?"
    assert question in keepalive_email(s, th, question).body


def test_keepalive_email_when_strategist_question_has_figures_drops_it():
    s, th = keepalive_thread()
    question = "Could you reach 9.10 if we pay in 14 days?"  # figures in a keep-alive bypass the writer's checks
    assert "9.10" not in keepalive_email(s, th, question).body


# ---- a cost breakdown re-prices the band before the verdict ----------------------------------------------------

SANDBOX = {c["case_id"]: c for c in json.loads((KIT / "tests/fixtures/sandbox_cases.json").read_text())}
J1, C1 = "sup:DEMO-J1", "sup:DEMO-C1"
BREAKDOWN = (
    "[Cost breakdown]\nElement | EUR per unit | Share of price\n"
    "BAR-14021 (material / cost driver) | 5.56 | =B2/B6\nWAGES-METAL (material / cost driver) | 4.39 | =B3/B6\n"
    "conversion (machine-hour rate x time) | 2.81 | =B4/B6\noverhead and profit | 2.94 | =B5/B6\n"
    "Unit price | =SUM(B2:B5) | =SUM(C2:C5)"
)


def d02_quote(sid: str, n: int, terms: dict, attachments=()) -> dict:
    sheet = {"term_sheet_id": f"offer:{sid[4:]}-{n}", "terms": terms}
    return {"from": f"sales@{sid[4:]}", "status": "open", "term_sheets": [sheet], "attachments": list(attachments),
            "body": "Offer."}  # fmt: skip


def test_verdict_when_a_cost_breakdown_arrives_judges_against_the_repriced_band():
    # sandbox D02 (53ce03986722d2b5): the band was re-priced only on a counter, so after J1 sent its breakdown the
    # policy still saw its 15.70 sheet "at our target" and parked it with keep-alives instead of pushing on
    case = SANDBOX["case:D02"]
    agent = BuyerAgent(DataTools(DataPackage(DATA)), None, None, None, None, None, OrchestratorConfig(),
                       Telemetry("off"))  # fmt: skip
    j1_open = d02_quote(J1, 0, {"unit_price": 17.1, "payment_days": 30, "lead_time_days": 28})
    c1_open = d02_quote(C1, 0, {"unit_price": 16.0, "payment_days": 45, "incoterm": "EXW"})
    s = agent.open_case(case, {J1: [j1_open], C1: [c1_open]})
    th = s.threads[J1]
    standing = {"unit_price": 15.7, "payment_days": 30, "lead_time_days": 28, "incoterm": "DAP"}
    th.events += [
        ours(14.9, 60, {"lead_time_days": 28, "incoterm": "DAP"}),
        d02_quote(J1, 1, standing, [{"name": "cost-breakdown.xlsx", "text": BREAKDOWN}]),
    ]
    observe.rebuild(s, th)
    agent.pipeline.sync(s, th)
    assert s.band(J1).c_target < th.c * 0.95  # its own cost says it can go well below its standing sheet
    assert policy.verdict(s, th).kind == "counter"


def test_offers_more_when_counter_to_a_final_offer_repeats_our_sheets_is_false():
    # D02 (53ce03986722d2b5): our one counter to J1's final offer repeated our last sheets; it walked away
    last = {"unit_price": 15.0, "payment_days": 30, "lead_time_days": 42}
    s = session_for(S02, {A: [rfq_quote(A, 0, 17.2), ours(15.0, extra={"lead_time_days": 42}),
                             rfq_quote(A, 1, 16.7, "final_offer")], B: [rfq_quote(B, 0, 18.0)]})  # fmt: skip
    th = s.threads[A]
    assert not policy.offers_more(s, th, [last])
    assert policy.offers_more(s, th, [{**last, "unit_price": 15.4}])


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
    s = session_for(S01, {SID: [quote(SID, 0, 10.0)]})
    th = s.threads[SID]
    band = s.band(SID)
    reply = quote(SID, 1, band.target)
    reply["term_sheets"][0]["terms"] = dict(band.target_terms)
    th.events += [ours(round(band.target * 0.9, 2)), reply]
    observe.rebuild(s, th)
    s.table = RefusingTable()
    assert policy.acceptable(s, th)
    assert not Arbiter(s).try_accept(th, "test")
    assert not policy.acceptable(s, th) and policy.verdict(s, th).kind != "accept"
    assert not Arbiter(s).try_accept(th, "again") and s.table.closes == 1


# ---- the target is a prior in the first half of the rounds -------------------------------------------------------


def at_target_thread(fit=None):
    s = session_for(S01, {SID: [quote(SID, 0, 10.0)]})
    th = s.threads[SID]
    band = s.band(SID)
    reply = quote(SID, 1, band.target)
    reply["term_sheets"][0]["terms"] = dict(band.target_terms)  # it offers exactly our target package
    th.events += [ours(round(band.target * 0.9, 2)), reply]
    observe.rebuild(s, th)
    th.fit = fit
    return s, th


def test_verdict_when_at_target_in_the_first_half_keeps_negotiating():
    # practice P01/P03/P11 (4c30c05) closed at our band target after one message: target scores were 0.81, 0.84,
    # 0.60 while the baseline reached 0.91 on P01/P03 - our target is an estimate the supplier's moves can beat
    s, th = at_target_thread()
    v = policy.verdict(s, th)
    assert v.kind == "counter" and not v.accept_permitted


def test_verdict_when_at_target_and_its_prices_show_no_room_accepts():
    s, th = at_target_thread(ConcessionFit(SID, (10.0, 9.0), 8.9, -10.0, True, 0.05))
    assert policy.verdict(s, th).kind == "accept"


# ---- every message carries an offer ----------------------------------------------------------------------------


def test_keepalive_message_when_our_last_offer_still_stands_sends_it_again():
    # sweep 4c30c05: 7 of 28 messages carried no term sheet; a message without a package gets "without movement
    # from your side" (practice replays) - a keep-alive repeats our last offer, which still stands
    s = session_for(S01, {SID: [quote(SID, 0, 10.0)]})
    th = s.threads[SID]
    th.events += [ours(8.5), quote(SID, 1, 9.8)]
    observe.rebuild(s, th)
    message = keepalive_message(s, th)
    assert [ts["terms"]["unit_price"] for ts in message["term_sheets"]] == [8.5]
    assert "proposal" in message["body"].lower()


def test_keepalive_message_after_a_final_offer_sends_no_sheet():
    s = session_for(S01, {SID: [quote(SID, 0, 10.0)]})
    th = s.threads[SID]
    th.events += [ours(8.5), quote(SID, 1, 9.8, "final_offer")]
    observe.rebuild(s, th)
    assert keepalive_message(s, th)["term_sheets"] == []
