"""Every orchestrator constant in one place (owner: Jeddou), each with where it comes from.

Time limits come from the brief and the API docs; the negotiation rules themselves live in tools.planner and
policy, with their reasons next to them.
"""

import os
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class OrchestratorConfig:
    # ---- time per message (brief §5: 60 s per message, counted from the supplier's reply) ---------------
    move_budget_s: float = 48.0  # our receipt of a reply -> our message leaves; 12 s cushion for network/server
    strategist_timeout_s: float = 17.0  # outer bound around the strategist (roles use 15 s internally)
    # with tool calling one tool turn is a second model call; 24 + writer 16 still fits the 48 s budget
    strategist_tools_timeout_s: float = 24.0
    writer_timeout_s: float = 16.0  # outer bound around the writer (roles use 15 s internally)
    min_llm_window_s: float = 6.0  # less time than this left: skip the LLM stage, use its rule twin
    # ---- case caps in the Live Final (kit play.py: close 90 s before the cap, or slowest round + 15 s) --
    close_margin_s: float = 90.0
    round_margin_s: float = 15.0
    last_call_s: float = 30.0  # accept the best acceptable sheet whatever is in flight, else walk
    rival_in_flight_margin: float = 0.03  # near the cap, an unanswered bidder within 3 % may still come back cheaper
    max_claims: int = 3  # api.md: at most 3 checked arguments per message
    # ---- robustness --------------------------------------------------------------------------------------
    errors_before_rule_only: int = 1  # after one exception in a thread, that thread runs on rule twins only
    errors_before_dead: int = 3  # stop working a thread that keeps failing; the arbiter still closes the case
    serialised_session: bool = False  # one POST in flight per session (env BUYER_SERIALISED_SESSION)
    worker_poll_s: float = 0.5  # how often a parked worker re-checks its thread

    @classmethod
    def from_env(cls, **overrides) -> "OrchestratorConfig":
        config = cls(serialised_session=os.environ.get("BUYER_SERIALISED_SESSION", "0") == "1")
        return replace(config, **overrides) if overrides else config
