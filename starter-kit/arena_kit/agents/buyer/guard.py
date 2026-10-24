"""The last check before anything reaches a supplier (owner: Jeddou). Pure functions, no LLM."""

import re

from ...cost import must_haves_ok, total_cost
from ...docs import standard_terms
from ...play import standing
from ..llm import ID_RE
from .contracts import CaseContext, Email, OfferPlan

MAX_BODY, MAX_SUBJECT, MAX_REFS = 8000, 300, 20


def accept_ok(ctx: CaseContext, events: list[dict]) -> bool:
    """The standing sheet exists, keeps every must-have and beats the alternative."""
    sheet_id, terms = standing(events)
    if not sheet_id:
        return False
    std = standard_terms(events)
    if not must_haves_ok(ctx.case, terms, std):
        return False
    return ctx.c_alternative is None or total_cost(ctx.case, terms, std) <= ctx.c_alternative


def scrub_ids(text: str, known) -> str:
    """Remove data IDs we cannot back: an unknown ID reads as a lie (`contradicted`)."""
    cleaned = ID_RE.sub(lambda m: m.group(0) if known(m.group(0)) else "", text)
    return re.sub(r"\s*\(\s*[,;\s]*\)", "", cleaned)


def message(ctx: CaseContext, email: Email, plan: OfferPlan, known) -> dict:
    """The arena message: bounded lengths, only known IDs, term sheets exactly as planned."""
    refs = [r for r in dict.fromkeys(email.refs) if known(r)][:MAX_REFS]
    return {
        "subject": email.subject[:MAX_SUBJECT],
        "body": scrub_ids(email.body, known).strip()[:MAX_BODY],
        "term_sheets": [{"terms": t} for t in plan.term_sheets[:3]],
        "refs": refs,
    }
