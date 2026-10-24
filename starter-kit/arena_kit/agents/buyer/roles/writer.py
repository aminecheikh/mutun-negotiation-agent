"""Writer: the e-mail around the chosen claims and term sheets (owner: Saed).

v0: one LLM call with prompts/writer.md, a template when there is no model or it is late. The writer
never states a price or figure that is not in the term sheets or the claim sentences it is given.
"""

import json

from ....play import latest
from ..contracts import CaseContext, Email, OfferPlan, Tactic
from .llm import call_json, prompt

TIMEOUT_S = 15.0


def template(ctx: CaseContext, tactic: Tactic, plan: OfferPlan, claims: list[str]) -> Email:
    lines = ["Dear Sir or Madam,", "", "thank you for your offer."]
    lines += claims
    if tactic.question:
        lines.append(tactic.question)
    lines += [
        "Please find our proposal in the attached term sheet.",
        "",
        "Kind regards",
        "Hollerau Pumpenwerke, Purchasing",
    ]
    return Email(subject=f"Re: {ctx.case['title']}", body="\n".join(lines))


class LLMWriter:
    def __init__(self, complete=None):
        self.complete = complete
        self.system = prompt("writer")

    def write(self, ctx: CaseContext, tactic: Tactic, plan: OfferPlan, events: list[dict]) -> Email:
        available = ctx.claims.get(tactic.supplier_id, ())
        chosen = [available[i] for i in tactic.claims if 0 <= i < len(available)][:3]
        sentences = [c.sentence for c in chosen]
        user = json.dumps(
            {
                "case_title": ctx.case["title"],
                "briefing": ctx.case.get("briefing", ""),
                "supplier_last_email": latest(events).get("body", "")[:1500],
                "tactic": {"levers": tactic.levers, "question": tactic.question, "rationale": tactic.rationale},
                "claim_sentences": sentences,
                "term_sheets": plan.term_sheets,
            },
            ensure_ascii=False,
            default=str,
        )
        reply = call_json(self.complete, self.system, user, TIMEOUT_S)
        if not reply or not reply.get("body"):
            return template(ctx, tactic, plan, sentences)
        refs = tuple(ref for c in chosen for ref in c.refs)
        return Email(
            subject=str(reply.get("subject") or f"Re: {ctx.case['title']}"), body=str(reply["body"]), refs=refs
        )
