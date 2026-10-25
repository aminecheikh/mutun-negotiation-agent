"""The negotiation runner (owner: Jeddou): one worker per supplier thread, one arbiter per case.

Why not the kit's `play()`: it makes one decision per loop and waits for that supplier's reply (up to a
minute), so in an RFQ the other threads' 60-second clocks run out - sandbox D02 lost its second bidder to three
timeouts, twice. Here every thread has its own worker that answers its supplier within the move budget, and the
arbiter alone accepts or walks, under the case lock.

    play_case(table, case_id, agent, deadline)   one case (practice, sandbox or a final case)
    play_set(new_table, case_ids, new_agent)      many cases in parallel (kit play_all semantics)
    play_final(client, new_agent, waves)          the Live Final in waves (kit play_waves semantics)
"""

import functools
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

from ...client import ArenaError
from ...play import fetch_documents, quota_used_up
from . import guard, observe, policy
from .arbiter import Arbiter
from .pipeline import repeat_message
from .state import CaseSession, ThreadState

log = logging.getLogger("arena")
_DOCS = ThreadPoolExecutor(max_workers=8, thread_name_prefix="buyer-docs")  # attachment downloads, never on a move


class ThreadWorker:
    """Negotiates one thread until it ends or the case ends."""

    def __init__(self, agent, session: CaseSession, arbiter: Arbiter):
        self.agent, self.s, self.arbiter = agent, session, arbiter

    def run(self, supplier_id: str) -> None:
        th, cfg = self.s.threads[supplier_id], self.s.config
        while not self.s.ended and th.alive and th.errors < cfg.errors_before_dead:
            if not th.awaiting_us:
                self.s.wait_change(cfg.worker_poll_s)
                continue
            try:
                self.iteration(th)
            except ArenaError as e:
                th.errors += 1
                log.warning("%s/%s: arena error %s: %s", self.s.case["case_id"], supplier_id, e.status, e.detail)
                time.sleep(min(2.0, cfg.worker_poll_s * 2))
            except Exception:
                th.errors += 1
                log.exception("%s/%s: round failed (%d errors)", self.s.case["case_id"], supplier_id, th.errors)
        if th.errors >= cfg.errors_before_dead:
            with self.s.lock:  # stop counting on a thread we can no longer work: rivals must not wait for it
                th.alive = False
            log.error("%s/%s: giving up on this thread after %d errors", self.s.case["case_id"], supplier_id, th.errors)
        self.s.notify()

    # ---- one round ---------------------------------------------------------------------------------

    def iteration(self, th: ThreadState) -> None:
        s, cfg = self.s, self.s.config
        deadline = th.received_at + cfg.move_budget_s
        with s.lock:  # a consistent view of every thread (others are rebuilt under this lock)
            v = policy.verdict(s, th)
        if v.kind == "accept":
            if self.arbiter.try_accept(th, v.reason):
                return
            v = policy.Verdict("park", f"accept deferred ({v.reason})")
        if v.kind in ("park", "dead"):
            self.park(th, v)
            return
        rule_only = th.rule_only_once or th.errors >= cfg.errors_before_rule_only
        th.rule_only_once = False
        out = self.agent.pipeline.run(s, th, v, deadline, rule_only=rule_only)
        if out.tactic.action == "accept":
            if self.arbiter.try_accept(th, f"decision agent: {out.tactic.rationale}"):
                return
            self.park(th, policy.Verdict("park", "decision agent's accept deferred: a cheaper sheet is on the table"))
            return
        checked = None
        if out.plan is not None and out.email is not None:
            checked = guard.build_message(s, th, out.plan.term_sheets, out.email)
            if checked is None:  # the guard cut the writer's e-mail too short: the template, same sheets
                email = self.agent.pipeline.rule_email(s, th, out.tactic, out.plan)
                checked = guard.build_message(s, th, out.plan.term_sheets, email)
        if checked is None or not checked.message.get("term_sheets"):
            # nothing new we would be content to offer: its standing sheet beats any step of ours - take it; else our
            # last offer again; else this thread cannot give a deal we accept - stop working it (the arbiter closes)
            if policy.acceptable(s, th) and self.arbiter.try_accept(th, "no counter beats its standing sheet"):
                return
            if (message := repeat_message(s, th)) is not None:
                self.send(th, message, "repeat", verdict=v, out=out, problems=("no new sheet: our last offer again",))
                return
            with s.lock:
                th.alive = False
            log.warning("%s/%s: no sheet we could send or accept - thread given up", s.case["case_id"], th.supplier_id)
            s.notify()
            return
        with s.lock:
            if v.challenge is not None:
                s.challenged.add(v.challenge)
            if out.tactic.price == "push":  # a trim still moves: only a push counts against the push limits
                th.pushed = (*th.pushed, th.our_rounds + 1)
        self.send(th, checked.message, "counter", verdict=v, out=out, problems=checked.problems)

    def park(self, th: ThreadState, v: policy.Verdict) -> None:
        """Wait without conceding. Returns when the verdict changes or the thread or case ends; the arbiter closes
        the case when every thread waits."""
        s, cfg = self.s, self.s.config
        self._log_round(th, "park", verdict=v)
        while not s.ended and th.alive and th.awaiting_us:
            with s.lock:
                current = policy.verdict(s, th)
            if current.kind == "accept" and self.arbiter.try_accept(th, current.reason):
                return
            if current.kind == "counter":
                return
            s.wait_change(cfg.worker_poll_s)

    # ---- the wire ----------------------------------------------------------------------------------

    def send(self, th: ThreadState, message: dict, kind: str, verdict=None, out=None, problems=()) -> None:
        s = self.s
        with s.lock:  # reserve this message's data IDs atomically: parallel threads must not cite the same one
            message = guard.reserve(s, th, message)
            if not self.arbiter.begin_send(th):
                return
        asked_at = th.received_at
        delivered, reply = True, None
        try:
            try:
                with s.gate(asked_at):
                    reply = s.table.send(th.supplier_id, message)
            except ArenaError as e:
                if e.status in (409, 422) and "round limit" in str(e.detail).lower():  # api.md: may come as 409
                    # "accept the standing term sheet or walk away": no message can go out - the policy decides
                    log.warning("%s/%s: round limit reached - no more messages", s.case["case_id"], th.supplier_id)
                    th.exhausted = True
                    return
                if e.status == 422:  # invalid message, nothing happened, no round used: repair once
                    log.warning("%s/%s: 422 %s - repairing", s.case["case_id"], th.supplier_id, e.detail)
                    message = guard.hard_repair(s, th, message)
                    try:
                        with s.gate(asked_at):
                            reply = s.table.send(th.supplier_id, message)
                    except ArenaError as e2:
                        if e2.status != 409:
                            raise
                        delivered, reply = False, s.table.missed(th.supplier_id, e2)
                elif e.status == 409:  # late, thread ended, round limit or case ended: read what happened
                    delivered, reply = False, s.table.missed(th.supplier_id, e)
                else:
                    raise
        finally:
            self.arbiter.end_send(th)
        if "result" in reply:
            s.finish(reply["result"], "the arena ended the case")
            self._log_round(th, kind, verdict=verdict, out=out, message=message, problems=problems, ended=True)
            return
        received = time.monotonic()
        if any(a.get("attachment_id") and "text" not in a for a in reply.get("attachments") or []):
            # one attachment GET can block for over a minute (archived v2, run 4: 76 s, past the 60 s rule) and a
            # deadline cannot interrupt a call in flight: documents arrive on their own thread, read from then on
            _DOCS.submit(self._fetch_later, th, reply)
        with s.lock:
            if delivered:
                th.events.append({**message, "from": "buyer"})
            th.events.append(reply)
            th.received_at = received
            th.slowest_round_s = max(th.slowest_round_s, received - asked_at)
            observe.rebuild(s, th)
            s.offers |= {ts["term_sheet_id"] for ts in reply.get("term_sheets") or [] if ts.get("term_sheet_id")}
        claims = observe.report_claims(s, th, message, reply) if delivered else {}
        self._log_round(th, kind, verdict=verdict, out=out, message=message, problems=problems, reply=reply,
                        delivered=delivered, claims=claims, round_s=round(received - asked_at, 1))  # fmt: skip
        s.notify()

    def _fetch_later(self, th: ThreadState, reply: dict) -> None:
        """The reply's documents, downloaded off the move's path; the thread is re-observed once they are in."""
        try:
            fetch_documents(self.s.table, reply)
        except Exception as e:  # a document we cannot read must never cost a round
            log.warning("%s/%s: attachment read failed: %s", self.s.case.get("case_id"), th.supplier_id, e)
            return
        with self.s.lock:
            observe.rebuild(self.s, th)
        self.s.notify()

    def _log_round(self, th: ThreadState, kind: str, verdict=None, out=None, message=None, reply=None, **extra):
        s = self.s
        sheets = [ts.get("terms") for ts in (message or {}).get("term_sheets") or []]
        self.agent.telemetry.round(
            case_id=s.case.get("case_id"),
            supplier_id=th.supplier_id,
            kind=kind,
            round=th.our_rounds,
            verdict=verdict.kind if verdict else None,
            reason=verdict.reason if verdict else None,
            stages=out.used if out else None,
            timings_ms=out.timings_ms if out else None,
            tactic=(
                {
                    "action": out.tactic.action,
                    "claims": out.tactic.claims,
                    "question": out.tactic.question,
                    "rationale": out.tactic.rationale,
                    "price": out.tactic.price,  # the decision agent: follow the planner or push for price
                    "package": out.tactic.package,  # its own package, priced by the planner within the plan's C
                    "plan": out.plan.rationale if out.plan else None,  # the planner's step and why
                }
                if out
                else None
            ),  # fmt: skip
            sheets=[{"terms": t, "c": round(s.cost(th.supplier_id, t))} for t in sheets if t],
            body=(message or {}).get("body"),
            reply_status=(reply or {}).get("status"),
            standing_c=None if th.c == float("inf") else round(th.c),
            c_open=None if s.c_open == float("inf") else round(s.c_open),
            c_alt=s.c_alt,
            language=observe.detect_language(str((reply or {}).get("body") or "")) if reply else None,
            **extra,
        )


# ---- entry points ---------------------------------------------------------------------------------------


OPENING_DOCS_S = 10.0  # the 60 s clock runs from the opening: a slow download must not eat it (one took 76 s)


def _fetch_all(table, threads: dict[str, list[dict]]) -> None:
    """The opening documents, within OPENING_DOCS_S: what has not arrived by then is skipped (the term sheets are
    structured; only the attached text is missing) - a late download keeps running but never delays a move."""
    messages = [m for events in threads.values() for m in events if m.get("attachments")]
    if not messages:
        return
    pool = ThreadPoolExecutor(max_workers=min(8, len(messages)), thread_name_prefix="opening-docs")
    futures = [pool.submit(fetch_documents, table, m) for m in messages]
    done, late = wait(futures, timeout=OPENING_DOCS_S)
    if late:
        log.warning("%d opening document(s) not in after %.0f s - playing without their text", len(late),
                    OPENING_DOCS_S)  # fmt: skip
    pool.shutdown(wait=False, cancel_futures=True)


def play_case(table, case_id: str, agent, deadline=None) -> dict:
    """Play one case to its end and return the arena's result. `deadline`: the wave's cap as time.time(), or a
    function returning it (an extension counts), or None (practice and sandbox have no cap)."""
    case, threads = table.open(case_id)
    if getattr(table, "done", None):  # resumed a case that had already ended
        return table.done
    opened = time.monotonic()
    resumed = any(m.get("from") == "buyer" for events in threads.values() for m in events)
    _fetch_all(table, threads)
    session = agent.open_case(case, threads, table, deadline, received_at=opened, resumed=resumed)
    arbiter = Arbiter(session, agent.telemetry)
    worker = ThreadWorker(agent, session, arbiter)
    pool = ThreadPoolExecutor(max_workers=max(1, len(session.threads)), thread_name_prefix="buyer-thread")
    futures = [pool.submit(worker.run, sid) for sid in session.threads]
    try:
        arbiter.loop(lambda: all(f.done() for f in futures))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    result = session.result or arbiter.result_or_none() or {"deal": False, "end_reason": session.end_reason}
    agent.telemetry.case(
        case_id=case_id, session_id=getattr(table, "session_id", None), end=session.end_reason,
        rounds={sid: t.our_rounds for sid, t in session.threads.items()},
        **{k: result.get(k) for k in ("deal", "supplier_id", "score", "c_open", "c_deal", "c_alt", "c_best")},
    )  # fmt: skip
    return result


def play_set(new_table, case_ids, new_agent, parallel: int = 8) -> dict:
    """Many cases at once (practice sweep). Same quota handling as the kit's play_all."""
    used_up = threading.Event()

    def one(case_id):
        if used_up.is_set():
            return case_id, ArenaError(429, "not started: the practice quota is used up")
        try:
            return case_id, play_case(new_table(), case_id, new_agent())
        except ArenaError as e:
            if quota_used_up(e):
                used_up.set()
                log.warning("practice quota used up: %s", e.detail)
            elif e.status == 403:
                log.warning("%s: %s", case_id, e.detail)
            else:
                log.exception("%s failed", case_id)
            return case_id, e
        except Exception as e:  # one broken case must not stop the others
            log.exception("%s failed", case_id)
            return case_id, e

    with ThreadPoolExecutor(max_workers=max(1, min(parallel, 10))) as pool:
        return dict(pool.map(one, case_ids))


def play_final(client, new_agent, waves: int = 5, parallel: int = 10, poll: float = 5.0) -> dict:
    """The Live Final: wait for each wave, play its cases in parallel within the wave's cap, stop after `waves`
    waves have closed. Same polling contract as the kit's play_waves; reads only our own cases and sessions."""
    from ...play import RemoteTable

    started, closed, caps = {}, set(), {}

    def one(case, deadline):
        try:
            return play_case(RemoteTable(client, case), case["case_id"], new_agent(), deadline)
        except Exception as e:
            log.exception("%s failed", case["case_id"])
            return e

    with ThreadPoolExecutor(max_workers=max(1, min(parallel, 10))) as pool:
        while len(closed) < waves or not all(f.done() for f in started.values()):
            try:
                cases = client.cases("final")
            except ArenaError as e:
                if e.status != 403:  # 403: no wave has started yet
                    raise
                cases = []
            for case in cases:
                if not case.get("open"):
                    if case["wave"] not in closed:
                        log.warning("wave %s closed (%d of %d)", case["wave"], len(closed) + 1, waves)
                    closed.add(case["wave"])
                    continue
                if case.get("seconds_left") is not None:
                    caps[case["wave"]] = time.time() + case["seconds_left"]
                if case["case_id"] not in started and case.get("seconds_left"):
                    if case["wave"] not in {c["wave"] for c in cases if c["case_id"] in started}:
                        log.warning("wave %s started: %s", case["wave"], case.get("theme"))
                    started[case["case_id"]] = pool.submit(one, case, functools.partial(caps.get, case["wave"]))
            time.sleep(poll)
    return {case_id: f.result() for case_id, f in started.items()}
