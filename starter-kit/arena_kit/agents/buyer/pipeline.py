"""The role pipeline (owner: Jeddou): the one place the orchestrator calls tools and roles in a round.

  fit       tools.fit_concessions          numbers: where the supplier's prices converge     (Amine)
  read      analyst.read                   words + fit: is it still open, what it wants     (Saed)
  choose    strategist.choose              send / accept / walk, levers, claims, question   (Saed, one LLM call)
  clamp     policy.coerce_tactic           within this round's permissions                  (Jeddou)
  plan      tools.plan_offers              1-3 term sheets and their C                      (Amine)
  pre-guard guard.check_sheets             only sheets we would be content to see accepted   (Jeddou)
  write     writer.write                   the e-mail around the claims and sheets           (Saed, one LLM call)

Every stage has a time slice and a rule twin: a slow, broken or absent model never costs the 60-second limit,
it costs that stage's quality for one round. The strategist sees one thread (the one this worker answers) and a
claims list already filtered for IDs cited earlier in the case, so its claim indices stay valid and every ID is
cited once per case across all threads.
"""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field, replace

from ...cost import complete_offer
from ...play import latest
from . import guard, policy
from .contracts import BehaviourRead, CaseContext, ConcessionFit, Email, OfferPlan, Tactic
from .observe import detect_language
from .roles import LLMWriter, RuleAnalyst, RuleStrategist
from .state import CaseSession, ThreadState

log = logging.getLogger("arena")
# Role calls run here so the orchestrator can bound them; a call that overruns keeps its worker until it ends.
# 10 cases x up to 3 threads x 1 call at a time = 30 in flight at worst.
_POOL = ThreadPoolExecutor(max_workers=48, thread_name_prefix="buyer-role")
ANALYST_SLICE_S = 5.0
SAFETY_S = 2.0  # never start a stage that would end closer than this to the move budget

# Questions for keep-alive e-mails, per case type: open, no figures, answerable by the role in charge.
QUESTIONS = {
    "en": {
        "price_increase": "Could you send us a breakdown of the cost elements behind your price?",
        "renewal": "Which volume commitment would improve your terms for the coming period?",
        "rfq": "What is your earliest confirmed lead time for the start of production?",
        "bundling": "Could you confirm your price per complete kit?",
        "raw_material": "Could you state your base price without the surcharge clause?",
        "capacity": "What lead time can you confirm against a firm commitment from our side?",
        "quality": "Which defect level will your improvement plan commit to?",
        "default": "Could you send us a breakdown of the cost elements behind your price?",
    },
    "de": {
        "price_increase": "Könnten Sie uns die Kostenbestandteile hinter Ihrem Preis aufschlüsseln?",
        "renewal": "Welche Mengenzusage würde Ihre Konditionen für die kommende Periode verbessern?",
        "rfq": "Welche Lieferzeit können Sie uns für den Serienanlauf verbindlich bestätigen?",
        "bundling": "Könnten Sie uns Ihren Preis pro vollständigem Set bestätigen?",
        "raw_material": "Könnten Sie uns Ihren Basispreis ohne die Zuschlagsklausel nennen?",
        "capacity": "Welche Lieferzeit können Sie gegen eine feste Abnahmezusage bestätigen?",
        "quality": "Auf welche Fehlerquote wird sich Ihr Verbesserungsplan festlegen?",
        "default": "Könnten Sie uns die Kostenbestandteile hinter Ihrem Preis aufschlüsseln?",
    },
}
KEEPALIVE = {
    "en": "Dear Sir or Madam,\n\nthank you for your message. We are reviewing your offer internally and "
    "will come back to you with our position. {question}\n\nKind regards\nHollerau Pumpenwerke, Purchasing",
    "de": "Sehr geehrte Damen und Herren,\n\nvielen Dank für Ihre Nachricht. Wir prüfen Ihr Angebot intern und "
    "melden uns mit unserer Position bei Ihnen. {question}\n\nMit freundlichen Grüßen\n"
    "Hollerau Pumpenwerke, Einkauf",
}


@dataclass(frozen=True)
class RoleOutput:
    tactic: Tactic
    plan: OfferPlan | None  # sheets already passed through guard.check_sheets; None for accept or nothing sendable
    email: Email | None
    fit: ConcessionFit
    read: BehaviourRead
    used: dict[str, str] = field(default_factory=dict)  # stage -> main | rule | timeout | error | skipped
    timings_ms: dict[str, int] = field(default_factory=dict)
    sheet_problems: tuple[str, ...] = ()


def keepalive_email(session: CaseSession, th: ThreadState, question: str | None = None) -> Email:
    """A short answer without a term sheet: keeps a parked thread inside its 60 s without conceding anything.
    The strategist's question when it is plain (no figures: a keep-alive skips the writer's number checks) and in
    the supplier's language, else the case type's, else another one: never a body this thread already got."""
    language = "de" if detect_language(str(latest(th.events).get("body") or "")) == "de" else "en"
    questions = QUESTIONS[language]
    own = [question.strip()] if question and language == "en" and not re.search(r"\d", question) else []
    first = questions.get(str(session.case.get("type")), questions["default"])
    bodies = [KEEPALIVE[language].format(question=q) for q in dict.fromkeys([*own, first, *questions.values()])]
    sent = {str(m.get("body") or "") for m in th.events if m.get("from") == "buyer"}
    body = next((b for b in bodies if b not in sent), bodies[0])
    subject = str(latest(th.events).get("subject") or "").strip() or session.case.get("title", "")
    subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    return Email(subject=subject[:300], body=body)


class RolePipeline:
    def __init__(self, tools, analyst, strategist, writer):
        self.tools = tools
        self.analyst, self.strategist, self.writer = analyst, strategist, writer or LLMWriter(None)
        self.rule_analyst, self.rule_strategist = RuleAnalyst(), RuleStrategist()
        self.rule_writer = LLMWriter(None)  # no model: Saed's persona template, same checks

    # ---- context ------------------------------------------------------------------------------------

    def round_context(self, session: CaseSession, th: ThreadState) -> CaseContext:
        """Claims refreshed from the threads (series the supplier named, rival offers), then filtered to what
        may still be cited to this supplier."""
        ctx = session.ctx
        if hasattr(self.tools, "refresh"):
            try:
                ctx = self.tools.refresh(ctx, {s: t.events for s, t in session.threads.items()})
            except Exception:
                log.exception("%s: tools.refresh failed - using the prepared claims", th.supplier_id)
            if ctx.bands is not session.ctx.bands:  # re-priced after a cost breakdown: the policy uses them too
                keep = {"bands": ctx.bands, **({"trades": ctx.trades} if hasattr(ctx, "trades") else {})}
                with session.lock:
                    session.ctx = replace(session.ctx, **keep)
        sid = th.supplier_id
        usable = tuple(
            c
            for c in ctx.claims.get(sid, ())
            if c.favours_buyer and all(guard.cite_problem(session, th, r) is None for r in c.refs)
        )
        return replace(ctx, claims={**ctx.claims, sid: usable})

    # ---- stages -------------------------------------------------------------------------------------

    def _stage(self, name, main, fallback, timeout, used, timings):
        t0 = time.monotonic()
        try:
            if timeout <= 0:
                used[name] = "skipped"
                return fallback()
            future = _POOL.submit(main)
            try:
                out = future.result(timeout=timeout)
                used[name] = "main"
                return out
            except FutureTimeout:
                used[name] = "timeout"
                log.warning("stage %s over %.1fs - rule twin", name, timeout)
            except Exception:
                used[name] = "error"
                log.exception("stage %s failed - rule twin", name)
            return fallback()
        finally:
            timings[name] = int((time.monotonic() - t0) * 1000)

    def run(self, session: CaseSession, th: ThreadState, v: policy.Verdict, deadline: float, rule_only=False):
        """One round for one thread. `deadline` is time.monotonic() by which the message must be ready."""
        used: dict[str, str] = {}
        timings: dict[str, int] = {}
        sid, events = th.supplier_id, list(th.events)
        ctx = self.round_context(session, th)

        def left() -> float:
            return deadline - time.monotonic() - SAFETY_S

        def slice_(limit: float) -> float:
            return 0.0 if rule_only or left() < session.config.min_llm_window_s else min(limit, left())

        t_fit = time.monotonic()
        fit = self._fit(ctx, sid, events, used)
        timings["fit"] = int((time.monotonic() - t_fit) * 1000)
        th.fit = fit
        rule_read = self._safe(lambda: self.rule_analyst.read(ctx, sid, events, fit), None, "rule_read")
        rule_read = rule_read or BehaviourRead(sid, 0.5, 0.2, (), ())
        read = self._stage(
            "read", lambda: self.analyst.read(ctx, sid, events, fit), lambda: rule_read,
            0.0 if rule_only else min(ANALYST_SLICE_S, max(left(), 0.0)), used, timings,
        )  # fmt: skip
        reads = {sid: read}
        view = {sid: events}
        rule_tactic = self._safe(
            lambda: self.rule_strategist.choose(ctx, view, dict(reads)), Tactic(sid, "send"), "rule_choose"
        )
        tactic = self._stage(
            "choose", lambda: self.strategist.choose(ctx, view, reads), lambda: rule_tactic,
            slice_(session.config.strategist_timeout_s), used, timings,
        )  # fmt: skip
        if not isinstance(tactic, Tactic):
            tactic, used["choose"] = rule_tactic, "error"
        read = reads.get(sid, read)  # the LLM strategist writes its refined read back into this dict
        th.read = read
        n_claims = len(ctx.claims.get(sid, ()))
        tactic = policy.coerce_tactic(session, th, tactic, v, n_claims)
        if tactic.action == "accept":
            return RoleOutput(tactic, None, None, fit, read, used, timings)

        t0 = time.monotonic()
        plan = self._plan(ctx, session, th, events, tactic, read, used)
        sheets, sheet_problems = guard.check_sheets(session, th, plan.term_sheets)
        timings["plan"] = int((time.monotonic() - t0) * 1000)
        if not sheets:
            return RoleOutput(tactic, None, None, fit, read, used, timings, tuple(sheet_problems))
        plan = OfferPlan(sid, tuple(sheets), tuple(session.cost(sid, s) for s in sheets), plan.rationale)

        email = self._stage(
            "write", lambda: self.writer.write(ctx, tactic, plan, events),
            lambda: self.rule_writer.write(ctx, tactic, plan, events),
            slice_(session.config.writer_timeout_s), used, timings,
        )  # fmt: skip
        if not isinstance(email, Email):
            email, used["write"] = self.rule_writer.write(ctx, tactic, plan, events), "error"
        return RoleOutput(tactic, plan, email, fit, read, used, timings, tuple(sheet_problems))

    def rule_email(self, session: CaseSession, th: ThreadState, tactic: Tactic, plan: OfferPlan) -> Email:
        """The template e-mail for a plan, with the same claims list the roles saw (used after a guard reject)."""
        return self.rule_writer.write(self.round_context(session, th), tactic, plan, list(th.events))

    # ---- helpers ------------------------------------------------------------------------------------

    def _fit(self, ctx, sid, events, used) -> ConcessionFit:
        try:
            out = self.tools.fit_concessions(ctx, sid, events)
            used["fit"] = "main"
            return out
        except Exception:
            log.exception("%s: fit_concessions failed", sid)
            used["fit"] = "error"
            return ConcessionFit(sid, (), None, None, False)

    def _plan(self, ctx, session, th, events, tactic, read, used) -> OfferPlan:
        try:
            plan = self.tools.plan_offers(ctx, th.supplier_id, events, tactic, read)
            if isinstance(plan, OfferPlan) and plan.term_sheets:
                used["plan"] = "main"
                return plan
            used["plan"] = "empty"
        except Exception:
            log.exception("%s: plan_offers failed - repeating our last offer", th.supplier_id)
            used["plan"] = "error"
        return fallback_plan(session, th)

    @staticmethod
    def _safe(fn, default, name):
        try:
            return fn()
        except Exception:
            log.exception("rule twin %s failed", name)
            return default


def fallback_plan(session: CaseSession, th: ThreadState) -> OfferPlan:
    """Without the offer engine: our last sheets again (no concession without tools), else the supplier's
    standing terms at our band target. The guard decides whether any of them may go out."""
    ours = [m for m in th.events if m.get("from") == "buyer" and m.get("term_sheets")]
    if ours:
        sheets = tuple(ts.get("terms", ts) for ts in ours[-1]["term_sheets"])
    else:
        band = session.band(th.supplier_id)
        theirs = th.standing_terms.get("unit_price")
        price = band.target if band else (theirs * 0.95 if theirs else None)
        sheets = (complete_offer(session.case, {**th.standing_terms, "unit_price": price}),) if price else ()
    costs = tuple(session.cost(th.supplier_id, s) for s in sheets)
    return OfferPlan(th.supplier_id, sheets, costs, "fallback: offer engine unavailable")
