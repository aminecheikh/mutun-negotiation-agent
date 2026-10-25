"""The orchestrator's deterministic policy (owner: Jeddou): what is permitted this round, and when to close.

Closing rules, per thread for our parallel runner (provenance: docs/hybrid.md):

  acceptable   may we take this standing sheet at all? must-haves on closing terms, cheaper than the best opening,
               not dearer than our alternative - a deal outside that scores 0 or loses to no deal
  verdict      for one thread whose supplier spoke last:
                 - never take the opening quote; answer it
                 - its last word (final offer, agreement, or our last message) closes: accept if acceptable - a
                   final offer is never countered - otherwise the thread is done
                 - several suppliers: an acceptable last word waits while another bidder's reply is in flight or a
                   competitive bidder still gets its one final challenge, at most 30 s (45 s if a pending bidder is
                   at least 1 % cheaper) so the offer cannot expire; a bidder more than 3 % dearer than the best
                   last word is not challenged; a bidder that met our own package after two messages is taken
                 - otherwise counter
  walk         walking away only when nothing acceptable exists and no thread can move any more

The decision agent works inside bounds this module keeps (docs/adr-bounded-agent.md): it may accept before the
rules only where accepting dominates our own next offer (`dominates`), push only within `push_allowed`, and any
package it proposes is priced by the planner at no more than the deterministic plan's C.
"""

import math
import os
import time
from dataclasses import dataclass, replace
from typing import Literal

from ...cost import complete_offer, must_haves_ok
from ...play import latest
from .contracts import OfferPlan, Tactic
from .state import CaseSession, ThreadState
from .tools import planner

Kind = Literal["accept", "counter", "park", "dead"]
AGE_GUARD_S, LONG_AGE_GUARD_S = 30.0, 45.0  # a last word waits at most this long for the other bidders
BETTER_PENDING_SHARE = 0.99  # a pending bidder at least 1 % cheaper earns the longer wait
CHALLENGE_SHARE = 1.03  # a bidder within 3 % of the best last word gets one final challenge
MATCHED_BID_SHARE = 1.001  # it met our own package within 0.1 % of its C
STOPPED_P_ROOM = 0.2  # concession fit: under a 1-in-5 chance of another >= 1 % step it has stopped
STOPPED_STEP_PCT = 0.3  # without p_room: shrinking steps under 0.3 % (analyst.md "almost nothing")
MAX_PUSHES = 2  # per supplier: the agent presses for price at most twice, never twice in a row
PUSH_MIN_STEP_PCT = 0.5  # a push only while its last price step was at least this (strategist.md: "a real step")


@dataclass(frozen=True)
class Verdict:
    kind: Kind  # accept: close now | counter: send an e-mail | park: wait | dead: nothing useful to do
    reason: str
    accept_permitted: bool = False  # on a counter: its sheet is acceptable; the agent's accept needs `dominates` too
    note: str = ""  # one line for the logs and the decision agent's rationale
    challenge: tuple[str, str] | None = None  # (best last word's sheet ID, this supplier): its one final challenge
    send_back: dict | None = None  # its own cheaper alternative sheet: our message is exactly that sheet


# ---- the acceptability invariant ---------------------------------------------------------------------------------


def acceptable(session: CaseSession, th: ThreadState) -> bool:
    return th.alive and standing_ok(session, th)


def standing_ok(session: CaseSession, th: ThreadState) -> bool:
    """The standing sheet keeps every must-have on its closing terms, beats the cheapest opening and does not lose
    to our alternative."""
    if not th.standing_id or math.isinf(th.c) or th.standing_id in th.refused:
        return False
    if not must_haves_ok(session.case, th.standing_terms, th.std):
        return False
    if not th.c < session.c_open:
        return False
    return session.c_alt is None or th.c <= session.c_alt


def sheet_acceptable(session: CaseSession, th: ThreadState, sheet: dict) -> tuple[bool, str]:
    """The same invariant for a sheet we send - the supplier may answer 'agreed', so we must be content with every
    sheet we offer - and never more than its own standing sheet asks of us."""
    if not must_haves_ok(session.case, sheet, th.std):
        return False, "breaks a must-have on its closing terms"
    c = session.cost(th.supplier_id, sheet)
    if math.isinf(c):
        return False, "cannot be priced"
    if not c < session.c_open:
        return False, f"C {c:,.0f} not below the best opening {session.c_open:,.0f}"
    if session.c_alt is not None and c > session.c_alt:
        return False, f"C {c:,.0f} above the alternative {session.c_alt:,.0f}"
    if c >= th.c:
        return False, f"C {c:,.0f} not below its own standing sheet {th.c:,.0f}"
    return True, ""


def opening_cost(session: CaseSession) -> float:
    """C_open as the arena defines it: the cheapest opening quote that keeps the must-haves."""
    best = math.inf
    for th in session.threads.values():
        quote = next((m for m in th.events if m.get("from") != "buyer" and m.get("term_sheets")), None)
        if quote is None:
            continue
        terms = quote["term_sheets"][0]["terms"]
        if must_haves_ok(session.case, terms, th.std):
            best = min(best, session.cost(th.supplier_id, terms))
    return best


def alternative_cost(case: dict) -> float | None:
    c = planner.alternative_cost(case)
    return None if math.isinf(c) else c


def best_acceptable(session: CaseSession, idle_only: bool = True) -> ThreadState | None:
    """The cheapest acceptable standing sheet; ties go to the thread with fewer of our messages."""
    pool = [t for t in session.alive() if acceptable(session, t) and not (idle_only and t.in_flight)]
    return min(pool, key=lambda t: (t.c, t.our_rounds), default=None)


# ---- thread states -----------------------------------------------------------------------------------------------


def last_word(session: CaseSession, th: ThreadState) -> bool:
    """Its final offer or agreement, or our last message: nothing more to negotiate on this thread."""
    return th.status in ("agreed", "final_offer") or th.moves_left(session.case) <= 1


def stopped(th: ThreadState) -> bool:
    """The supplier's own price path says it is at its limit (ConcessionFit). No fit yet: still moving."""
    fit = th.fit
    if fit is None:
        return False
    if fit.p_room is not None:
        return fit.p_room < STOPPED_P_ROOM
    return fit.last_step_pct is not None and fit.shrinking and abs(fit.last_step_pct) < STOPPED_STEP_PCT


def met_our_bid(session: CaseSession, th: ThreadState) -> bool:
    """After two of our messages its standing sheet is one of our last packages, within 0.1 % of its C."""
    if th.our_rounds < 2:
        return False
    ours = next((m.get("term_sheets") or [] for m in reversed(th.events) if m.get("from") == "buyer"), [])
    standing = complete_offer(session.case, th.standing_terms)
    for bid in ours:
        terms = complete_offer(session.case, bid.get("terms", bid))
        if planner.same_exchange(standing, terms) and th.c <= session.cost(th.supplier_id, terms) * MATCHED_BID_SHARE:
            return True
    return False


# ---- the per-thread verdict --------------------------------------------------------------------------------------


def verdict(session: CaseSession, th: ThreadState, now: float | None = None) -> Verdict:
    """For a live thread whose supplier spoke last."""
    if not th.alive:
        return Verdict("dead", f"thread ended ({th.status})")
    if th.our_rounds == 0 and th.status != "agreed":
        return Verdict("counter", "first answer: never take the opening quote")
    ok = acceptable(session, th)
    others = [t for t in session.alive() if t is not th]
    if session.in_close_window():
        return Verdict("accept", "case cap is near") if ok else Verdict("park", "case cap is near, not acceptable")
    if last_word(session, th) and th.moves_left(session.case) > 0 and (alt := cheaper_alternative(session, th)):
        # only its standing (first) sheet can be accepted (api.md): send its cheaper proposal back, it will agree
        return Verdict("counter", "its alternative sheet is cheaper for us: send exactly that alternative back",
                       send_back=alt)  # fmt: skip
    if last_word(session, th):
        if not ok:
            return Verdict("dead", "its last word is not acceptable: a final offer is never countered")
        wait = _wait_for_bidders(session, th, others, now)
        return Verdict("park", wait) if wait else Verdict("accept", f"its last word ({th.status})")
    if others:
        contacted = all(t.our_rounds > 0 for t in session.alive())
        pending = any(t.in_flight for t in others)
        if ok and contacted and not pending and met_our_bid(session, th):
            return Verdict("accept", "it met our own package: little left to negotiate")
        best = min((t for t in others if last_word(session, t) and acceptable(session, t)), key=lambda t: t.c,
                   default=None)  # fmt: skip
        if best is not None and contacted:
            if th.c > best.c * CHALLENGE_SHARE:
                return Verdict("park", f"more than 3 % dearer than {best.supplier_id}'s last word: no challenge")
            key = (best.standing_id, th.supplier_id)
            if key in session.challenged:
                return Verdict("park", f"already challenged against {best.supplier_id}'s last word")
            return Verdict("counter", f"one final challenge against {best.supplier_id}'s last word",
                           accept_permitted=ok, challenge=key)  # fmt: skip
    return Verdict("counter", "negotiate", accept_permitted=ok)


def _wait_for_bidders(session: CaseSession, th: ThreadState, others: list[ThreadState], now: float | None) -> str:
    """Why an acceptable last word should still wait for the other bidders, or '' to close now."""
    if not others:
        return ""
    pending = [t for t in others if t.in_flight]
    challengers = [
        t
        for t in others
        if not last_word(session, t)
        and t.c <= th.c * CHALLENGE_SHARE
        and (th.standing_id, t.supplier_id) not in session.challenged
    ]
    if not pending and not challengers:
        return ""
    better = any(t.c < th.c * BETTER_PENDING_SHARE for t in pending)
    challenged = any((th.standing_id, t.supplier_id) in session.challenged for t in pending)
    guard = LONG_AGE_GUARD_S if better or challenged else AGE_GUARD_S
    age = (now if now is not None else time.monotonic()) - th.received_at
    if age >= guard:
        return ""  # a last word can be withdrawn after the next timeout: never sacrifice it to a slow bidder
    return f"waiting for the other bidders' best offers ({age:.0f}/{guard:.0f} s)"


def cheaper_alternative(session: CaseSession, th: ThreadState) -> dict | None:
    """The cheapest alternative sheet of the supplier's latest e-mail that costs us less than its standing sheet,
    keeps the must-haves, beats the best opening and our alternative, and that we have not sent back already - its
    negotiable fields, ready to send. None otherwise (practice P18: the main sheet was accepted at C 294k next to
    its own alternative at C 274k)."""
    last = th.events[-1] if th.events and th.events[-1].get("from") != "buyer" else {}
    negotiable = list(session.case.get("negotiable") or ())
    sent = [
        complete_offer(session.case, ts.get("terms", ts))
        for m in th.events
        if m.get("from") == "buyer"
        for ts in m.get("term_sheets") or []
    ]
    best, best_c = None, th.c
    for ts in (last.get("term_sheets") or [])[1:]:
        terms = complete_offer(session.case, ts.get("terms") or {})
        if terms.get("unit_price") is None or not must_haves_ok(session.case, terms, th.std) or terms in sent:
            continue
        c = session.cost(th.supplier_id, terms)
        if c < best_c and c < session.c_open and (session.c_alt is None or c <= session.c_alt):
            best, best_c = {f: terms.get(f) for f in negotiable}, c
    return best


def alternative_pending(session: CaseSession, th: ThreadState) -> bool:
    """Another live thread is about to send back its supplier's alternative that is cheaper than this standing sheet:
    an accept here waits for that answer (outside the close window)."""
    for other in session.alive():
        if other is th or other.in_flight or not last_word(session, other) or other.moves_left(session.case) <= 0:
            continue
        alt = cheaper_alternative(session, other)
        if alt is not None and session.cost(other.supplier_id, alt) < th.c:
            return True
    return False


# ---- the case-level walk -----------------------------------------------------------------------------------------


def walk_reason(session: CaseSession) -> str | None:
    """Walking away is a decision: only when no acceptable sheet exists and no thread can move any more."""
    alive = session.alive()
    if not alive or any(t.in_flight for t in alive):
        return None
    if any(acceptable(session, t) for t in alive):
        return None
    if all(last_word(session, t) for t in alive):
        return "nothing acceptable and no thread can move"
    return None


# ---- the decision agent's margin: conceding less than the planner --------------------------------------------------


def margin_allowed(session: CaseSession, th: ThreadState, price: str) -> bool:
    """The decision agent may concede less than the planner - "trim" (half its step) or "push" (only the minimal
    step) - only while the supplier still moves in real steps (its last step at least PUSH_MIN_STEP_PCT, its price
    path not stopped), so never before its first counter-offer, and never after its last word. A push - standing
    nearly still - also never in two messages in a row and at most MAX_PUSHES per supplier (practice: four pushes in
    a row and the supplier went final a round earlier), and only with BUYER_PUSH=1: in two practice runs every push
    that changed an outcome lost (P07 twice, P08). A trim only with BUYER_TRIM=1: in practice a reciprocal supplier
    answered our smaller step with a smaller one of its own and a dearer final offer (P08 -0.03)."""
    if price == "follow":
        return True
    if price not in ("trim", "push") or last_word(session, th) or latest(th.events).get("status") != "open":
        return False
    step = th.fit.last_step_pct if th.fit is not None else None
    if step is None or step < PUSH_MIN_STEP_PCT or stopped(th):
        return False
    if price == "push":
        return (os.environ.get("BUYER_PUSH", "0") == "1" and th.our_rounds not in th.pushed
                and len(th.pushed) < MAX_PUSHES)  # fmt: skip
    return os.environ.get("BUYER_TRIM", "0") == "1"  # practice: a trimmed step drew a smaller step back (P08)


def push_allowed(session: CaseSession, th: ThreadState) -> bool:
    return margin_allowed(session, th, "push")


# ---- the decision agent's accept: only where it dominates our own next offer ---------------------------------------


def dominates(session: CaseSession, th: ThreadState, plan: OfferPlan | None) -> bool:
    """Accepting its standing sheet now is at least as good as anything the deterministic layer can get from this
    thread next round: the best the supplier can do with our offer is accept it, and its standing sheet already costs
    us no more than our cheapest sheet - and no other live bidder stands cheaper."""
    if plan is None or not plan.term_sheets or not acceptable(session, th):
        return False
    ours = min(session.cost(th.supplier_id, t) for t in plan.term_sheets)
    rivals = [t.c for t in session.alive() if t is not th and acceptable(session, t)]
    return th.c <= ours and all(th.c <= c for c in rivals)


# ---- clamping the decision agent -----------------------------------------------------------------------------------


def coerce_tactic(session: CaseSession, th: ThreadState, tactic: Tactic, v: Verdict, n_claims: int) -> Tactic:
    """The decision agent's tactic within the verdict: our thread only, accept only where permitted, walking is
    the policy's decision, claims only from the filtered list (at most 3), less concession only within
    `margin_allowed`, a package only of negotiable non-price terms."""
    mine = tactic.supplier_id == th.supplier_id
    action = tactic.action if mine and tactic.action == "accept" and v.accept_permitted else "send"
    picked = []
    for i in tactic.claims if mine else ():
        if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < n_claims and i not in picked:
            picked.append(i)
    question = tactic.question.strip()[:300] if isinstance(tactic.question, str) and tactic.question.strip() else None
    price = tactic.price if mine and margin_allowed(session, th, tactic.price) else "follow"
    package = agent_package(session.case, tactic.package) if mine and action == "send" else None
    rationale = f"{tactic.rationale} [{v.note}]" if v.note else tactic.rationale
    return replace(
        tactic,
        supplier_id=th.supplier_id,
        action=action,
        claims=tuple(picked[: session.config.max_claims]),
        question=question,
        rationale=rationale[:400],
        price=price,
        package=package,
    )


def agent_package(case: dict, package) -> dict | None:
    """The agent's proposed terms, kept to negotiable fields other than the price (code sets every price)."""
    if not isinstance(package, dict):
        return None
    allowed = set(case.get("negotiable") or ()) - {"unit_price"}
    kept = {k: v for k, v in package.items() if k in allowed and v is not None and not isinstance(v, (list, dict))}
    if package.get("index_clause") and "index_clause" in allowed and isinstance(package["index_clause"], dict):
        kept["index_clause"] = package["index_clause"]
    return kept or None
