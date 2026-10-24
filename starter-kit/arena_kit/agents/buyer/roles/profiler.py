"""Profiler: reads a supplier's evidence once per case and sets persona and levers (owner: Saed).

v0 passes the dossier through. To build: an LLM read (prompts/profiler.md) of the dossier's evidence
lines -> persona + levers, cached per supplier on disk (precompute all 46 suppliers offline; the clock is
running when this is called), with case-specific `facts` (new letters in hidden cases) read live.
"""

from ..contracts import Dossier


class PassthroughProfiler:
    def refine(self, case: dict, dossier: Dossier) -> Dossier:
        return dossier
