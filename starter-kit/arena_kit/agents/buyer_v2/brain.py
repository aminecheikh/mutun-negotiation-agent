"""One decision for one supplier thread: facts -> fast paths -> specialists in parallel -> negotiator -> guard.

  1. facts      the deterministic tools refreshed, the thread read (`facts.thread_facts`)
  2. fast path  code decides without a model when the answer is not a judgement call:
                  the supplier accepted our offer             -> accept (no further e-mail)
                  one of its alternative sheets is our offer   -> confirm exactly that package
                  no messages left                             -> accept if signable, else stop
  3. agents     reader + deal every round, counterpart + market on the first round and when new documents
                arrive (cached otherwise) - in parallel, each with its own tools and deadline
  4. negotiator accept / counter / walk_away and the e-mail, from the reports and the decision frame
  5. guard      hard limits on every sheet and every sentence; anything unusable falls back to the rules

Every step has a deterministic fallback, so the move always leaves in time. `Move` is what the runner executes.
"""

import logging
import time
from dataclasses import dataclass, field

from ..buyer.contracts import BehaviourRead, Tactic
from . import agents as A
from . import emails, guard
from . import facts as F
from . import terms as T
from .config import V2Config
from .llm import LLM, AgentCall
from .toolbox import ToolContext, toolset

log = logging.getLogger("arena")


@dataclass(frozen=True)
class Move:
    kind: str  # "accept" | "send" | "stop"
    supplier_id: str
    reason: str
    message: dict | None = None  # for "send": subject, body, term_sheets, refs
    term_sheet_id: str | None = None  # for "accept"
    detail: dict = field(default_factory=dict)  # for the decision trail


class Brain:
    def __init__(self, tools, llm: LLM | None, config: V2Config | None = None, package=None, trail=None):
        self.tools, self.llm, self.package, self.trail = tools, llm, package, trail
        self.config = config or V2Config()

    # ---- the case ----------------------------------------------------------------------------------------

    def open_case(self, case: dict, threads: dict[str, list[dict]]) -> F.CaseState:
        try:
            ctx = self.tools.prepare(case, threads)
        except Exception:
            log.exception("%s: tools.prepare failed - negotiating with the bare case", case.get("case_id"))
            from ..buyer.contracts import CaseContext

            ctx = CaseContext(case, {}, {}, {}, F.alternative_cost(case))
        state = F.CaseState(case=case, ctx=ctx, tools=self.tools, config=self.config, c_alt=F.alternative_cost(case))
        F.with_estimates(state, threads)
        state.c_open = F.opening_cost(state, threads)
        state.burned = F.cited_ids(threads)
        return state

    # ---- one decision -------------------------------------------------------------------------------------

    def decide(self, state: F.CaseState, sid: str, threads: dict[str, list[dict]], received_at: float) -> Move:
        t0 = time.monotonic()
        try:
            move = self._decide(state, sid, threads, received_at)
        except Exception as e:  # never lose a move to a bug: the rules always have an answer
            log.exception("%s/%s: v2 decision failed - rule fallback", state.case.get("case_id"), sid)
            try:
                move = self._fallback(state, sid, threads, F.thread_facts(state, sid, threads[sid]), f"error: {e!r}")
            except Exception:
                log.exception("%s/%s: rule fallback failed too - last resort", state.case.get("case_id"), sid)
                move = self._last_resort(state, sid, threads)
        move.detail.setdefault("seconds", round(time.monotonic() - t0, 2))
        try:
            self._remember(state, sid, threads[sid], move)
        except Exception:  # memory refines later rounds; it must never cost this one
            log.exception("%s/%s: could not record the round", state.case.get("case_id"), sid)
        return move

    def _remember(self, state: F.CaseState, sid: str, events: list[dict], move: Move) -> None:
        """Our negotiation log for this supplier: each round's decision, why, what we offered and argued, and how
        the reader understood its message - so every later round knows the strategy so far, not only the e-mails."""
        if move.kind not in ("send", "accept", "stop"):
            return
        message = move.message or {}
        entry = {
            "round": sum(m.get("from") == "buyer" for m in events) + 1,
            "path": move.detail.get("path"),
            "decision": move.detail.get("decision") or move.kind,
            "why": str(move.detail.get("reasoning") or move.reason)[:500],
            "sheets": [
                {
                    "terms": T.describe(s["terms"]),
                    "c": round(s["c"]) if s.get("c") not in (None, float("inf")) else None,
                }
                for s in move.detail.get("sheets") or []
            ],  # fmt: skip
            "cited": list(message.get("refs") or []),
            "its_message": move.detail.get("read"),
            "corrections": (move.detail.get("guard") or [])[:4],
        }
        with state.lock:
            state.notes.setdefault(sid, {}).setdefault("log", []).append(entry)

    def _decide(self, state: F.CaseState, sid: str, threads: dict[str, list[dict]], received_at: float) -> Move:
        F.refreshed(state, threads)
        events = threads[sid]
        f = F.thread_facts(state, sid, events)
        if not f.alive:
            return Move("stop", sid, f"thread ended ({f.status})", detail={"path": "fast"})
        fast = self._fast_path(state, f, events)
        if fast is not None:
            return fast
        cfg = self.config
        left = received_at + cfg.decide_by_s - time.monotonic()
        if self.llm is None or not self.llm.available or left < cfg.min_llm_window_s:
            why = "no model" if self.llm is None or not self.llm.available else f"only {left:.0f}s left"
            return self._fallback(state, sid, threads, f, why)
        return self._agents(state, sid, threads, f, received_at)

    def _fast_path(self, state: F.CaseState, f: F.ThreadFacts, events: list[dict]) -> Move | None:
        sid = f.supplier_id
        ok, why = guard.accept_ok(state, f)
        if f.accepted_ours and guard.accept_ours_ok(state, f)[0]:
            return Move("accept", sid, "the supplier accepted our offer", term_sheet_id=f.standing.id,
                        detail={"path": "fast", "c": f.standing.c})  # fmt: skip
        if f.moves_left <= 0:  # the arena still takes an accept at the round limit, never another message
            if ok:
                return Move("accept", sid, "no messages left: its standing sheet is signable",
                            term_sheet_id=f.standing.id, detail={"path": "fast", "c": f.standing.c})  # fmt: skip
            return Move("stop", sid, f"no messages left and its sheet is not signable ({why})", detail={"path": "fast"})
        if f.accepted_alternative is not None:
            confirm = self._confirm(state, f, events)
            if confirm is not None:
                return confirm
        return None

    def _confirm(self, state: F.CaseState, f: F.ThreadFacts, events: list[dict]) -> Move | None:
        """Its alternative sheet is our package: send exactly that package back, so it becomes its standing sheet."""
        alt = f.accepted_alternative
        sheet = guard.normalise(state, alt.terms, f)
        if sheet is None:
            return None
        # the same hard limits as any counter, and the package must pass them unchanged - else it is not a confirmation
        checked = guard.check_counter(state, f, [sheet])
        if not checked.sheets or not F.same_terms(state.case, checked.sheets[0], sheet, f.std, 1e-9):
            return None
        c = F.cost(state, f.supplier_id, sheet, f.std)
        lang = emails.language(str((F.latest_supplier(events) or {}).get("body") or ""))
        body = emails.template_body(lang, "confirm", 1, [], False)
        message = {"subject": emails.subject_for(events, state.case.get("title", "")), "body": body,
                   "term_sheets": [{"terms": sheet}], "refs": []}  # fmt: skip
        return Move("send", f.supplier_id, "its alternative sheet is our package: confirm it", message,
                    detail={"path": "fast", "sheets": [{"terms": sheet, "c": c}]})  # fmt: skip

    # ---- the agents -----------------------------------------------------------------------------------------

    def _agents(self, state: F.CaseState, sid: str, threads: dict, f: F.ThreadFacts, received_at: float) -> Move:
        cfg, events = self.config, threads[sid]
        ctx = ToolContext(state, threads, self.package, focus=sid)
        notes = state.notes.setdefault(sid, {})
        latest = F.latest_supplier(events) or {}
        new_docs = bool(latest.get("attachments")) and f.our_rounds > 0
        analysts_deadline = received_at + cfg.analysts_by_s
        context = {"case_id": state.case.get("case_id"), "supplier_id": sid, "round": f.our_rounds + 1}
        calls = []
        for role, spec in A.SPECS.items():
            if spec.static and role in notes and not new_docs:
                continue
            # the profile and market agents read documents: a little longer, the negotiator still has its window
            deadline = received_at + cfg.static_analysts_by_s if spec.static else analysts_deadline
            pre = A.precomputed(role, toolset(list(spec.tools), ctx), f)
            calls.append(AgentCall(
                role=role, system=A.system_prompt(role), user=A.analyst_input(role, state, f, events, pre),
                deadline=deadline, schema=A.SCHEMAS[role], tools=toolset(list(spec.tools), ctx),
                tool_turns=min(cfg.analyst_tool_turns, spec.tool_turns), context=context,
            ))  # fmt: skip
        results = self.llm.run_many(calls)
        reports, status = {}, {}
        for role in A.SPECS:
            r = results.get(role)
            if r is not None:
                status[role] = {"ok": r.ok, "s": r.seconds, "tools": r.tool_calls, "error": r.error}
                if r.ok:
                    reports[role] = r.data
                    if A.SPECS[role].static:
                        notes[role] = r.data
            if role not in reports and A.SPECS[role].static and role in notes:
                reports[role] = notes[role]  # last known profile or market view
        f = F.thread_facts(state, sid, events)  # cheap; the analysts did not change it, but stay exact
        # a figure a specialist stated is never taken as fact: code owns every number it can compute
        corrections = A.check_numbers(state, f, reports)
        if corrections:
            log.info("%s/%s: corrected agent figures: %s", state.case.get("case_id"), sid, "; ".join(corrections))
        # through the pool like the specialists: even a transport that ignores its timeout cannot hold the move
        answer = self.llm.run_many([AgentCall(
            role="negotiator", system=A.system_prompt("negotiator"),
            user=A.negotiator_input(state, f, events, reports,
                                    A.precomputed("negotiator", toolset(list(A.NEGOTIATOR_TOOLS), ctx), f)),
            deadline=received_at + cfg.decide_by_s,
            schema=A.SCHEMAS["negotiator"], tools=toolset(list(A.NEGOTIATOR_TOOLS), ctx),
            tool_turns=cfg.negotiator_tool_turns, context=context,
        )])["negotiator"]  # fmt: skip
        status["negotiator"] = {"ok": answer.ok, "s": answer.seconds, "tools": answer.tool_calls, "error": answer.error}
        if corrections:
            status["corrected_figures"] = corrections
        if not answer.ok:
            move = self._fallback(state, sid, threads, f, f"negotiator: {answer.error}", status, reports)
        else:
            move = self._apply(state, sid, threads, f, answer.data, status, reports)
        read = reports.get("reader")
        if isinstance(read, dict):  # for the log: how we understood its message this round
            keep = ("offer_summary", "movement", "wants", "questions_to_answer", "tone", "walk_risk", "room_to_move")
            move.detail["read"] = {k: read.get(k) for k in keep}
        return move

    def _apply(self, state, sid, threads, f: F.ThreadFacts, d: dict, status: dict, reports: dict) -> Move:
        decision = d.get("decision")
        detail = {"path": "agents", "agents": status, "reasoning": str(d.get("reasoning") or "")[:600],
                  "decision": decision}  # fmt: skip
        if decision == "accept":
            ok, why = guard.accept_ok(state, f)
            if ok:
                return Move("accept", sid, f"negotiator: {detail['reasoning'][:200]}", term_sheet_id=f.standing.id,
                            detail={**detail, "c": f.standing.c})  # fmt: skip
            detail["overruled"] = f"accept refused: {why}"
        elif decision == "walk_away":
            if A.walk_allowed(state, f):
                return Move("stop", sid, f"negotiator walks away: {detail['reasoning'][:200]}", detail=detail)
            detail["overruled"] = "walk away refused: its sheet is signable or it may still move"
        standing = f.standing.terms if f.standing else {}
        proposed, problems = [], []
        for offer in d.get("counter_offers") or []:
            if not isinstance(offer, dict):
                continue
            sheet, p = T.parse_sheet(state.case, offer.get("unit_price"), offer.get("terms"), standing, f.std)
            problems += p
            if sheet.get("unit_price") is not None:
                proposed.append(sheet)
        # AC_next (research 02): what the negotiator wants to offer next is no better for us than its standing sheet
        # (judged before the step cap trims it) - then its sheet is the deal: take it now, a round earlier and safer
        raw_cs = [F.cost(state, sid, n, f.std) for n in (guard.normalise(state, p, f) for p in proposed) if n]
        if raw_cs and f.standing and min(raw_cs) >= f.standing.c * (1 - state.config.same_cost_rel):
            ok, _ = guard.accept_ok(state, f)
            if ok:
                detail["guard"] = problems
                return Move("accept", sid, "AC_next: its standing sheet is at least as good as our next offer",
                            term_sheet_id=f.standing.id, detail={**detail, "c": f.standing.c})  # fmt: skip
        checked = guard.check_counter(state, f, proposed)
        detail["guard"] = problems + checked.notes
        if not checked.sheets:
            return self._fallback(state, sid, threads, f, "no usable counter from the negotiator", status, reports,
                                  extra=detail)  # fmt: skip
        events = threads[sid]
        others = [A.supplier_name(state, s) for s in state.case.get("suppliers") or () if s != sid]
        body, refs, email_notes = guard.clean_email(
            state,
            f,
            events,
            str(d.get("email_body") or ""),
            [str(t) for t in d.get("arguments") or []],
            checked.sheets,
            others,
            A.supplier_name(state, sid),
        )
        detail["guard"] += email_notes
        if body is None:
            body, refs = self._template(state, f, events, len(checked.sheets))
            detail["email"] = "template (the negotiator's e-mail did not pass the checks)"
        subject = str(d.get("email_subject") or "").strip()[:300] or emails.subject_for(
            events, state.case.get("title", "")
        )
        message = {
            "subject": subject,
            "body": body,
            "term_sheets": [{"terms": s} for s in checked.sheets],
            "refs": refs,
        }
        detail["sheets"] = [{"terms": s, "c": F.cost(state, sid, s, f.std)} for s in checked.sheets]
        return Move("send", sid, f"negotiator counters: {detail['reasoning'][:200]}", message, detail=detail)

    # ---- the rules (no model, a late model, or an unusable answer) -------------------------------------------

    def _fallback(self, state, sid, threads, f: F.ThreadFacts, why: str, status=None, reports=None, extra=None) -> Move:
        detail = {"path": "rules", "why": why, **({"agents": status} if status else {}), **(extra or {})}
        events = threads[sid]
        ok, _ = guard.accept_ok(state, f)
        band = getattr(state.ctx, "bands", {}).get(sid)
        fit = getattr(state.ctx, "fits", {}).get(sid)
        stopped = fit is not None and fit.p_room is not None and fit.p_room < 0.2
        if ok:
            if f.status == "final_offer":
                return Move("accept", sid, "rules: a signable final offer", term_sheet_id=f.standing.id, detail=detail)
            if f.moves_left <= 1:
                return Move(
                    "accept", sid, "rules: last message, signable sheet", term_sheet_id=f.standing.id, detail=detail
                )
            at_target = band is not None and f.standing.c <= band.c_target * 1.005
            if f.our_rounds >= 2 and (at_target and stopped):
                return Move("accept", sid, "rules: at our target and it stopped moving", term_sheet_id=f.standing.id,
                            detail=detail)  # fmt: skip
        countered_final = f.status == "final_offer" and any(
            m.get("from") == "buyer" and m.get("term_sheets") for m in events[_last_final_index(events) + 1 :]
        )
        if f.status == "final_offer" and (countered_final or f.moves_left < 2):
            return Move("stop", sid, "rules: final offer we cannot sign and should not push again", detail=detail)
        sheets = self._engine_sheets(state, sid, threads, f)
        if not sheets:
            if ok:
                return Move("accept", sid, "rules: no counter is better than its standing sheet",
                            term_sheet_id=f.standing.id, detail=detail)  # fmt: skip
            if f.status == "final_offer":
                return Move("stop", sid, "rules: final offer, nothing we may offer", detail=detail)
        body, refs = self._template(state, f, events, len(sheets))
        message = {"subject": emails.subject_for(events, state.case.get("title", "")), "body": body,
                   "term_sheets": [{"terms": s} for s in sheets], "refs": refs}  # fmt: skip
        detail["sheets"] = [{"terms": s, "c": F.cost(state, sid, s, f.std)} for s in sheets]
        return Move("send", sid, f"rules counter ({why})", message, detail=detail)

    def _engine_sheets(self, state: F.CaseState, sid: str, threads: dict, f: F.ThreadFacts) -> list[dict]:
        dossier = getattr(state.ctx, "dossiers", {}).get(sid)
        trades = getattr(state.ctx, "trades", {}).get(sid, ())
        wanted = [*(dossier.levers if dossier else ()), *(t.field for t in trades)]
        levers = tuple(dict.fromkeys(x for x in wanted if x in state.negotiable and x != "unit_price"))[:2]
        read = BehaviourRead(sid, 0.5, 0.2, (), ())
        for choice in (levers, ()):
            try:
                plan = self.tools.plan_offers(
                    state.ctx, sid, threads[sid], Tactic(sid, "send", choice or ("unit_price",)), read
                )
            except Exception:
                log.exception("%s: offer engine failed", sid)
                continue
            checked = guard.check_counter(state, f, list(plan.term_sheets))
            if checked.sheets:
                return checked.sheets
        bridge = guard.bridge_sheet(state, f)  # its own terms, priced between our offer and its offer
        if bridge is not None:
            return [bridge]
        if f.our_offer:  # our last offer still stands: repeat what the rules allow of it
            return guard.check_counter(state, f, [s.terms for s in f.our_offer]).sheets
        return []

    def _last_resort(self, state: F.CaseState, sid: str, threads: dict) -> Move:
        """When even the rules failed: accept a signable sheet, else repeat our offer if the rules allow, else stop."""
        try:
            f = F.thread_facts(state, sid, threads[sid])
            if guard.closable(state, f):
                return Move("accept", sid, "last resort: a signable sheet", term_sheet_id=f.standing.id,
                            detail={"path": "last_resort"})  # fmt: skip
            sheets = guard.check_counter(state, f, [s.terms for s in f.our_offer]).sheets if f.our_offer else []
            if sheets:
                events = threads[sid]
                body, refs = self._template(state, f, events, len(sheets))
                message = {"subject": emails.subject_for(events, state.case.get("title", "")), "body": body,
                           "term_sheets": [{"terms": s} for s in sheets], "refs": refs}  # fmt: skip
                return Move("send", sid, "last resort: our offer stands", message, detail={"path": "last_resort"})
        except Exception:
            log.exception("%s/%s: last resort failed", state.case.get("case_id"), sid)
        return Move("stop", sid, "last resort: nothing safe to send", detail={"path": "last_resort"})

    def _template(self, state: F.CaseState, f: F.ThreadFacts, events: list[dict], n_sheets: int):
        latest = F.latest_supplier(events) or {}
        lang = emails.language(str(latest.get("body") or ""))
        args = F.arguments(state, f.supplier_id)[: (2 if f.our_rounds == 0 else 1)]
        kind = "first" if f.our_rounds == 0 else "final" if f.status == "final_offer" else "next"
        body = emails.template_body(lang, kind, n_sheets, [a.sentence for a in args], f.our_rounds == 0)
        refs = [r for a in args for r in a.refs]
        return body, refs


def _last_final_index(events: list[dict]) -> int:
    idx = -1
    for i, m in enumerate(events):
        if m.get("from") != "buyer" and m.get("status") == "final_offer":
            idx = i
            break
    return idx
