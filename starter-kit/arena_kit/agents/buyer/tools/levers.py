"""What each term change is worth to us, in euros and as a price per unit (owner: Amine).

For every negotiable field with allowed options: C at the supplier's terms minus C with that one field changed,
at the same price. Positive = it saves us money. `per_unit` turns the saving into a price step, so the strategist
can trade it ("28 days instead of 56 is worth EUR 0.51 a unit to us"). Covers every term of C: lead time,
quality (ppm, chargeback), payment, freight (Incoterm), coverage (volume, contract length), dates, clauses.
"""

from ....cost import complete_offer, must_haves_ok, total_cost
from ..contracts import Trade


def trades(case: dict, terms: dict, std: dict, supplier: dict | None) -> tuple[Trade, ...]:
    """The best saving per field against the supplier's terms, biggest first."""
    base = complete_offer(case, terms)
    c_base = total_cost(case, base, std, supplier)
    per_price_unit = total_cost(case, {**base, "unit_price": base["unit_price"] + 1}, std, supplier) - c_base
    if per_price_unit <= 0:
        return ()
    best = {}
    for name in case["negotiable"]:
        if name == "unit_price":
            continue
        for option in case.get("term_options", {}).get(name, []):
            if option == base.get(name):
                continue
            changed = complete_offer(case, {**base, name: option})
            if changed.get(name) != option or not must_haves_ok(case, changed, std):
                continue
            saving = c_base - total_cost(case, changed, std, supplier)
            if saving > 0.5 and saving > best.get(name, (None, 0.0))[1]:
                best[name] = (option, saving)
    out = [
        Trade(name, base.get(name), option, round(saving, 2), round(saving / per_price_unit, 4))
        for name, (option, saving) in best.items()
    ]
    return tuple(sorted(out, key=lambda t: t.eur, reverse=True))
