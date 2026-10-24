"""Writer: the e-mail around the chosen claims and term sheets (owner: Saed).

v1: one LLM call with prompts/writer.md (tone per persona), then `check` before anything leaves: every claim
sentence verbatim, no number or data ID of the model's own, no leak of urgency, budget or a missing
alternative. A failed check, a late model or no model gives the template, which follows the same rules.
Replays showed why: one wrong scorecard figure "costs trust" for the rest of the case (P01, P05).
"""

import json
import logging
import re

from ....play import latest
from ...llm import ID_RE
from ..contracts import CaseContext, Email, OfferPlan, Tactic
from .llm import call_json, prompt

log = logging.getLogger("arena")
TIMEOUT_S = 15.0
MAX_WORDS = 180  # the prompt asks for 150; claim sentences with their IDs are long
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
LEAKS = re.compile(
    r"\b(urgent\w*|asap|as soon as possible|immediately|right away|quickly|soon|deadline|budget|"
    r"no (?:other )?alternative|only (?:possible )?supplier|depend(?:ent|s)? on you|"
    r"(?:switch|move|change) (?:to )?(?:another|a different|other) supplier|last chance|or else)\b",
    re.I,
)
SIGNATURE = "Kind regards\nHollerau Pumpenwerke, Purchasing"

# The template's tone per persona: an opening that acknowledges the supplier the way its persona wants.
OPENINGS = {
    "partner": "thank you for your offer and for the reliable cooperation over many years.",
    "cash_short": "thank you for your offer. We have looked for a solution that also helps your cash flow.",
    "full_order_book": "thank you for your offer. We value a reliable, well-planned supply from your side.",
    "monopolist": "thank you for your offer.",
    "price_fighter": "thank you for your offer. We see good potential for more business between us.",
    "unknown": "thank you for your offer.",
}
LEVER_LINES = {
    "payment_days": "One package changes the payment terms.",
    "contract_months": "One package offers a longer commitment.",
    "volume_per_year": "One package offers you more volume.",
    "effective_date": "One package moves the effective date.",
    "incoterm": "One package changes the delivery terms.",
    "lead_time_days": "One package changes the lead time.",
    "index_clause": "One package links the price to the index.",
}


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in NUMBER_RE.findall(text)}


def check(body: str, sentences: list[str], allowed_refs: set[str], supplier_text: str, title: str) -> list[str]:
    """Problems that must stop an e-mail; empty means it may be sent. Claim sentences are the only source
    of our figures and IDs; a figure the supplier wrote itself may be repeated."""
    problems = [f"claim sentence changed or missing: {s[:60]}" for s in sentences if s not in body]
    rest = body
    for s in sentences:
        rest = rest.replace(s, " ")
    extra = _numbers(rest) - _numbers(supplier_text) - _numbers(title)
    if extra:
        problems.append(f"numbers not from claims or the supplier: {sorted(extra)[:5]}")
    ids = {m.group(0) for m in ID_RE.finditer(rest)} - allowed_refs - set(ID_RE.findall(supplier_text))
    if ids:
        problems.append(f"data IDs not from the chosen claims: {sorted(ids)[:5]}")
    if leak := LEAKS.search(rest):
        problems.append(f"leak: {leak.group(0)!r}")
    if len(body.split()) > MAX_WORDS:
        problems.append(f"too long: {len(body.split())} words")
    return problems


def subject_for(ctx: CaseContext, events: list[dict]) -> str:
    last = str(latest(events).get("subject") or "").strip()
    while last.lower().startswith("re:"):
        last = last[3:].strip()
    return f"Re: {last or ctx.case['title']}"[:300]


def template(ctx: CaseContext, tactic: Tactic, plan: OfferPlan, claims: list[str], events=None) -> Email:
    """No model, a late model or a failed check: a safe e-mail that still fits the persona."""
    dossier = ctx.dossiers.get(tactic.supplier_id)
    persona = dossier.persona if dossier else "unknown"
    lines = ["Dear Sir or Madam,", "", OPENINGS.get(persona, OPENINGS["unknown"])]
    if claims:
        lines += ["", *claims]
    trades = [LEVER_LINES[lever] for lever in tactic.levers if lever in LEVER_LINES][:2]
    many = len(plan.term_sheets) > 1
    lines += ["", "Please find our alternative packages attached." if many else "Please find our proposal attached."]
    lines += trades
    if tactic.question:
        lines += ["", tactic.question]
    lines += ["", SIGNATURE]
    subject = subject_for(ctx, events) if events else f"Re: {ctx.case['title']}"
    return Email(subject=subject, body="\n".join(lines))


class LLMWriter:
    def __init__(self, complete=None):
        self.complete = complete
        self.system = prompt("writer")

    def write(self, ctx: CaseContext, tactic: Tactic, plan: OfferPlan, events: list[dict]) -> Email:
        sid = tactic.supplier_id
        available = ctx.claims.get(sid, ())
        chosen = [available[i] for i in tactic.claims if 0 <= i < len(available)][:3]
        sentences = [c.sentence for c in chosen]
        refs = tuple(dict.fromkeys(ref for c in chosen for ref in c.refs))
        fallback = template(ctx, tactic, plan, sentences, events)
        fallback = Email(fallback.subject, fallback.body, refs)

        last = latest(events)
        supplier_text = str(last.get("body", ""))[:1500]
        dossier = ctx.dossiers.get(sid)
        user = json.dumps(
            {
                "case_title": ctx.case["title"],
                "briefing": ctx.case.get("briefing", ""),
                "round": sum(m.get("from") == "buyer" for m in events) + 1,
                "supplier": {
                    "name": dossier.name if dossier else sid,
                    "persona": dossier.persona if dossier else "unknown",
                    "levers": list(dossier.levers) if dossier else [],
                    "evidence": list(dossier.evidence)[:6] if dossier else [],
                },
                "supplier_status": last.get("status"),
                "supplier_last_email": supplier_text,
                "tactic": {"levers": tactic.levers, "question": tactic.question, "rationale": tactic.rationale},
                "claim_sentences": sentences,
                "term_sheets": plan.term_sheets,
            },
            ensure_ascii=False,
            default=str,
        )
        reply = call_json(self.complete, self.system, user, TIMEOUT_S)
        if not reply or not reply.get("body"):
            return fallback
        body = str(reply["body"]).strip()
        allowed_text = f"{supplier_text}\n{tactic.question or ''}"
        problems = check(body, sentences, set(refs), allowed_text, ctx.case["title"])
        if problems:
            log.warning("%s: writer e-mail rejected (%s) - using the template", sid, "; ".join(problems))
            return fallback
        subject = str(reply.get("subject") or "").strip() or fallback.subject
        return Email(subject=subject[:300], body=body, refs=refs)
