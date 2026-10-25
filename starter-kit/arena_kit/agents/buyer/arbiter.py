"""The arbiter (owner: Jeddou): the only component that ends a case.

Thread workers negotiate in parallel; every irreversible act - accept, walk away - goes through here, under the
case lock, after the acceptability invariant is checked once more. The arbiter's own loop watches the case as a
whole: the wave's cap (close, last call), threads that all ended, and the walk-away condition.
"""

import logging
import time

from ...client import ArenaError
from . import guard, policy
from .state import CaseSession, ThreadState
from .telemetry import Telemetry

log = logging.getLogger("arena")


class Arbiter:
    def __init__(self, session: CaseSession, telemetry: Telemetry | None = None):
        self.s = session
        self.telemetry = telemetry or Telemetry("off")

    # ---- sends -------------------------------------------------------------------------------------

    def begin_send(self, th: ThreadState) -> bool:
        """Mark a POST as in flight; False if the case has ended (the worker must not send)."""
        with self.s.lock:
            if self.s.ended or not th.alive:
                return False
            th.in_flight = True
            return True

    def end_send(self, th: ThreadState) -> None:
        with self.s.lock:
            th.in_flight = False
        self.s.notify()

    # ---- closing -----------------------------------------------------------------------------------

    def try_accept(self, th: ThreadState, reason: str) -> bool:
        """Accept this thread's standing sheet if it is still acceptable, idle, and no other acceptable sheet
        on the table is cheaper. True if the case ended with this accept."""
        s = self.s
        with s.lock:
            if s.ended or th.in_flight or not guard.accept_ok(s, th):
                return False
            best = policy.best_acceptable(s, idle_only=False)
            if best is not None and best is not th and best.c < th.c:
                log.info("%s: accept of %s deferred - %s is cheaper", s.case["case_id"], th.supplier_id,
                         best.supplier_id)  # fmt: skip
                return False
            message = {"action": {"type": "accept", "term_sheet_id": th.standing_id}}
            return self._close(th, message, f"accept: {reason}")

    def try_walk(self, reason: str) -> bool:
        s = self.s
        with s.lock:
            if s.ended:
                return False
            target = next((t for t in s.threads.values() if t.alive), next(iter(s.threads.values())))
            return self._close(target, {"action": {"type": "walk_away"}}, f"walk: {reason}")

    def _close(self, th: ThreadState, message: dict, reason: str) -> bool:
        """Send the closing action (caller holds the lock). A 409 means the arena already ended the case."""
        s = self.s
        try:
            result = s.table.close(th.supplier_id, message)
        except ArenaError as e:
            if e.status == 409:
                result = self.result_or_none() or {"deal": False, "end_reason": str(e.detail)}
                s.finish(result, f"already ended ({e.detail})")
                return True
            log.warning("%s: closing action refused (%s): %s", s.case["case_id"], e.status, e.detail)
            if message["action"].get("term_sheet_id"):  # never retry it: the thread counters or waits instead
                th.refused.add(message["action"]["term_sheet_id"])
            if "has ended" in str(e.detail):  # the supplier walked or the thread closed: stop counting on it
                th.alive = False
            return False
        log.info("%s: %s (C %s)", s.case["case_id"], reason, None if th.c == float("inf") else round(th.c))
        self.telemetry.round(
            case_id=s.case.get("case_id"), supplier_id=th.supplier_id, kind="close", reason=reason,
            action=message["action"]["type"], c=th.c, c_open=s.c_open, c_alt=s.c_alt,
            since_reply_s=round(time.monotonic() - th.received_at, 1),  # how long the supplier's offer waited for us
        )  # fmt: skip
        s.finish(result, reason)
        return True

    def result_or_none(self) -> dict | None:
        try:
            return self.s.table.result()
        except Exception as e:
            log.warning("could not read the session result: %s", e)
            return None

    # ---- the case loop -----------------------------------------------------------------------------

    def step(self) -> None:
        s, cfg = self.s, self.s.config
        with s.lock:
            if s.ended:
                return
            alive = s.alive()
            if not alive:
                if not any(t.in_flight for t in s.threads.values()):
                    result = self.result_or_none()
                    if result and result.get("ended"):
                        s.finish(result, "every thread ended")
                    else:  # threads we gave up on are still open on the arena: close the case ourselves
                        self._close_abandoned()
                return
            left = s.seconds_left()
            if left is not None:
                margin = s.close_margin()
                on_table = policy.best_acceptable(s, idle_only=False)
                if left <= margin:
                    best = policy.best_acceptable(s, idle_only=True)
                    pending = best is not None and any(
                        t.in_flight and t.c < best.c * (1 + cfg.rival_in_flight_margin) for t in alive
                    )
                    if best is not None and (not pending or left <= cfg.last_call_s):
                        self.try_accept(best, f"case cap in {left:.0f}s")
                        return
                    if best is None and on_table is None and left <= cfg.last_call_s:
                        self.try_walk(f"case cap in {left:.0f}s and nothing acceptable")
                        return
            reason = policy.walk_reason(s)
            if not reason and self._nobody_negotiating(alive):
                best = policy.best_acceptable(s, idle_only=True)
                if best is not None:  # every thread waits on another: take the best sheet on the table
                    self.try_accept(best, "no thread is negotiating any more")
                    return
        if reason:
            self.try_walk(reason)

    def _close_abandoned(self) -> None:
        """Every thread was given up locally (repeated errors) but the arena still has them open: accept the
        cheapest standing sheet that passes the invariant, else walk away. Never leave a session open."""
        s = self.s
        open_threads = [t for t in s.threads.values() if t.status not in ("walked_away", "no_bid")]
        ok = [t for t in open_threads if policy.standing_ok(s, t)]
        if ok:
            best = min(ok, key=lambda t: t.c)
            message = {"action": {"type": "accept", "term_sheet_id": best.standing_id}}
            if self._close(best, message, "accept: every thread was given up, best acceptable sheet"):
                return
        self.try_walk("every thread was given up and nothing acceptable is on the table")

    def _nobody_negotiating(self, alive: list[ThreadState]) -> bool:
        """Every live thread is idle, awaits our answer, and its verdict is to wait - a stand-off."""
        s = self.s
        if any(t.in_flight or not t.awaiting_us for t in alive):
            return False
        return all(policy.verdict(s, t).kind in ("park", "dead") for t in alive)

    def loop(self, workers_done, poll_s: float = 1.0) -> None:
        """Run until the case ends. When every worker has stopped and the case is still open (all threads
        parked or failing), close it: the best acceptable sheet, else walk away - never leave a session open."""
        s = self.s
        while not s.ended:
            s.wait_change(poll_s)
            self.step()
            if not s.ended and workers_done():
                self.step()
                if s.ended:
                    break
                best = policy.best_acceptable(s, idle_only=True)
                if not (best and self.try_accept(best, "workers stopped")):
                    self.try_walk("workers stopped with nothing acceptable")
                if not s.ended:  # the arena refused both: record what it says and stop
                    s.finish(self.result_or_none() or {"deal": False}, "workers stopped")
            time.sleep(0)  # yield
