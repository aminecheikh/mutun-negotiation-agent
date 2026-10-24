"""Strategist: this round's decision (owner: Saed).

v0 is rule-based: talk to the supplier whose standing sheet costs least; accept on `agreed` or
`final_offer` or when out of messages. The orchestrator's guard still refuses bad accepts. To build:
an LLM choice (prompts/strategist.md) of action, levers, claims and question from the dossier persona,
the bands and the behaviour reads; keep this rule as the fallback.
"""

import math

from ....cost import total_cost
from ....docs import standard_terms
from ....play import latest, moves_left, standing
from ..contracts import BehaviourRead, CaseContext, Tactic


def cheapest_thread(ctx: CaseContext, threads: dict[str, list[dict]]) -> str:
    def c(sid: str) -> float:
        _, terms = standing(threads[sid])
        return total_cost(ctx.case, terms, standard_terms(threads[sid])) if terms else math.inf

    return min(threads, key=c)


class RuleStrategist:
    def choose(self, ctx: CaseContext, threads: dict[str, list[dict]], reads: dict[str, BehaviourRead]) -> Tactic:
        sid = cheapest_thread(ctx, threads)
        events = threads[sid]
        status = latest(events).get("status")
        if status in ("agreed", "final_offer") or moves_left(ctx.case, events) <= 0:
            return Tactic(sid, "accept", rationale=f"rule: status {status}, {moves_left(ctx.case, events)} left")
        return Tactic(sid, "send", levers=("unit_price",), rationale="rule: counter on price")
