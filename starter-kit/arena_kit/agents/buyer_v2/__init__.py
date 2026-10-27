"""Buyer agent v2 (owner: Jeddou): a team of agents - specialists in parallel, a negotiator, hard rules in code.

Selected with AGENTIC_STRUCTURE=1 (the default); AGENTIC_STRUCTURE=0 plays the v1 pipeline (`agents/buyer`).
See docs/agents-v2.md for the design. The deterministic tools are the shared ones in `agents/buyer/tools`.
"""

from .config import V2Config, agentic_structure
from .runner import V2Agent, buyer_v2_factory, play_case, play_final, play_set

__all__ = ["V2Agent", "V2Config", "agentic_structure", "buyer_v2_factory", "play_case", "play_final", "play_set"]
