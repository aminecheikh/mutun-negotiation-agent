"""Analyst: is the supplier still open, and what does it want? (owner: Saed)

v1 is rule-based and instant: the thread status and the concession fit give p_more, the supplier's own words
give what it wants and how irritated it is. The LLM read happens inside the strategist's single call
(roles/strategist.py, S2+S3 merged to save ~10 s per round); this read is its input and its fallback.
"""

import re

from ....play import latest
from ..contracts import BehaviourRead, CaseContext, ConcessionFit

# What a supplier says -> the term it values. Matched on its e-mails in this thread, newest first.
WANTS = {
    "payment_days": r"pay(?:ment)? (?:earlier|faster|sooner|within)|cash ?flow|zahlungsziel|liquidity|skonto|"
    r"cash discount",
    "volume_per_year": r"\bvolumes?\b|\bquantit|kits? per year|capacity utili[sz]ation|firm commitment",
    "contract_months": r"long[- ]term|multi-year|contract (?:term|length|period)|\b\d+[- ]month",
    "effective_date": r"valid from|effective (?:date|from)|from (?:1|01) (?:january|april|july|october|november)",
    "lead_time_days": r"lead ?time|lieferzeit|delivery (?:time|date)",
    "incoterm": r"\b(?:exw|fca|dap)\b|freight|transport",
    "index_clause": r"index clause|price adjustment clause|surcharge clause|follows? (?:the )?index",
}
# Words that mean pushing again risks the thread.
IRRITATION = re.compile(
    r"without any movement|take it or leave it|last price|letztes angebot|final offer|costs trust|"
    r"does not change our calculation|not our reality|not strategic|please decide|cannot go (?:any )?lower",
    re.I,
)


def supplier_text(events: list[dict], last: int = 3) -> str:
    return "\n".join(str(m.get("body") or "") for m in [e for e in events if e.get("from") != "buyer"][-last:])


def wants_from(text: str) -> tuple[str, ...]:
    return tuple(term for term, pattern in WANTS.items() if re.search(pattern, text, re.I))


class RuleAnalyst:
    def read(self, ctx: CaseContext, supplier_id: str, events: list[dict], fit: ConcessionFit) -> BehaviourRead:
        status = latest(events).get("status")
        text = supplier_text(events)
        wants = wants_from(text)
        irritated = [m.group(0) for m in IRRITATION.finditer(supplier_text(events, last=1))]
        signals = (f"status {status}", *dict.fromkeys(irritated))[:3]
        if status == "agreed":
            return BehaviourRead(supplier_id, 0.05, 0.3, wants, signals)
        if status == "final_offer":
            return BehaviourRead(supplier_id, 0.15, 0.6, wants, signals)
        p_more = 0.35 if fit.shrinking else 0.6
        if fit.last_step_pct is not None and fit.last_step_pct < 0.3:  # it barely moved last time
            p_more = min(p_more, 0.25)
        walk_risk = min(0.1 + 0.15 * len(irritated), 0.6)
        return BehaviourRead(supplier_id, p_more, walk_risk, wants, signals)
