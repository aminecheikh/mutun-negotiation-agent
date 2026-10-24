"""The v2 runner: one worker per supplier thread, one coordinator per case.

Every thread is negotiated on its own: its worker answers its supplier within the move budget, whatever the other
threads do. Irreversible acts go through the coordinator under the case lock:
  accept   the cheapest acceptable standing sheet among the idle threads (never a dearer sheet over a cheaper
           one; never holding a thread back for a rival) - a refused accept re-reads the thread from the arena
           and retries once with its current sheet
  walk     only when every thread has ended or stopped and nothing on the table is signable
  cap      before a Live Final wave's cap: the best acceptable sheet, else walk (no deal = our alternative)

    play_case(table, case_id, agent, deadline)    one case (practice, sandbox or a final case)
    play_set(new_table, case_ids, new_agent)      many cases in parallel
    play_final(client, new_agent, waves)          the Live Final in waves
"""

import functools
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ...client import ArenaError
from ...play import ENDED, fetch_documents, quota_used_up
from . import facts as F
from . import guard
from .brain import Brain, Move
from .config import V2Config
from .trail import Trail

log = logging.getLogger("arena")
MAX_ERRORS = 3  # a thread that keeps failing is given up (the coordinator still closes the case)


@dataclass
class ThreadRun:
    sid: str
    events: list[dict]
    received_at: float
    in_flight: bool = False
    done: bool = False  # we stopped negotiating with this supplier (walk away from it, or it ended)
    errors: int = 0
    slowest_s: float = 0.0
    decided_for: int = -1  # len(events) when we last decided: never decide twice on the same reply
    accept_retries: dict = None  # len(events) -> failed accepts on that reply (re-decide at most twice)

    @property
    def awaiting_us(self) -> bool:
        return bool(self.events) and self.events[-1].get("from") != "buyer"

    @property
    def status(self) -> str:
        last = F.latest_supplier(self.events) or {}
        return str(last.get("status") or "open")

    @property
    def live(self) -> bool:
        return not self.done and self.status not in ENDED


class V2Agent:
    """What `--agent buyer` plays with AGENTIC_STRUCTURE=1: the brain (tools + model) and the trail."""

    def __init__(self, brain: Brain, config: V2Config | None = None, trail: Trail | None = None):
        self.brain, self.config, self.trail = brain, config or brain.config, trail or Trail("off")


class CaseRun:
    def __init__(self, agent: V2Agent, table, case: dict, threads: dict[str, list[dict]], deadline=None):
        self.agent, self.table, self.case, self.deadline = agent, table, case, deadline
        self.cfg = agent.config
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.result: dict | None = None
        self.end_reason: str | None = None
        now = time.monotonic()
        self.runs = {sid: ThreadRun(sid, list(events), now) for sid, events in threads.items()}
        self.state = agent.brain.open_case(case, {s: r.events for s, r in self.runs.items()})

    # ---- lifecycle ----------------------------------------------------------------------------------------

    @property
    def ended(self) -> bool:
        return self.result is not None

    def finish(self, result: dict | None, reason: str) -> None:
        with self.lock:
            if self.result is None:
                self.result, self.end_reason = result or {"deal": False}, reason
                log.info("%s: case over - %s", self.case.get("case_id"), reason)
            self.cond.notify_all()

    def notify(self) -> None:
        with self.cond:
            self.cond.notify_all()

    def wait(self, seconds: float) -> None:
        with self.cond:
            if not self.ended:
                self.cond.wait(timeout=seconds)

    def play(self) -> dict:
        pool = ThreadPoolExecutor(max_workers=max(1, len(self.runs)), thread_name_prefix="v2-thread")
        futures = [pool.submit(self.worker, sid) for sid in self.runs]
        try:
            while not self.ended:
                self.wait(self.cfg.poll_s)
                self.step(workers_done=all(f.done() for f in futures))
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return self.result or {"deal": False}

    # ---- the worker of one thread ---------------------------------------------------------------------------

    def worker(self, sid: str) -> None:
        th = self.runs[sid]
        while not self.ended and th.live and th.errors < MAX_ERRORS:
            if not th.awaiting_us or th.in_flight or th.decided_for == len(th.events):
                self.wait(self.cfg.poll_s)
                continue
            try:
                with self.lock:
                    snapshot = {s: list(r.events) for s, r in self.runs.items()}
                    th.decided_for = len(th.events)
                move = self.agent.brain.decide(self.state, sid, snapshot, th.received_at)
                self._log_move(th, move)
                self.execute(th, move)
            except ArenaError as e:
                th.errors += 1
                th.decided_for = -1
                log.warning("%s/%s: arena error %s: %s", self.case.get("case_id"), sid, e.status, e.detail)
                time.sleep(0.5)
            except Exception:
                th.errors += 1
                th.decided_for = -1
                log.exception("%s/%s: round failed (%d errors)", self.case.get("case_id"), sid, th.errors)
        if th.errors >= MAX_ERRORS:
            with self.lock:
                th.done = True
            log.error("%s/%s: giving up on this thread after %d errors", self.case.get("case_id"), sid, th.errors)
        self.notify()

    def execute(self, th: ThreadRun, move: Move) -> None:
        if self.ended:
            return
        if move.kind == "accept":
            if not self.accept_best(prefer=th.sid, reason=move.reason):
                retries = th.accept_retries if th.accept_retries is not None else {}
                th.accept_retries = retries
                retries[len(th.events)] = retries.get(len(th.events), 0) + 1
                if retries[len(th.events)] <= 2:
                    th.decided_for = -1  # decide again on the same reply, now knowing the refusal
            return
        if move.kind == "stop":
            with self.lock:
                th.done = True
            self.notify()
            return
        if move.kind == "send" and move.message is not None:
            self.send(th, move.message)

    # ---- the wire ---------------------------------------------------------------------------------------------

    def send(self, th: ThreadRun, message: dict) -> None:
        with self.lock:
            if self.ended or not th.live:
                return
            message = guard.reserve(self.state, message, list(message.get("refs") or ()))
            th.in_flight = True
        asked_at = time.monotonic()
        reply, delivered = None, True
        try:
            try:
                reply = self.table.send(th.sid, message)
            except ArenaError as e:
                if e.status == 422:  # invalid, nothing happened, no round used: repair once
                    log.warning("%s/%s: 422 %s - repairing", self.case.get("case_id"), th.sid, e.detail)
                    message = self._repair(th, message)
                    try:
                        reply = self.table.send(th.sid, message)
                    except ArenaError as e2:
                        if e2.status != 409:
                            raise
                        delivered, reply = False, None
                elif e.status == 409:  # late, thread ended, round limit or case ended
                    delivered, reply = False, None
                else:
                    raise
        finally:
            with self.lock:
                th.in_flight = False
        if reply is not None and "result" in reply:
            self.finish(reply["result"], "the arena ended the case")
            return
        if reply is None:
            self._resync(th, f"our message to {th.sid} was not taken")
            return
        reply = fetch_documents(self.table, reply)
        now = time.monotonic()
        with self.lock:
            if delivered:
                th.events.append({**message, "from": "buyer"})
            th.events.append(reply)
            th.received_at = now
            th.slowest_s = max(th.slowest_s, now - asked_at)
        self.notify()

    def _repair(self, th: ThreadRun, message: dict) -> dict:
        """After a 422: re-check every sheet with snapping forced; with no sheet left it goes out as a question."""
        with self.lock:
            events = list(th.events)
        f = F.thread_facts(self.state, th.sid, events)
        sheets = [ts.get("terms", ts) for ts in message.get("term_sheets") or []]
        snapped = [guard.normalise(self.state, s, f) for s in sheets]
        kept = guard.check_counter(self.state, f, [s for s in snapped if s]).sheets
        return {**message, "term_sheets": [{"terms": s} for s in kept], "refs": list(message.get("refs") or [])[:20]}

    def _resync(self, th: ThreadRun, why: str) -> None:
        """Read the thread (and the case) from the arena after a 409 or a refusal: the arena is the record."""
        log.info("%s/%s: %s - re-reading the thread", self.case.get("case_id"), th.sid, why)
        try:
            client, session = getattr(self.table, "client", None), getattr(self.table, "session_id", None)
            if client is not None and session:
                state = client.session(session)
                if state.get("status") == "ended":
                    self.finish(state.get("result") or {"deal": False, "end_reason": state.get("end_reason")}, why)
                    return
                messages = client.thread(session, th.sid).get("messages") or []
            elif hasattr(self.table, "thread"):
                messages = self.table.thread(th.sid)
            else:
                messages = None
        except ArenaError as e:
            log.warning("%s/%s: could not re-read the thread: %s", self.case.get("case_id"), th.sid, e)
            return
        if not messages:
            return
        for m in messages:
            fetch_documents(self.table, m)
        with self.lock:
            th.events = list(messages)
            th.received_at = time.monotonic()
            th.decided_for = -1
        self.notify()

    # ---- closing ----------------------------------------------------------------------------------------------

    def _facts(self, th: ThreadRun) -> F.ThreadFacts:
        return F.thread_facts(self.state, th.sid, list(th.events))

    def best_acceptable(self, idle_only: bool = True) -> tuple[ThreadRun, F.ThreadFacts] | None:
        best = None
        for th in self.runs.values():
            if th.status in ENDED or (idle_only and th.in_flight):
                continue
            f = self._facts(th)
            if guard.closable(self.state, f) and (best is None or f.standing.c < best[1].standing.c):
                best = (th, f)
        return best

    def accept_best(self, prefer: str | None, reason: str) -> bool:
        """Accept the cheapest acceptable standing sheet among the idle threads (ties go to `prefer`)."""
        with self.lock:
            if self.ended:
                return True
            pick = self.best_acceptable(idle_only=True)
            if pick is None:
                return False
            th, f = pick
            if prefer and th.sid != prefer:
                mine = self._facts(self.runs[prefer])
                if guard.closable(self.state, mine) and mine.standing.c <= f.standing.c:
                    th, f = self.runs[prefer], mine
                else:
                    reason = f"{reason}; {th.sid}'s acceptable sheet is cheaper ({f.standing.c:,.0f})"
            return self._close_accept(th, f, reason)

    def _close_accept(self, th: ThreadRun, f: F.ThreadFacts, reason: str, retry: bool = True) -> bool:
        message = {"action": {"type": "accept", "term_sheet_id": f.standing.id}}
        try:
            result = self.table.close(th.sid, message)
        except ArenaError as e:
            if e.status == 409:
                self._resync(th, f"accept answered 409 ({e.detail})")
                if not self.ended:
                    self.finish(self._result_or_none() or {"deal": False, "end_reason": str(e.detail)}, "already ended")
                return True
            log.warning("%s/%s: accept refused (%s): %s", self.case.get("case_id"), th.sid, e.status, e.detail)
            self.state.refused.add(f.standing.id)
            if retry:  # a stale ID (the supplier answered a timeout repeat meanwhile): read the thread, try once more
                self._resync(th, "accept refused")
                fresh = self._facts(th)
                if guard.closable(self.state, fresh) and fresh.standing.id != f.standing.id:
                    return self._close_accept(th, fresh, f"{reason} (after re-reading the thread)", retry=False)
            return False
        self.agent.trail.round(case_id=self.case.get("case_id"), supplier_id=th.sid, kind="accept", reason=reason,
                               c=f.standing.c, c_open=self.state.c_open, c_alt=self.state.c_alt)  # fmt: skip
        self.finish(result if isinstance(result, dict) else {"deal": True}, f"accept: {reason}")
        return True

    def walk(self, reason: str) -> None:
        with self.lock:
            if self.ended:
                return
            target = next((t for t in self.runs.values() if t.status not in ENDED), next(iter(self.runs.values())))
            try:
                result = self.table.close(target.sid, {"action": {"type": "walk_away"}})
            except ArenaError as e:
                log.warning("%s: walk away refused (%s): %s", self.case.get("case_id"), e.status, e.detail)
                result = self._result_or_none() or {"deal": False, "end_reason": str(e.detail)}
            self.agent.trail.round(case_id=self.case.get("case_id"), supplier_id=target.sid, kind="walk", reason=reason)
            self.finish(result if isinstance(result, dict) else {"deal": False}, f"walk: {reason}")

    def _result_or_none(self) -> dict | None:
        try:
            return self.table.result()
        except Exception as e:
            log.warning("could not read the session result: %s", e)
            return None

    def seconds_left(self) -> float | None:
        cap = self.deadline() if callable(self.deadline) else self.deadline
        return None if cap is None else cap - time.time()

    def step(self, workers_done: bool = False) -> None:
        """The coordinator: the wave's cap, and a case where no thread is negotiating any more."""
        if self.ended:
            return
        left = self.seconds_left()
        if left is not None:
            slowest = max((r.slowest_s for r in self.runs.values()), default=0.0)
            if left <= max(self.cfg.close_margin_s, slowest + self.cfg.round_margin_s):
                if self.accept_best(prefer=None, reason=f"the wave's cap is in {left:.0f}s"):
                    return
                if left <= self.cfg.last_call_s:
                    self.walk(f"the wave's cap is in {left:.0f}s and nothing is signable")
                    return
        with self.lock:
            negotiating = [r for r in self.runs.values() if r.live and r.errors < MAX_ERRORS]
            in_flight = any(r.in_flight for r in self.runs.values())
        if (not negotiating or workers_done) and not in_flight:
            if not self.accept_best(prefer=None, reason="no supplier is negotiating any more"):
                self.walk("no supplier is negotiating any more and nothing on the table is signable")

    def _log_move(self, th: ThreadRun, move: Move) -> None:
        msg = move.message or {}
        self.agent.trail.round(
            case_id=self.case.get("case_id"), supplier_id=th.sid, kind=move.kind, reason=move.reason,
            round=sum(m.get("from") == "buyer" for m in th.events), status=th.status, detail=move.detail,
            body=msg.get("body"), refs=msg.get("refs"),
            decision_s=round(time.monotonic() - th.received_at, 2),
        )  # fmt: skip


# ---- entry points -----------------------------------------------------------------------------------------------


def play_case(table, case_id: str, agent: V2Agent, deadline=None) -> dict:
    case, threads = table.open(case_id)
    if getattr(table, "done", None):  # resumed a case that had already ended
        return table.done
    for events in threads.values():
        for m in events:
            fetch_documents(table, m)
    run = CaseRun(agent, table, case, threads, deadline)
    result = run.play()
    agent.trail.case(
        case_id=case_id, session_id=getattr(table, "session_id", None), end=run.end_reason,
        rounds={sid: sum(m.get("from") == "buyer" for m in r.events) for sid, r in run.runs.items()},
        **{k: result.get(k) for k in ("deal", "supplier_id", "score", "c_open", "c_deal", "c_alt", "c_best")},
    )  # fmt: skip
    return result


def play_set(new_table, case_ids, new_agent, parallel: int = 8) -> dict:
    """Many cases at once (practice sweep), the kit's play_all semantics."""
    used_up = threading.Event()

    def one(case_id):
        if used_up.is_set():
            return case_id, ArenaError(429, "not started: the practice quota is used up")
        try:
            return case_id, play_case(new_table(), case_id, new_agent())
        except ArenaError as e:
            if quota_used_up(e):
                used_up.set()
            log.warning("%s: %s", case_id, e.detail)
            return case_id, e
        except Exception as e:  # one broken case must not stop the others
            log.exception("%s failed", case_id)
            return case_id, e

    with ThreadPoolExecutor(max_workers=max(1, min(parallel, 10))) as pool:
        return dict(pool.map(one, case_ids))


def play_final(client, new_agent, waves: int = 5, parallel: int = 10, poll: float = 5.0) -> dict:
    """The Live Final: wait for each wave, play its cases in parallel within the wave's cap, stop after `waves`
    waves have closed. Reads only our own cases and sessions - never the live feed."""
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
                    closed.add(case["wave"])
                    continue
                if case.get("seconds_left") is not None:
                    caps[case["wave"]] = time.time() + case["seconds_left"]
                if case["case_id"] not in started and case.get("seconds_left"):
                    started[case["case_id"]] = pool.submit(one, case, functools.partial(caps.get, case["wave"]))
            time.sleep(poll)
    return {case_id: f.result() for case_id, f in started.items()}


# ---- the factory -------------------------------------------------------------------------------------------------


def buyer_v2_factory(package=None, config: V2Config | None = None):
    """`--agent buyer` with AGENTIC_STRUCTURE=1: one V2Agent per case. Shared across cases: the data package (behind
    the tools' lock), the model client and its slot pools, the trail. Without LLM_MODEL every decision runs on the
    deterministic rules (fast paths, offer engine, templates) - the same hard limits, no model calls."""
    from ..buyer.tools import DataTools
    from ..buyer.tools.safe_package import thread_safe
    from .llm import LLM, anthropic_transport

    config = config or V2Config.from_env()
    safe = thread_safe(package)
    trail = Trail(config.log_dir or None)
    transport = anthropic_transport(max_timeout_s=config.move_budget_s)
    llm = LLM(transport, config, recorder=trail.llm_call) if transport is not None else None
    tools = DataTools(safe)

    def new_agent() -> V2Agent:
        return V2Agent(Brain(tools, llm, config, safe, trail), config, trail)

    return new_agent
