"""The role pipeline (owner: Jeddou): the one place the orchestrator calls tools and roles in a round.

  fit       tools.fit_concessions     where the supplier's prices converge (numbers)
  read      analyst.read              is it still open, what it wants (words + fit)
  plan      tools.plan_offers         the deterministic planner's 1-3 term sheets: the follow step, and the push
                                      step where the policy allows a push
  choose    strategist.choose         the decision agent, knowing both plans: accept / send, follow the planner or
                                      push for price, which arguments, one question (one LLM call)
  clamp     policy.coerce_tactic      within this round's permissions
  pre-guard guard.check_sheets        only sheets we would be content to see accepted
  write     writer.write              the deterministic e-mail (tools.message); a pushed message's paragraph is
                                      the one LLM-written part

Every stage has a time slice and a rule twin: a slow, broken or absent model never costs the 60-second limit, it
costs that stage's quality for one round - and the rule twins together are exactly the deterministic layer. The
decision agent sees one thread and an argument list already filtered for IDs cited earlier in the case, so its
indices stay valid and every ID is cited once per case.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field, replace

from ...cost import complete_offer
from . import guard, policy
from .contracts import BehaviourRead, CaseContext, ConcessionFit, Email, OfferPlan, Tactic
from .roles import LLMWriter, RuleAnalyst, RuleStrategist
from .state import CaseSession, ThreadState
from .tools import message, planner

log = logging.getLogger("arena")
# Role calls run here so the orchestrator can bound them; a call that overruns keeps its worker until it ends.
# 10 cases x up to 3 threads x 1 call at a time = 30 in flight at worst.
_POOL = ThreadPoolExecutor(max_workers=48, thread_name_prefix="buyer-role")
ANALYST_SLICE_S = 5.0
SAFETY_S = 2.0  # never start a stage that would end closer than this to the move budget
TURN_WAIT_S = 8.0  # at most this long for a thread ahead in the claiming order to choose its arguments
MIN_ROUND_S = 30.0  # the wait never eats into the time a full round with the model needs


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


class RolePipeline:
    def __init__(self, tools, analyst, strategist, writer):
        self.tools = tools
        self.analyst, self.strategist, self.writer = analyst, strategist, writer or LLMWriter(None)
        self.rule_analyst, self.rule_strategist = RuleAnalyst(), RuleStrategist()
        self.rule_writer = LLMWriter(None)  # no model: the persona template, same checks

    # ---- context ------------------------------------------------------------------------------------

    @staticmethod
    def threads(session: CaseSession) -> dict[str, list[dict]]:
        return {sid: list(t.events) for sid, t in session.threads.items()}

    def round_context(self, session: CaseSession, th: ThreadState) -> CaseContext:
        """The arguments refreshed from every thread (its asked price, rival offers, its reactions), filtered to
        what may still be cited to this supplier."""
        ctx = session.ctx
        if hasattr(self.tools, "refresh"):
            try:
                ctx = self.tools.refresh(ctx, self.threads(session))
            except Exception:
                log.exception("%s: tools.refresh failed - using the prepared arguments", th.supplier_id)
        sid = th.supplier_id
        with session.lock:
            taken = {r for r, owner in session.reserved.items() if owner != sid}
        usable = tuple(
            c
            for c in ctx.claims.get(sid, ())
            if all(guard.cite_problem(session, th, r) is None and r not in taken for r in c.refs)
        )
        return replace(ctx, claims={**ctx.claims, sid: usable})

    @staticmethod
    def reserve_arguments(session: CaseSession, th: ThreadState, ctx: CaseContext, tactic: Tactic) -> Tactic:
        """Claim this message's arguments for this supplier at decision time, under the case lock: threads decide in
        parallel, and an argument another bidder's message already chose is replaced by the next free one in the
        rules' order (one at a time, the way a sequential buyer would choose)."""
        sid, claims = th.supplier_id, ctx.claims.get(th.supplier_id, ())
        with session.lock:

            def free(i: int) -> bool:
                return all(r.startswith("offer:") or session.reserved.get(r, sid) == sid for r in claims[i].refs)

            kept = [i for i in tactic.claims if free(i)]
            spare = (i for i in range(len(claims)) if i not in kept and i not in tactic.claims and free(i))
            kept += [i for _, i in zip(range(len(tactic.claims) - len(kept)), spare, strict=False)]
            for i in kept:
                session.reserved.update({r: sid for r in claims[i].refs if not r.startswith("offer:")})
            session.choosing.pop(sid, None)  # claimed: the threads behind it need not wait for its e-mail
        session.notify()
        return replace(tactic, claims=tuple(kept))

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
        with session.lock:  # the claiming order: fewest of our messages, then the cheapest standing sheet
            session.choosing[th.supplier_id] = (th.our_rounds, th.c, th.supplier_id)
        try:
            return self._run(session, th, v, deadline, rule_only)
        finally:
            with session.lock:
                session.choosing.pop(th.supplier_id, None)
            session.notify()

    def _run(self, session: CaseSession, th: ThreadState, v: policy.Verdict, deadline: float, rule_only: bool):
        used: dict[str, str] = {}
        timings: dict[str, int] = {}
        sid, events = th.supplier_id, list(th.events)
        # the threads ahead in the claiming order choose first: this one's arguments then exclude theirs
        session.wait_turn(sid, max(0.0, min(TURN_WAIT_S, deadline - time.monotonic() - MIN_ROUND_S)))
        ctx = self.round_context(session, th)

        def left() -> float:
            return deadline - time.monotonic() - SAFETY_S

        def slice_(limit: float) -> float:
            return 0.0 if rule_only or left() < session.config.min_llm_window_s else min(limit, left())

        t_fit = time.monotonic()
        fit = self._fit(ctx, sid, events, used)
        timings["fit"] = int((time.monotonic() - t_fit) * 1000)
        th.fit = fit
        ctx = replace(ctx, fits={**ctx.fits, sid: fit})
        rule_read = self._safe(lambda: self.rule_analyst.read(ctx, sid, events, fit), None, "rule_read")
        rule_read = rule_read or BehaviourRead(sid, 0.5, 0.2, (), ())
        read = self._stage(
            "read", lambda: self.analyst.read(ctx, sid, events, fit), lambda: rule_read,
            0.0 if rule_only else min(ANALYST_SLICE_S, max(left(), 0.0)), used, timings,
        )  # fmt: skip
        reads = {sid: read}
        view = {sid: events}
        t0 = time.monotonic()
        offers = {"follow": self._plan(ctx, session, th, Tactic(sid, "send"), used)}
        for price in ("trim", "push"):
            if policy.margin_allowed(session, th, price):
                offers[price] = self._plan(ctx, session, th, Tactic(sid, "send", price=price), {})
        timings["plan"] = int((time.monotonic() - t0) * 1000)
        ctx = replace(ctx, offers={**ctx.offers, sid: offers})
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
        # the agent's accept only where it dominates the deterministic layer's own next offer
        v = replace(v, accept_permitted=v.accept_permitted and policy.dominates(session, th, offers["follow"]))
        tactic = policy.coerce_tactic(session, th, tactic, v, len(ctx.claims.get(sid, ())))
        if tactic.action == "accept":
            return RoleOutput(tactic, None, None, fit, read, used, timings)
        tactic = self.reserve_arguments(session, th, ctx, tactic)

        plan = offers.get(tactic.price, offers["follow"])
        if tactic.package and hasattr(self.tools, "add_package"):
            try:
                plan = self.tools.add_package(ctx, sid, self.threads(session), plan, tactic.package)
            except Exception:
                log.exception("%s: the agent's package could not be priced - the planner's sheets", sid)
        sheets, sheet_problems = guard.check_sheets(session, th, plan.term_sheets)
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
        """The deterministic e-mail for a plan, with the same arguments the roles saw (used after a guard reject)."""
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

    def _plan(self, ctx, session, th, tactic, used) -> OfferPlan:
        try:
            plan = self.tools.plan_offers(ctx, th.supplier_id, self.threads(session), tactic)
            if isinstance(plan, OfferPlan) and plan.term_sheets:
                used["plan"] = "main"
                return plan
            used["plan"] = "empty"
        except Exception:
            log.exception("%s: plan_offers failed - repeating our last offer", th.supplier_id)
            used["plan"] = "error"
        return last_offer(session, th)

    @staticmethod
    def _safe(fn, default, name):
        try:
            return fn()
        except Exception:
            log.exception("rule twin %s failed", name)
            return default


def last_offer(session: CaseSession, th: ThreadState) -> OfferPlan:
    """Without the planner: our last sheets again (never a concession without it); before our first offer, its own
    terms at the planner's anchor share of its price. The guard decides whether any of them may still go out."""
    ours = [m for m in th.events if m.get("from") == "buyer" and m.get("term_sheets")]
    if ours:
        sheets = tuple(ts.get("terms", ts) for ts in ours[-1]["term_sheets"])
    elif (price := th.standing_terms.get("unit_price")) is not None:
        strategic = session.case["buyer"].get("category_goal") in planner.STRATEGIC
        share = planner.STRATEGIC_ANCHOR_SHARE if strategic else planner.ANCHOR_SHARE
        sheets = (complete_offer(session.case, {**th.standing_terms, "unit_price": round(price * share, 2)}),)
    else:
        sheets = ()
    return OfferPlan(th.supplier_id, sheets, tuple(session.cost(th.supplier_id, s) for s in sheets), "our last offer")


REPEAT_BODY = "\n".join([message.INTRO, "Our proposal attached stands.", message.SIGN_OFF])


def repeat_message(session: CaseSession, th: ThreadState) -> dict | None:
    """Our last sheets again where the guard still lets them go out, or None: an answer within 60 s that concedes
    nothing (only when no new sheet of the planner may go out)."""
    sheets, _ = guard.check_sheets(session, th, last_offer(session, th).term_sheets)
    if not sheets:
        return None
    sheets = [{"terms": t} for t in sheets]
    return {"subject": message.subject(session.case), "body": REPEAT_BODY, "term_sheets": sheets, "refs": []}


ALTERNATIVE_BODY = "\n".join(
    [message.INTRO, "We take up your alternative package as attached. Please confirm it so we can close.",
     message.SIGN_OFF]
)  # fmt: skip


def alternative_message(session: CaseSession, th: ThreadState, terms: dict) -> dict | None:
    """Its own cheaper alternative back as our only sheet (policy.final_alternative), if the guard lets it go out."""
    sheets, _ = guard.check_sheets(session, th, [terms])
    if not sheets:
        return None
    sheets = [{"terms": t} for t in sheets]
    return {"subject": message.subject(session.case), "body": ALTERNATIVE_BODY, "term_sheets": sheets, "refs": []}
