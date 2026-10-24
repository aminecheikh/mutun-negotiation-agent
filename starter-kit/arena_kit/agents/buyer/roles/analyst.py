"""Analyst: is the supplier still open, and what does it want? (owner: Saed)

v0 is rule-based on the thread status and the concession fit. To build: an LLM read of the supplier's
last e-mail (prompts/analyst.md) combined with the fit's numbers - the LLM interprets words, the fit
supplies the numbers. Keep the rule-based read as the fallback.
"""

from ....play import latest
from ..contracts import BehaviourRead, CaseContext, ConcessionFit


class RuleAnalyst:
    def read(self, ctx: CaseContext, supplier_id: str, events: list[dict], fit: ConcessionFit) -> BehaviourRead:
        status = latest(events).get("status")
        if status == "agreed":
            return BehaviourRead(supplier_id, 0.05, 0.3, (), ("status agreed",))
        if status == "final_offer":
            return BehaviourRead(supplier_id, 0.15, 0.6, (), ("status final_offer",))
        p_more = 0.35 if fit.shrinking else 0.6
        return BehaviourRead(supplier_id, p_more, 0.1, (), (f"status {status}",))
