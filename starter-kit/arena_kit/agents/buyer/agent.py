"""The buyer agent's orchestrator (owner: Jeddou).

Plain code with LLM roles at fixed points (docs/team/DECISIONS.md): tools compute numbers, roles read and
write, the orchestrator decides what is permitted and when to close.

  BuyerAgent.open_case   prepare a case: tools.prepare, profiler, our supplier estimates, C_open, thread states
  runner.play_case       the live path: one worker per thread + an arbiter (`--agent buyer` uses this)
  BuyerAgent.decide      the kit's single-decision interface (`play()`, tests): same policy, one message

Every stage has a deterministic fallback; no exception, slow model or missing data costs a case.
"""

import functools
import logging
import os
import threading
import time

from ...docs import standard_terms
from ...play import live_threads
from ..llm import llm_from_env
from . import guard, observe, policy, roles
from .config import OrchestratorConfig
from .contracts import CaseContext
from .pipeline import RolePipeline, keepalive_email
from .roles import LLMWriter, PassthroughProfiler, RuleAnalyst, RuleStrategist
from .state import CaseSession, NoGate, SendGate, ThreadState
from .telemetry import Telemetry
from .tools import DataTools

log = logging.getLogger("arena")


class LockedPackage:
    """The data package behind one lock. `DataPackage` shares a single sqlite connection; under ten parallel
    cases it returned `None` from `fetchone()` (Saed's bench, 17:15). Every method call is serialised; plain
    attributes (`facts`, `root`, `release`) pass through. Queries take milliseconds, so the lock costs nothing."""

    def __init__(self, package):
        self._package = package
        self._lock = threading.RLock()

    def __getattr__(self, name):
        value = getattr(self._package, name)
        if not callable(value):
            return value

        @functools.wraps(value)
        def locked(*args, **kwargs):
            with self._lock:
                return value(*args, **kwargs)

        return locked


class BuyerAgent:
    def __init__(self, tools, profiler, analyst, strategist, writer, package=None, config=None, telemetry=None):
        self.tools, self.profiler, self.package = tools, profiler or PassthroughProfiler(), package
        self.config = config or OrchestratorConfig.from_env()
        self.telemetry = telemetry or Telemetry()
        self.pipeline = RolePipeline(tools, analyst or RuleAnalyst(), strategist or RuleStrategist(), writer)
        self._prepared: tuple[str, CaseContext, dict] | None = None  # cache for decide(): one case per agent

    # ---- preparing a case ----------------------------------------------------------------------------

    def prepare(self, case: dict, threads: dict[str, list[dict]]) -> tuple[CaseContext, dict]:
        """The tools' case context (dossiers refined by the profiler) and our estimate of each supplier."""
        try:
            ctx = self.tools.prepare(case, threads)
        except Exception:
            log.exception("%s: tools.prepare failed - negotiating without bands, claims or dossiers", case["case_id"])
            ctx = CaseContext(case, {}, {}, {}, policy.alternative_cost(case))
        dossiers = {}
        for sid, dossier in ctx.dossiers.items():
            try:
                dossiers[sid] = self.profiler.refine(case, dossier)
            except Exception:
                log.exception("%s: profiler failed for %s - dossier unchanged", case["case_id"], sid)
                dossiers[sid] = dossier
        ctx = CaseContext(ctx.case, dossiers, ctx.bands, ctx.claims, ctx.c_alternative)
        estimates = {sid: self._estimate(ctx, sid, events) for sid, events in threads.items()}
        return ctx, estimates

    def _estimate(self, ctx: CaseContext, sid: str, events: list[dict]) -> dict | None:
        """The same supplier estimate the bands used (opening terms, standard terms), so our C and the band's
        C are on one scale. None without data: C then leaves the relationship charge out (logged)."""
        if not hasattr(self.tools, "supplier"):
            return None
        quote = next((m for m in events if m.get("from") != "buyer" and m.get("term_sheets")), None)
        if quote is None:
            return None
        try:
            return self.tools.supplier(ctx, sid, quote["term_sheets"][0]["terms"], standard_terms(events))
        except Exception:
            log.exception("%s: supplier estimate failed", sid)
            return None

    def _lookup(self, ref: str) -> bool:
        try:
            return bool(self.package and self.package.lookup(ref))
        except Exception:
            return False

    def open_case(self, case, threads, table=None, deadline=None, received_at=None, resumed=False) -> CaseSession:
        if self._prepared and self._prepared[0] == case["case_id"]:
            ctx, estimates = self._prepared[1], self._prepared[2]
        else:
            ctx, estimates = self.prepare(case, threads)
            self._prepared = (case["case_id"], ctx, estimates)
        now = received_at if received_at is not None else time.monotonic()
        states = {sid: ThreadState(sid, list(events), now, rule_only_once=resumed) for sid, events in threads.items()}
        session = CaseSession(
            case=case, ctx=ctx, threads=states, config=self.config, table=table, estimates=estimates,
            deadline=deadline, gate=SendGate() if self.config.serialised_session else NoGate(),
        )  # fmt: skip
        session.set_lookup(self._lookup)
        observe.rebuild_case(session)
        session.c_open = policy.opening_cost(session)
        if case.get("buyer", {}).get("category_goal") in ("stabilise", "secure_supply"):
            blind = [sid for sid, est in estimates.items() if est is None]
            if blind:
                log.warning("%s: no supplier estimate for %s - C leaves the relationship charge out",
                            case["case_id"], blind)  # fmt: skip
        return session

    # ---- the kit's single-decision interface ---------------------------------------------------------

    def decide(self, case: dict, threads: dict[str, list[dict]]) -> dict:
        """One message for the kit's `play()` loop: the same verdicts, roles and guard as the live runner, for
        the most promising thread that awaits us. (The live runner answers every thread; this cannot.)"""
        t0 = time.monotonic()
        try:
            decision = self._decide(case, threads)
        except Exception:  # never lose a case to a bug
            log.exception("%s: decision failed - safe fallback", case["case_id"])
            decision = self._safe_fallback(case, threads)
        log.info("%s: decided in %.1fs: %s", case["case_id"], time.monotonic() - t0, _summary(decision))
        return decision

    def _decide(self, case: dict, threads: dict[str, list[dict]]) -> dict:
        s = self.open_case(case, threads)
        waiting = [t for t in s.alive() if t.awaiting_us]
        verdicts = {t.supplier_id: policy.verdict(s, t) for t in waiting}
        accept = [t for t in waiting if verdicts[t.supplier_id].kind == "accept" and guard.accept_ok(s, t)]
        if accept:
            best = min(accept, key=lambda t: t.c)
            return _accept(best)
        counters = sorted((t for t in waiting if verdicts[t.supplier_id].kind == "counter"), key=lambda t: t.c)
        for th in counters:
            v = verdicts[th.supplier_id]
            out = self.pipeline.run(s, th, v, time.monotonic() + self.config.move_budget_s)
            if out.tactic.action == "accept" and guard.accept_ok(s, th):
                return _accept(th)
            if out.plan is not None and out.email is not None:
                checked = guard.build_message(s, th, out.plan.term_sheets, out.email) or guard.build_message(
                    s, th, out.plan.term_sheets, self.pipeline.rule_email(s, th, out.tactic, out.plan)
                )
                if checked is not None:
                    return {"supplier_id": th.supplier_id, "message": checked.message}
            if v.accept_permitted and guard.accept_ok(s, th):
                return _accept(th)
        if policy.walk_reason(s):
            return _walk(waiting or s.alive() or list(s.threads.values()))
        best = policy.best_acceptable(s)
        if best is not None:
            return _accept(best)
        parked = [t for t in waiting if t.moves_left(case) >= 1]
        if parked:
            return _ask(parked[0].supplier_id, keepalive_email(s, parked[0]))
        return _walk(s.alive() or list(s.threads.values()))

    def _safe_fallback(self, case: dict, threads: dict[str, list[dict]]) -> dict:
        """After a bug: accept the cheapest acceptable sheet, else answer the first live thread with a question,
        else walk. Never a price we did not compute."""
        live = live_threads(threads) or list(threads)
        try:
            s = self.open_case(case, threads)
            best = policy.best_acceptable(s)
            if best is not None and best.our_rounds > 0:
                return _accept(best)
            th = s.threads[live[0]]
            email = keepalive_email(s, th)
        except Exception:
            log.exception("%s: fallback failed too - walking away", case["case_id"])
            return {"supplier_id": live[0], "message": {"action": {"type": "walk_away"}}}
        return _ask(live[0], email)


def _accept(th: ThreadState) -> dict:
    return {"supplier_id": th.supplier_id, "message": {"action": {"type": "accept", "term_sheet_id": th.standing_id}}}


def _ask(supplier_id: str, email) -> dict:
    """A question without a term sheet: an answer within 60 s that concedes nothing."""
    return {"supplier_id": supplier_id,
            "message": {"subject": email.subject, "body": email.body, "term_sheets": [], "refs": []}}  # fmt: skip


def _walk(threads: list[ThreadState]) -> dict:
    return {"supplier_id": threads[0].supplier_id, "message": {"action": {"type": "walk_away"}}}


def _summary(decision: dict) -> str:
    msg = decision["message"]
    if msg.get("action"):
        return f"{decision['supplier_id']} {msg['action']['type']}"
    prices = [ts["terms"].get("unit_price") for ts in msg.get("term_sheets", [])]
    return f"{decision['supplier_id']} send {prices}"


def buyer_factory(package=None):
    """`--agent buyer`: one BuyerAgent per case, sharing the data package (behind a lock), the LLM client and
    the telemetry. Without LLM_MODEL every role runs on its rule twin (no LLM calls at all). The strategist is
    Saed's LLMStrategist when the roles package provides it, else the rule strategist."""
    locked = LockedPackage(package) if package is not None else None
    complete = llm_from_env() if os.environ.get("LLM_MODEL") else None
    tools = DataTools(locked)
    config, telemetry = OrchestratorConfig.from_env(), Telemetry()
    llm_strategist = getattr(roles, "LLMStrategist", None)

    def new_agent() -> BuyerAgent:
        strategist = llm_strategist(complete) if llm_strategist else RuleStrategist()
        return BuyerAgent(
            tools, PassthroughProfiler(), RuleAnalyst(), strategist, LLMWriter(complete), locked, config, telemetry
        )

    return new_agent


__all__ = ["BuyerAgent", "LockedPackage", "buyer_factory"]
