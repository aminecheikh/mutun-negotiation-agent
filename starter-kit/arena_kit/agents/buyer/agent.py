"""The buyer agent's orchestrator (owner: Jeddou).

Plain code with LLM roles at fixed points (docs/team/DECISIONS.md): tools compute numbers, roles read and
write, the orchestrator decides what is permitted and when to close.

  BuyerAgent.open_case   prepare a case: tools.prepare, profiler, our supplier estimates, C_open, thread states
  runner.play_case       the live path: one worker per thread + an arbiter (`--agent buyer` uses this)
  BuyerAgent.decide      the kit's single-decision interface (`play()`, tests): same policy, one message

Every stage has a deterministic fallback; no exception, slow model or missing data costs a case.
"""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from ...docs import standard_terms
from ...play import live_threads
from ..llm import llm_from_env
from . import guard, observe, policy
from .config import OrchestratorConfig
from .contracts import CaseContext
from .pipeline import RolePipeline, keepalive_email
from .roles import LLMProfiler, LLMStrategist, LLMWriter, PassthroughProfiler, RuleAnalyst, RuleStrategist
from .state import CaseSession, NoGate, SendGate, ThreadState
from .telemetry import Telemetry
from .tools import DataTools
from .tools.safe_package import thread_safe

log = logging.getLogger("arena")


class BuyerAgent:
    def __init__(self, tools, profiler, analyst, strategist, writer, package=None, config=None, telemetry=None):
        self.tools, self.profiler, self.package = tools, profiler or PassthroughProfiler(), package
        self.config = config or OrchestratorConfig.from_env()
        self.telemetry = telemetry or Telemetry()
        self.pipeline = RolePipeline(tools, analyst or RuleAnalyst(), strategist or RuleStrategist(), writer)
        self._prepared: tuple[str, CaseContext] | None = None  # prepared once per case (decide() calls open_case often)

    # ---- preparing a case ----------------------------------------------------------------------------

    def prepare(self, case: dict, threads: dict[str, list[dict]]) -> CaseContext:
        """The tools' case context with the dossiers refined by the profiler (every other field kept as is)."""
        try:
            ctx = self.tools.prepare(case, threads)
        except Exception:
            log.exception("%s: tools.prepare failed - negotiating without bands, claims or dossiers", case["case_id"])
            ctx = CaseContext(case, {}, {}, {}, policy.alternative_cost(case))
        return replace(ctx, dossiers=self._refine(case, ctx.dossiers))

    def _refine(self, case: dict, dossiers: dict) -> dict:
        """The profiler on every supplier, in parallel (an RFQ with new suppliers may call the model once per
        supplier; the clock is already running). A failure keeps the tools' dossier."""

        def one(item):
            sid, dossier = item
            try:
                return sid, self.profiler.refine(case, dossier)
            except Exception:
                log.exception("%s: profiler failed for %s - dossier unchanged", case["case_id"], sid)
                return sid, dossier

        if len(dossiers) <= 1:
            return dict(map(one, dossiers.items()))
        with ThreadPoolExecutor(max_workers=len(dossiers), thread_name_prefix="buyer-profiler") as pool:
            return dict(pool.map(one, dossiers.items()))

    def estimator(self, case: dict, ctx: CaseContext):
        """(supplier_id, events) -> our estimate of the supplier, the same one the tools price with: its quote and
        standard terms, and its own cost breakdown once it has sent one. None without data (C then leaves the
        relationship charge out, which matters only in stabilise / secure_supply cases)."""
        if hasattr(self.tools, "supplier_from_thread"):
            return lambda sid, events: self.tools.supplier_from_thread(case, sid, events)
        if not hasattr(self.tools, "supplier"):
            return None

        def from_quote(sid: str, events: list[dict]) -> dict | None:
            quote = next((m for m in events if m.get("from") != "buyer" and m.get("term_sheets")), None)
            if quote is None:
                return None
            return self.tools.supplier(ctx, sid, quote["term_sheets"][0]["terms"], standard_terms(events))

        return from_quote

    def _lookup(self, ref: str) -> bool:
        try:
            return bool(self.package and self.package.lookup(ref))
        except Exception:
            return False

    def open_case(self, case, threads, table=None, deadline=None, received_at=None, resumed=False) -> CaseSession:
        if self._prepared and self._prepared[0] == case["case_id"]:
            ctx = self._prepared[1]
        else:
            ctx = self.prepare(case, threads)
            self._prepared = (case["case_id"], ctx)
        now = received_at if received_at is not None else time.monotonic()
        states = {sid: ThreadState(sid, list(events), now, rule_only_once=resumed) for sid, events in threads.items()}
        session = CaseSession(
            case=case, ctx=ctx, threads=states, config=self.config, table=table, deadline=deadline,
            estimator=self.estimator(case, ctx), gate=SendGate() if self.config.serialised_session else NoGate(),
        )  # fmt: skip
        session.set_lookup(self._lookup)
        observe.rebuild_case(session)
        session.c_open = policy.opening_cost(session)
        if case.get("buyer", {}).get("category_goal") in ("stabilise", "secure_supply"):
            blind = [sid for sid in threads if session.estimates.get(sid) is None]
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
                    s, th, out.plan.term_sheets, self.pipeline.rule_email(s, th, out.tactic, out.plan, out.context)
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
    """`--agent buyer`: one BuyerAgent per case. Shared across cases: the data package (behind the tools' lock: one
    SQLite connection, up to 10 cases in parallel), the LLM client, the telemetry and the profiler (precomputed
    profiles + live reads of case facts, Saed). Per case: the strategist (S2+S3 in one call) and the writer.
    Without LLM_MODEL every role runs on its rule twin (no LLM calls at all)."""
    safe = thread_safe(package)
    complete = llm_from_env() if os.environ.get("LLM_MODEL") else None
    tools = DataTools(safe)
    config, telemetry = OrchestratorConfig.from_env(), Telemetry()
    profiler = LLMProfiler(complete)

    def new_agent() -> BuyerAgent:
        return BuyerAgent(
            tools, profiler, RuleAnalyst(), LLMStrategist(complete), LLMWriter(complete), safe, config, telemetry
        )

    return new_agent


__all__ = ["BuyerAgent", "buyer_factory"]
