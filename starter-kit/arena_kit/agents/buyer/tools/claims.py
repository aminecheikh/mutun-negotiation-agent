"""Pre-verified claim sentences per supplier (owner: Amine).

PLACEHOLDER: no claims. To build: for the part's cost drivers (`cost_drivers`), the 12-month index change
computed exactly as the arena checks it (brief/api.md "What counts"), rival quotes, PO price history,
scorecards, notice periods - each a `Claim` with the figure and its IDs in one sentence. Only claims that
favour us, checked against facts.json. See skill `data-package`.
"""

from ..contracts import Claim


def candidates(case: dict, package, supplier_id: str) -> tuple[Claim, ...]:
    return ()
