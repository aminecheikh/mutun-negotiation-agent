"""The orchestrator's deterministic policy (owner: Jeddou): what is allowed this round, and when to close.

Division of labour (docs/team/DECISIONS.md): tools compute prices and C, roles read the supplier and write,
the orchestrator decides what is *permitted*. This module never invents a price. It answers four questions:

  acceptable    may we take this standing sheet at all?  must-haves on closing terms, cheaper than the best
                opening, not dearer than our alternative - a deal outside that scores 0 or loses to no deal
  verdict       for one thread whose supplier spoke last: accept now, counter, or park (wait / keep alive)
  walk          for the case: is walking away better than anything that can still happen?
  tactic        clamp the strategist's tactic to the verdict (it may choose accept only where permitted, cite
                only unused claims, trade only negotiable terms)
"""

import math
from dataclasses import dataclass, replace
from typing import Literal

from ...cost import complete_offer, must_haves_ok, supplier_value, total_cost
from .contracts import Tactic
from .state import CaseSession, ThreadState

Kind = Literal["accept", "counter", "park", "dead"]
TERMS = {
    "unit_price", "payment_days", "volume_per_year", "contract_months", "effective_date", "lead_time_days",
    "incoterm", "index_clause", "tooling_eur", "ppm_commitment", "chargeback_eur_per_complaint",
}  # fmt: skip


@dataclass(frozen=True)
class Verdict:
    kind: Kind  # accept: close now | counter: send an e-mail | park: wait, keep alive | dead: nothing useful to do
    reason: str
    accept_permitted: bool = False  # on a counter: the strategist may still choose to accept this sheet
    note: str = ""  # one line for the logs and the strategist's rationale


# ---- the acceptability invariant ------------------------------------------------------------------------


def acceptable(session: CaseSession, th: ThreadState) -> bool:
    """The standing sheet keeps every must-have on its closing terms, beats the cheapest opening and does not
    lose to our alternative. Outside this a deal scores 0 (dearer than the opening, broken must-have) or worse
    than walking away (dearer than the alternative)."""
    return th.alive and standing_ok(session, th)


def standing_ok(session: CaseSession, th: ThreadState) -> bool:
    """The acceptability test on the standing sheet alone, whether or not we still work the thread."""
    if not th.standing_id or math.isinf(th.c) or th.standing_id in th.refused:
        return False
    if not must_haves_ok(session.case, th.standing_terms, th.std):
        return False
    if not th.c < session.c_open:
        return False
    return session.c_alt is None or th.c <= session.c_alt


def sheet_acceptable(session: CaseSession, th: ThreadState, sheet: dict) -> tuple[bool, str]:
    """The same invariant for a sheet *we* send: the supplier may answer 'agreed', so we must be content with
    every sheet we offer. Also never offer more than its own standing sheet asks of us."""
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
    """C_open as the arena defines it: the cheapest opening quote that keeps the must-haves (our evaluator)."""
    best = math.inf
    for th in session.threads.values():
        quote = next((m for m in th.events if m.get("from") != "buyer"), None)
        sheets = (quote or {}).get("term_sheets") or []
        if not sheets or sheets[0].get("terms", {}).get("unit_price") is None:
            continue
        terms = sheets[0]["terms"]
        if must_haves_ok(session.case, terms, th.std):
            best = min(best, session.cost(th.supplier_id, terms))
    return best


def alternative_cost(case: dict) -> float | None:
    """C of what we get without a deal (same definition as tools.bands.alternative_cost)."""
    alt = case.get("buyer", {}).get("alternative") or {}
    if alt.get("kind") in (None, "none") or not alt.get("terms", {}).get("unit_price"):
        return None
    return total_cost(case, alt["terms"])


# ---- comparing threads ------------------------------------------------------------------------------------


def best_acceptable(session: CaseSession, idle_only: bool = True) -> ThreadState | None:
    """The cheapest acceptable standing sheet; ties go to the thread with fewer of our messages."""
    pool = [t for t in session.alive() if acceptable(session, t) and not (idle_only and t.in_flight)]
    return min(pool, key=lambda t: (t.c, t.our_rounds), default=None)


def reachable_c(session: CaseSession, th: ThreadState) -> float:
    """Where this thread can plausibly still go, in C: where it is now if its price path leaves little room
    (`p_room`), else its standing sheet at the price tools estimate it stops at, else our band target."""
    if th.status in ("final_offer", "agreed") or not th.standing_terms:
        return th.c
    fit, band = th.fit, session.band(th.supplier_id)
    if fit is not None and fit.p_room is not None and fit.p_room < session.config.low_room_probability:
        return th.c  # its own price path says it is (almost) done: tools.concession p_room
    price = fit.reservation_estimate if fit and fit.reservation_estimate else None
    if price is None and band is not None:
        return min(th.c, band.c_target)
    if price is None:
        return th.c
    return min(th.c, session.cost(th.supplier_id, {**th.standing_terms, "unit_price": price}))


def rival_may_beat(session: CaseSession, th: ThreadState) -> str | None:
    """Why closing on `th` now could leave money on the table: another live thread that can plausibly come in
    cheaper and still has room to negotiate, or an unanswered rival that was close. None if nothing can."""
    cfg = session.config
    for other in session.alive():
        if other is th:
            continue
        if other.in_flight and other.c < th.c * (1 + cfg.rival_in_flight_margin):
            return f"{other.supplier_id} is answering and stood within {cfg.rival_in_flight_margin:.0%}"
        if other.status in ("final_offer", "agreed") or other.moves_left(session.case) < 2:
            continue
        if reachable_c(session, other) < th.c * (1 - cfg.rival_beat_margin):
            return f"{other.supplier_id} can plausibly go below {th.c:,.0f}"
    return None


def stuck(session: CaseSession, th: ThreadState) -> bool:
    """No more progress is likely on this thread."""
    cfg = session.config
    if not th.alive or th.status == "final_offer" or th.moves_left(session.case) <= 0:
        return True
    hist = [c for c in th.c_history if not math.isinf(c)]
    if th.our_rounds >= cfg.stuck_rounds and len(hist) >= 3:
        return hist[-1] >= hist[-3] * (1 - cfg.stuck_improvement)
    return False


def counter_allowed(session: CaseSession, th: ThreadState) -> bool:
    """One counter after a final offer, only when it is worth the risk of the supplier walking: clearly above
    our target, room in rounds and time, a fallback if it walks, and a persona that does not walk easily."""
    cfg, band = session.config, session.band(th.supplier_id)
    if th.final_countered or band is None or band.c_target <= 0:
        return False
    gap = (th.c - band.c_target) / band.c_target
    left = session.seconds_left()
    fallback = session.c_alt is not None or any(acceptable(session, t) for t in session.alive() if t is not th)
    return (
        gap > cfg.final_counter_gap
        and th.moves_left(session.case) >= cfg.final_counter_min_moves
        and (left is None or left >= cfg.final_counter_min_seconds)
        and fallback
        and session.persona(th.supplier_id) not in cfg.walk_prone_personas
    )


def offers_more(session: CaseSession, th: ThreadState, sheets: list[dict]) -> bool:
    """Our planned sheets give the supplier more than our last message did (its estimated cash value, else the
    price): a counter to a final offer without that is "push further" with no move of ours - it may walk."""
    ours = [m for m in th.events if m.get("from") == "buyer" and m.get("term_sheets")]
    if not ours or not sheets:
        return bool(sheets)
    last = [ts.get("terms", ts) for ts in ours[-1]["term_sheets"]]
    supplier = session.estimates.get(th.supplier_id)

    def worth(terms: dict) -> float:
        if supplier is None:
            return float(terms.get("unit_price") or 0.0)
        return supplier_value(session.case, complete_offer(session.case, terms), supplier)

    return max(map(worth, sheets)) > max(map(worth, last)) + 1e-6


# ---- the per-thread verdict ---------------------------------------------------------------------------------


def verdict(session: CaseSession, th: ThreadState) -> Verdict:
    """For a live thread whose supplier spoke last. Forced closes come from rules; between them the strategist
    decides, and may choose accept only where `accept_permitted` says so."""
    case, cfg = session.case, session.config
    if not th.alive:
        return Verdict("dead", f"thread ended ({th.status})")
    ok = acceptable(session, th)
    left = th.moves_left(case)
    if th.our_rounds == 0 and th.status != "agreed":
        return Verdict("counter", "first answer: never take the opening quote")
    rival = rival_may_beat(session, th) if ok else None
    band = session.band(th.supplier_id)
    early = target_is_prior(session, th)
    at_target = ok and band is not None and th.c <= band.c_target * (1 + cfg.accept_eps) and not early
    final_closed = th.status == "final_offer" and not counter_allowed(session, th)
    forced = [
        why
        for cond, why in (
            (th.status == "agreed", "supplier agreed to our sheet"),
            (left <= 0, "no messages left"),
            (at_target, "standing sheet at our target"),
            (final_closed, "final offer, no counter worth the risk"),
            (session.in_close_window(), "case cap is near"),
        )
        if cond
    ]
    cheaper = cheaper_elsewhere(session, th) if ok else None
    if ok and forced:
        if cheaper is not None:  # never close above another acceptable sheet: beat it, or hold and let it close
            if can_still_beat(session, th, cheaper):
                return Verdict("counter", f"{forced[0]}, but {cheaper.supplier_id} is cheaper: push on")
            return Verdict("park", f"{forced[0]}, but {cheaper.supplier_id} is cheaper", note="hold the sheet")
        if rival and not session.in_close_window():
            return Verdict("park", f"{forced[0]}, but {rival}", note="hold the sheet, keep the thread alive")
        return Verdict("accept", forced[0])
    if left <= 0:
        return Verdict("dead", "no messages left and the sheet is not acceptable")
    if th.status == "final_offer":
        if counter_allowed(session, th):
            return Verdict(
                "counter", "final offer clearly above target: one counter", accept_permitted=ok and not rival,
                note="this is our one answer to its final offer: add a term it values, do not only ask a lower price",
            )  # fmt: skip
        return Verdict("dead", "final offer we cannot accept and should not push")
    if session.freeze and best_acceptable(session, idle_only=False) is not None:
        return Verdict("park", "freeze before the cap: an acceptable sheet is on the table")
    return Verdict("counter", "negotiate", accept_permitted=ok and rival is None and cheaper is None and not early)


def target_is_prior(session: CaseSession, th: ThreadState) -> bool:
    """First half of the rounds and the supplier's prices still show room (or no fit yet): our target is an estimate
    its moves can beat, not a reason to close (practice P01/P03/P11 closed at it after one message)."""
    if th.status != "open":
        return False
    limit = session.case.get("round_limit", 10)
    if th.moves_left(session.case) <= limit * session.config.prior_share_of_rounds:
        return False
    fit = th.fit
    return fit is None or fit.p_room is None or fit.p_room >= session.config.low_room_probability


def cheaper_elsewhere(session: CaseSession, th: ThreadState) -> ThreadState | None:
    """Another live thread whose standing sheet is acceptable and cheaper than this one."""
    pool = [t for t in session.alive() if t is not th and acceptable(session, t) and t.c < th.c]
    return min(pool, key=lambda t: t.c, default=None)


def can_still_beat(session: CaseSession, th: ThreadState, other: ThreadState) -> bool:
    """This thread can plausibly come in below `other` and still has room to negotiate."""
    return (
        th.status not in ("final_offer", "agreed")
        and th.moves_left(session.case) >= 2
        and not session.in_close_window()
        and reachable_c(session, th) < other.c * (1 - session.config.rival_beat_margin)
    )


# ---- the case-level walk ------------------------------------------------------------------------------------


def walk_reason(session: CaseSession) -> str | None:
    """Walking away is a decision: only when no acceptable sheet exists and nothing can still produce one."""
    alive = session.alive()
    if not alive or any(t.in_flight for t in alive):
        return None
    if any(acceptable(session, t) for t in alive):
        return None
    if session.c_alt is None:  # walking and failing both score 0; walk only when nothing can move any more
        dead = all(t.moves_left(session.case) <= 0 or (t.status == "final_offer" and not counter_allowed(session, t))
                   for t in alive)  # fmt: skip
        return "nothing acceptable and no thread can move" if dead else None
    if not all(t.our_rounds >= 1 for t in alive):
        return None
    if all(stuck(session, t) for t in alive):
        return "the alternative beats every standing sheet and no supplier is moving"
    if all(t.walk_hint or stuck(session, t) for t in alive):
        return "strategist recommends walking; the alternative beats every standing sheet"
    return None


# ---- clamping the strategist ---------------------------------------------------------------------------------


def coerce_tactic(session: CaseSession, th: ThreadState, tactic: Tactic, v: Verdict, n_claims: int) -> Tactic:
    """The strategist's tactic within the verdict: our thread only, accept only where permitted, walk as a hint
    to the arbiter, claims only from the filtered list (at most 3), levers only negotiable terms."""
    action = tactic.action if tactic.supplier_id == th.supplier_id else "send"
    if action == "walk_away":
        th.walk_hint, action = True, "send"
    if action == "accept" and not v.accept_permitted:
        action = "send"
    picked = []
    for i in tactic.claims if tactic.supplier_id == th.supplier_id else ():
        if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < n_claims and i not in picked:
            picked.append(i)
    allowed = set(session.case.get("negotiable") or ()) | {"unit_price"}
    levers = tuple(dict.fromkeys(x for x in tactic.levers if isinstance(x, str) and x in allowed and x in TERMS))
    question = tactic.question.strip()[:300] if isinstance(tactic.question, str) and tactic.question.strip() else None
    rationale = f"{tactic.rationale} [{v.note}]" if v.note else tactic.rationale
    return replace(
        tactic,
        supplier_id=th.supplier_id,
        action=action,
        levers=levers or ("unit_price",),
        claims=tuple(picked[: session.config.max_claims]),
        question=question,
        rationale=rationale[:400],
    )
