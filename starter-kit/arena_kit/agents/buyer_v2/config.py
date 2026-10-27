"""Every v2 constant in one place, each with where it comes from. Environment overrides use the V2_ prefix.

Timing follows the brief: 60 s per message, counted from the supplier's reply. The whole decision - specialist
agents in parallel, the negotiator, the guard - must leave the building well before that; every stage has a
deadline inside the move budget and a deterministic fallback, so a slow model costs quality, never a timeout.
Nothing here is tuned on practice scores.
"""

import os
from dataclasses import dataclass, fields, replace

ENV_SWITCH = ("AGENTIC_STRUCTURE", "Agentic_structure", "agentic_structure")


def agentic_structure() -> int:
    """1 (default): the v2 multi-agent buyer. 0: the v1 pipeline (roles + orchestrator) stays available."""
    for name in ENV_SWITCH:
        value = os.environ.get(name)
        if value is not None and value.strip() != "":
            try:
                return int(value.strip())
            except ValueError:
                return 1
    return 1


@dataclass(frozen=True)
class V2Config:
    # ---- time per message (brief: 60 s from the supplier's reply) -------------------------------------
    move_budget_s: float = 48.0  # receipt of a reply -> our message leaves; 12 s cushion for network and server
    analysts_by_s: float = 20.0  # the specialist agents report by this many seconds after the reply
    # counterpart and market read documents (live: ~19 s, over 20 s); the negotiator needs ~10-14 s after them
    static_analysts_by_s: float = 25.0
    decide_by_s: float = 42.0  # the negotiator answers by then; the guard and the POST use the rest
    min_llm_window_s: float = 5.0  # less time than this left: skip the model, use the rule fallback
    # ---- models (Anthropic; the provider and key come from LLM_PROVIDER / ANTHROPIC_API_KEY) ------------
    analyst_model: str = ""  # default: LLM_MODEL
    negotiator_model: str = ""  # default: LLM_MODEL
    analyst_effort: str = "low"  # adaptive thinking effort for the specialists ("" = model default)
    negotiator_effort: str = "medium"
    analyst_max_tokens: int = 8000  # thinking counts against max_tokens
    negotiator_max_tokens: int = 12000
    analyst_tool_turns: int = 1  # tool rounds before a specialist must report (its numbers come precomputed)
    negotiator_tool_turns: int = 1
    max_tool_calls_per_turn: int = 8
    tool_turn_min_s: float = 14.0  # a tool turn needs a second model call: skip it with less time than this left
    analyst_slots: int = 32  # concurrent specialist calls across all cases (rate-limit guard)
    negotiator_slots: int = 16  # negotiators have their own slots: specialists can never starve them
    tool_result_chars: int = 6000  # one tool result is cut here (context and latency)
    # ---- negotiation rules (hard limits are in guard.py; these are tolerances) -----------------------
    same_price_eps: float = 0.005  # EUR: two unit prices this close are the same price
    same_cost_rel: float = 0.0005  # relative C: "costs us no more than our own offer" (rounding of the arena)
    max_claims: int = 3  # api.md: at most 3 checked arguments per message
    # concession discipline (research 02: relative tit-for-tat, decreasing steps, never close the gap ourselves)
    step_gap_share: float = 0.5  # our step never covers more than this share of the remaining gap in C
    step_min_rel: float = 0.003  # a visible step when it stands still: this share of its standing C
    max_email_words: int = 230
    # ---- the case (brief: 8-minute waves in the final; kit play.py margins) --------------------------
    close_margin_s: float = 90.0  # accept the best acceptable sheet this long before the wave's cap
    last_call_s: float = 30.0  # nothing acceptable this close to the cap: walk away (no deal = alternative)
    round_margin_s: float = 15.0  # ... or the slowest round so far plus this, if longer
    poll_s: float = 0.25  # how often a waiting worker or the coordinator re-checks
    # the arena client paces every call (9/s across all cases): a reply's attachments are downloaded under
    # this budget and never before the decision, so a slow download cannot eat the 60 s move limit
    attachment_budget_s: float = 8.0
    # ---- logging ---------------------------------------------------------------------------------------
    log_dir: str = ""  # default: BUYER_LOG_DIR or "runs"; "off" disables

    @classmethod
    def from_env(cls, **overrides) -> "V2Config":
        """Defaults, then V2_<FIELD> environment variables, then explicit overrides."""
        values = {}
        for f in fields(cls):
            raw = os.environ.get(f"V2_{f.name.upper()}")
            if raw is None:
                continue
            kind = type(f.default)
            try:
                values[f.name] = kind(raw) if kind is not bool else raw.strip().lower() in {"1", "true", "yes"}
            except ValueError:
                continue
        config = replace(cls(), **values)
        return replace(config, **overrides) if overrides else config

    def model_for(self, role: str) -> str:
        chosen = self.negotiator_model if role == "negotiator" else self.analyst_model
        return chosen or os.environ.get("LLM_MODEL", "")
