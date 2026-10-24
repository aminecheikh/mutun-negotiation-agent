"""The orchestrator (owner: Jeddou): one round = fit -> read -> choose -> plan -> write -> guard.

Plain code with LLM roles at fixed points. Every step has a deterministic fallback, and if the whole
round fails the kit's naive move is sent instead, so no case is lost to an exception or a slow model.
"""

import logging
import os
import time

from ...play import live_threads, moves_left, standing
from ..llm import llm_from_env
from ..naive import NaiveAgent
from . import guard
from .contracts import CaseContext, Tactic
from .roles import LLMProfiler, LLMStrategist, LLMWriter, RuleAnalyst
from .tools import DataTools

log = logging.getLogger("arena")


class BuyerAgent:
    def __init__(self, tools, profiler, analyst, strategist, writer, package=None):
        self.tools, self.profiler, self.analyst = tools, profiler, analyst
        self.strategist, self.writer, self.package = strategist, writer, package
        self.fallback = NaiveAgent()
        self.ctx: CaseContext | None = None

    def decide(self, case: dict, threads: dict[str, list[dict]]) -> dict:
        t0 = time.monotonic()
        try:
            decision = self._round(case, threads)
        except Exception:  # never lose a case to a bug: send the naive move
            log.exception("%s: round failed - naive fallback", case["case_id"])
            decision = self.fallback.decide(case, threads)
        log.info("%s: decided in %.1fs: %s", case["case_id"], time.monotonic() - t0, _summary(decision))
        return decision

    def _context(self, case: dict, threads: dict[str, list[dict]]) -> CaseContext:
        if self.ctx is None or self.ctx.case["case_id"] != case["case_id"]:
            ctx = self.tools.prepare(case, threads)
            dossiers = {sid: self.profiler.refine(case, d) for sid, d in ctx.dossiers.items()}
            self.ctx = CaseContext(ctx.case, dossiers, ctx.bands, ctx.claims, ctx.c_alternative)
        return self.ctx

    def _round(self, case: dict, threads: dict[str, list[dict]]) -> dict:
        ctx = self._context(case, threads)
        live = {sid: threads[sid] for sid in live_threads(threads)}
        fits = {sid: self.tools.fit_concessions(ctx, sid, ev) for sid, ev in live.items()}
        reads = {sid: self.analyst.read(ctx, sid, ev, fits[sid]) for sid, ev in live.items()}
        tactic = self.strategist.choose(ctx, live, reads)
        if tactic.supplier_id not in live:
            raise ValueError(f"strategist chose a closed thread: {tactic.supplier_id}")
        return self._act(ctx, live, tactic, reads)

    def _act(self, ctx: CaseContext, live: dict[str, list[dict]], tactic: Tactic, reads: dict) -> dict:
        sid, events = tactic.supplier_id, live[tactic.supplier_id]
        if tactic.action == "walk_away":
            return {"supplier_id": sid, "message": {"action": {"type": "walk_away"}}}
        if tactic.action == "accept":
            if guard.accept_ok(ctx, events):
                return {
                    "supplier_id": sid,
                    "message": {"action": {"type": "accept", "term_sheet_id": standing(events)[0]}},
                }
            log.info("%s: guard refused the accept (%s)", sid, tactic.rationale)
            if moves_left(ctx.case, events) <= 0:
                return {"supplier_id": sid, "message": {"action": {"type": "walk_away"}}}
            tactic = Tactic(sid, "send", tactic.levers, tactic.claims, tactic.question, "guard: accept refused")
        plan = self.tools.plan_offers(ctx, sid, events, tactic, reads[sid])
        email = self.writer.write(ctx, tactic, plan, events)
        return {"supplier_id": sid, "message": guard.message(ctx, email, plan, self._known(ctx, live))}

    def _known(self, ctx: CaseContext, live: dict[str, list[dict]]):
        offers = {standing(ev)[0] for ev in live.values()}

        def known(ref: str) -> bool:
            return ref in offers or ref in ctx.case.get("facts", {}) or bool(self.package and self.package.lookup(ref))

        return known


def _summary(decision: dict) -> str:
    msg = decision["message"]
    if msg.get("action"):
        return f"{decision['supplier_id']} {msg['action']['type']}"
    prices = [ts["terms"].get("unit_price") for ts in msg.get("term_sheets", [])]
    return f"{decision['supplier_id']} send {prices}"


def buyer_factory(package=None):
    """`--agent buyer`: one BuyerAgent per case, sharing the data package, the LLM client and the profiler
    (precomputed profiles + live reads of case facts). Without LLM_MODEL every role uses its rule-based
    fallback (no LLM calls at all)."""
    complete = llm_from_env() if os.environ.get("LLM_MODEL") else None
    tools = DataTools(package)
    profiler = LLMProfiler(complete)
    return lambda: BuyerAgent(tools, profiler, RuleAnalyst(), LLMStrategist(complete), LLMWriter(complete), package)
