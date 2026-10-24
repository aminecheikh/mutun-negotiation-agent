"""Every orchestrator constant in one place (owner: Jeddou), each with where it comes from.

Nothing here is tuned on practice scores (CLAUDE.md "Generalise, don't overfit"): time limits come from the
brief and the API docs, the accept and counter thresholds from the cost function's own scale.
"""

import os
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class OrchestratorConfig:
    # ---- time per message (brief §5: 60 s per message, counted from the supplier's reply) ---------------
    move_budget_s: float = 48.0  # our receipt of a reply -> our message leaves; 12 s cushion for network/server
    strategist_timeout_s: float = 17.0  # outer bound around the strategist (roles use 15 s internally)
    writer_timeout_s: float = 16.0  # outer bound around the writer (roles use 15 s internally)
    min_llm_window_s: float = 6.0  # less time than this left: skip the LLM stage, use its rule twin
    keepalive_after_s: float = 40.0  # a parked thread gets a question this long after the supplier's reply
    keepalive_max: int = 2  # per thread; each one costs a message of the round limit
    # ---- case caps in the Live Final (kit play.py: close 90 s before the cap, or slowest round + 15 s) --
    close_margin_s: float = 90.0
    round_margin_s: float = 15.0
    freeze_s: float = 150.0  # with an acceptable sheet on the table: no new counter-offers inside this window
    last_call_s: float = 30.0  # accept the best acceptable sheet whatever is in flight, else walk
    # ---- accept / counter / walk -----------------------------------------------------------------------
    accept_eps: float = 0.005  # a standing sheet within 0.5 % of the band's target C is the target
    rival_beat_margin: float = 0.01  # another thread "could beat" a sheet if it can plausibly go 1 % lower in C
    rival_in_flight_margin: float = 0.03  # an unanswered rival within 3 % may still come back cheaper
    final_counter_gap: float = 0.02  # one counter after a final offer only if it is > 2 % above our target C
    final_counter_min_moves: int = 2
    final_counter_min_seconds: float = 150.0
    walk_prone_personas: tuple[str, ...] = ("monopolist", "full_order_book")  # dossier personas that walk easily
    # assumption, not tuned: below a 1-in-5 chance of another >= 1 % step (tools.concession p_room) a rival counts
    # as done and no longer holds up an accept elsewhere
    low_room_probability: float = 0.2
    # our offers reach the band target halfway through the rounds (tools.offers TARGET_BY_SHARE_OF_ROUNDS); before
    # that the target is a prior: it closes a thread only when the supplier's own prices show no room left
    prior_share_of_rounds: float = 0.5
    stuck_rounds: int = 3  # rounds of ours after which a supplier that stopped moving counts as stuck
    stuck_improvement: float = 0.001  # "stopped moving": its C fell by less than 0.1 % over its last two replies
    max_claims: int = 3  # api.md: at most 3 checked arguments per message
    # ---- robustness --------------------------------------------------------------------------------------
    errors_before_rule_only: int = 1  # after one exception in a thread, that thread runs on rule twins only
    errors_before_dead: int = 3  # stop working a thread that keeps failing; the arbiter still closes the case
    serialised_session: bool = False  # one POST in flight per session (J0 probe decides; env BUYER_SERIALISED_SESSION)
    worker_poll_s: float = 0.5  # how often a parked worker re-checks its thread

    @classmethod
    def from_env(cls, **overrides) -> "OrchestratorConfig":
        config = cls(serialised_session=os.environ.get("BUYER_SERIALISED_SESSION", "0") == "1")
        return replace(config, **overrides) if overrides else config
