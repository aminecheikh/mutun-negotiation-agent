"""What the data says about one supplier, before any e-mail (owner: Amine; the Profiler role refines it).

PLACEHOLDER: name and country from the vendor master, persona "unknown", no evidence. To build:
supplier_intel (owner, equity, credit-index trend), invoices (how late we pay), scorecards, sourcing
(second source), negotiation_history (mean asked - settled), and evidence lines with their IDs from
documents / news / correspondence for the Profiler to read. See skill `data-package`.
"""

from ..contracts import Dossier


def build(case: dict, package, supplier_id: str) -> Dossier:
    row = (package.lookup(supplier_id) if package else None) or {}
    return Dossier(
        supplier_id=supplier_id,
        name=row.get("name", supplier_id),
        country=row.get("country"),
        persona="unknown",
        levers=(),
        history_give_pct=None,
        second_source=False,
        evidence=(),
    )
