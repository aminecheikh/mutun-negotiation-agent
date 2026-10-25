"""The deterministic planner (owner: Jeddou): every term sheet we send, from the published cost function only.

Provenance: docs/hybrid.md. The LLM decision agent (roles/strategist) sits on top of this layer and may only keep
more margin (concede less: trim or push), never concede more than it.

  terms       the main sheet carries the cheapest combination of every negotiable option at its price (exact
              search, a beam for large menus); on equal C the supplier-friendly one (longer delivery time and
              contract, higher ppm commitment)
  anchor      the first price: 86 % of the supplier's price, 90 % in stabilise / secure_supply; with a should-cost
              in those cases never under what keeps the supplier at its sustainable margin
  concession  the price gap closes by a share decided by the supplier's own movement in total C: none while it does
              not move (a small nudge from our third message on, so a supplier that waits for us is not deadlocked),
              a quarter while it has not confirmed the terms of our package, a half to a third once it has
  exchanges   up to two more sheets at exactly the main sheet's C: earlier payment, an index clause, the longest
              contract, the simpler quality route, more delivery time, the supplier's own combination of terms -
              its choice shows what it values without costing us anything
  rivals      in a case with several suppliers, a first offer is anchored to the best compliant rival's C
"""

import itertools
import math
import os

from ....cost import OPS, RELATIONSHIP_GOALS, closing_terms, complete_offer, must_haves_ok, supplier_value, total_cost
from ....docs import standard_terms
from ....play import latest, live_threads, moves_left
from . import message

STRATEGIC = {"stabilise", "secure_supply"}
ANCHOR_SHARE, STRATEGIC_ANCHOR_SHARE = 0.86, 0.90
LEVERAGE_ANCHOR_SHARE = 0.80  # leverage cases ("competition and data should bring the price down"): measured in
# practice 2026-10-25 against 0.86 - P05 .742 -> .820, P18 .532 -> .598 (its first move off .532 in 27 runs), P08
# .570 -> .538; net +0.11; sandbox D02 (no estimate) .618 at 0.86 vs .702 at 0.80 (2026-10-25). Floored at the
# credible floor where a full-cost estimate exists. BUYER_LEVERAGE_ANCHOR=0 switches it off.
DEFAULT_SUSTAINABLE_MARGIN_PCT = 5
CANDIDATE_SHARES = (0.98, 1.0)  # strategic cases: prices near its quote where the squeeze charge may be lower; never
# above its own price - a squeeze saving there rests on a point estimate inside its own error band
EXHAUSTIVE_LIMIT, BEAM_WIDTH = 4096, 64
STILL_SHARE = 0.999  # its best C fell by less than 0.1 %: it did not move
NUDGE_SHARE = 0.08  # it did not move: from our third message on, a small step of the remaining gap
UNCONFIRMED_SHARE = 0.25  # it moved but has not confirmed our package's terms
RIVAL_PLAUSIBLE_SHARE = 0.8  # a rival price under 80 % of this one is cheap because of a term it breaks
MAX_SHEETS = 3
SURPLUS_SHARE = 0.5  # of what an exchange is worth to the supplier beyond its cost to us, half comes back to us


# ---- valuation -------------------------------------------------------------------------------------------------


def anchor_share(case: dict) -> float:
    """The first offer's share of the supplier's price: 0.90 in stabilise / secure_supply, 0.80 in leverage cases
    (unless BUYER_LEVERAGE_ANCHOR=0), 0.86 otherwise."""
    goal = case["buyer"].get("category_goal")
    if goal in STRATEGIC:
        return STRATEGIC_ANCHOR_SHARE
    if goal == "leverage" and os.environ.get("BUYER_LEVERAGE_ANCHOR", "1") != "0":
        return LEVERAGE_ANCHOR_SHARE
    return ANCHOR_SHARE


def full_cost_low(supplier: dict) -> float:
    """The low edge of our estimate of the supplier's full cost: the estimate less its own error band."""
    return supplier["full_cost"] * (1 - (supplier.get("uncertainty_pct") or 0) / 100)


def credible_floor(supplier: dict | None, theirs: float) -> float:
    """The lowest price we put to a supplier: the low edge of our full-cost estimate, or of its own price where that
    is lower already (its price refutes a higher estimate). 0 without an estimate."""
    if not supplier:
        return 0.0
    return min(theirs, supplier["full_cost"]) * (1 - (supplier.get("uncertainty_pct") or 0) / 100)


def standard(case: dict, events: list[dict]) -> dict:
    """The supplier's standard terms as complete closing terms: its quote PDF, then the case's fixed terms and plan.
    Every valuation, option menu and supplier estimate starts from this complete set."""
    return closing_terms(case, {}, standard_terms(events))


def cost(case: dict, terms: dict, std: dict, supplier=None) -> float:
    try:
        return total_cost(case, terms, std, supplier)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return math.inf


def alternative_cost(case: dict) -> float:
    alt = case["buyer"].get("alternative") or {}
    if alt.get("kind") in (None, "none") or not alt.get("terms", {}).get("unit_price"):
        return math.inf
    return total_cost(case, alt["terms"])


def best_sheet(case: dict, message: dict, std: dict, supplier=None) -> dict:
    """The terms of the message's cheapest sheet for us (a later start can beat a lower price)."""
    sheets = [s.get("terms") or {} for s in message.get("term_sheets") or []]
    return min(sheets, key=lambda t: cost(case, t, std, supplier), default={})


# ---- terms -----------------------------------------------------------------------------------------------------


def optimise(case: dict, price: float, std: dict, supplier=None, forced: dict | None = None) -> dict | None:
    """The complete sheet at `price` with the lowest C over every negotiable option, every must-have kept; exact
    up to EXHAUSTIVE_LIMIT combinations, a beam of the cheapest partial ones above. Equal C: the longest delivery
    time without extra cost, then the longest contract, then the highest ppm commitment - less effort for the
    supplier at the same cost to us."""
    forced = forced or {}
    seed = complete_offer(case, {**std, "unit_price": price})
    fields, menus = [], []
    for field in case["negotiable"]:
        if field == "unit_price":
            choices = [price]
        else:
            choices = list(case.get("term_options", {}).get(field) or [seed.get(field)])
            if field in std and std[field] not in choices:
                choices.append(std[field])
        if field in forced:
            choices = [v for v in choices if v == forced[field]]
        for rule in case["buyer"].get("must_haves", []):
            if rule["field"] == field:
                choices = [
                    v
                    for v in choices
                    if (v is not None or rule["op"] in ("==", "!=")) and OPS[rule["op"]](v, rule["value"])
                ]
        if not choices:
            return None
        fields.append(field)
        menus.append(choices)
        seed[field] = choices[0]

    def c_of(terms: dict) -> float:
        return cost(case, terms, std, supplier)

    if math.prod(map(len, menus)) <= EXHAUSTIVE_LIMIT:
        candidates = (dict(zip(fields, values, strict=True)) for values in itertools.product(*menus))
    else:
        beam = [seed]
        for field, choices in zip(fields, menus, strict=True):
            beam = sorted(({**t, field: v} for t in beam for v in choices), key=c_of)[:BEAM_WIDTH]
        candidates = iter(beam)
    feasible = (t for t in candidates if must_haves_ok(case, t, std))

    def rank(terms: dict):
        full = closing_terms(case, terms, std)
        return (c_of(terms), -full["lead_time_days"], -full["contract_months"], -(full.get("ppm_commitment") or 0))

    return min(feasible, key=rank, default=None)


def same_exchange(left: dict, right: dict) -> bool:
    """A price belongs to its package: two sheets are the same exchange when every non-price term matches."""
    return {k: v for k, v in left.items() if k != "unit_price"} == {k: v for k, v in right.items() if k != "unit_price"}


def at_cost(case: dict, template: dict, budget: float, std: dict, supplier=None) -> dict | None:
    """The template at the highest whole-cent price that keeps C within `budget`; its terms are never relaxed.
    C is linear in price, except the squeeze charge in stabilise / secure_supply cases makes it U-shaped: then the
    rising branch right of the minimum is searched."""
    if "unit_price" not in case["negotiable"] or not math.isfinite(budget):
        return None
    limit = budget + 0.005 - 1e-8  # the published cost rounds its total to cents

    def at(price: float) -> dict:
        return {**template, "unit_price": price}

    def cash(price: float) -> float:
        return cost(case, at(price), std)

    base = cash(0.01)
    slope = cash(1.01) - base
    if not math.isfinite(slope) or slope <= 0:
        return None
    ceiling = 0.01 + (limit - base) / slope
    if not math.isfinite(ceiling) or ceiling < 0.01:
        return None
    lower = 0.01
    b = case["buyer"]
    squeeze = (
        supplier is not None
        and b.get("category_goal") in RELATIONSHIP_GOALS
        and b.get("relationship_eur_per_point")
        and b.get("sustainable_margin_pct")
    )
    if squeeze:

        def full(price: float) -> float:
            return cost(case, at(price), std, supplier)

        worth = supplier_value(case, at(lower), supplier)  # its margin is defined only for a positive cash worth
        gain = supplier_value(case, at(lower + 1), supplier) - worth
        if gain <= 0:
            return None
        lower = max(lower, lower - worth / gain + 1e-6)
        if lower > ceiling:
            return None
        left, right = lower, ceiling
        for _ in range(64):  # the minimum of the U
            a, z = (2 * left + right) / 3, (left + 2 * right) / 3
            left, right = (left, z) if full(a) <= full(z) else (a, right)
        cents = {max(math.ceil(lower * 100), int(p * 100)) for p in (left, right)}
        cents |= {c + 1 for c in cents}
        feasible = [c / 100 for c in cents if c / 100 <= ceiling and full(c / 100) <= limit]
        if not feasible:
            return None
        left, right = max(feasible), ceiling
        lower = left
        for _ in range(48):  # the rising branch up to the budget
            middle = (left + right) / 2
            left, right = (middle, right) if full(middle) <= limit else (left, middle)
        ceiling = left
    price = math.floor(ceiling * 100 + 1e-7) / 100
    out = at(price)
    if price < lower - 1e-7 or not must_haves_ok(case, out, std):
        return None
    return out if cost(case, out, std, supplier) <= budget else None


def value_equivalent(case: dict, first: dict, templates: list[dict], std: dict, supplier, events: list[dict],
                     rate: float | None = None, ppm: float | None = None):  # fmt: skip
    """The main sheet and at most two distinct exchanges, each re-priced to the main sheet's C - or below, where the
    supplier's own `rate` for money shows it values the exchange more than it costs us (`supplier_priced`). A higher
    price we already offered for exactly the same package is never taken back: that exchange is left out instead."""
    budget = cost(case, first, std, supplier)
    previous = [s["terms"] for m in events if m.get("from") == "buyer" for s in m.get("term_sheets") or []]
    out = [first]
    for template in templates:
        if any(same_exchange(template, kept) for kept in out):
            continue
        priced = at_cost(case, template, budget, std, supplier)
        if priced is None:
            continue
        priced = supplier_priced(case, first, priced, std, rate, ppm, supplier)
        if any(same_exchange(priced, old) and priced["unit_price"] < old["unit_price"] for old in previous):
            continue
        out.append(priced)
        if len(out) == MAX_SHEETS:
            break
    return out


def supplier_priced(case: dict, first: dict, priced: dict, std: dict, rate: float | None, ppm: float | None = None,
                    supplier=None) -> dict:  # fmt: skip
    """An exchange priced at equal C to the main sheet, moved down by half the gap to the price at which the supplier
    values it as much as the main sheet (cost.supplier_value at its own `rate` for money and its real defect rate
    `ppm`, which prices a chargeback for it): where it values the exchange more than it costs us - paid earlier by a
    supplier whose money is dear, a chargeback from one that delivers better than it commits - it still prefers it
    and we keep half the difference. Never above equal C: where it values the exchange less, nothing changes, and
    where the lower price would raise our squeeze charge by more than it saves (`supplier`), neither."""
    if rate is None and ppm is None:
        return priced
    chargeback = "chargeback_eur_per_complaint"
    if rate is None and (priced.get(chargeback) or 0) == (first.get(chargeback) or 0):
        return priced  # only its defect rate is known: it prices nothing but a different chargeback
    who = {"standard": std, "interest_rate": case["buyer"]["interest_rate"] if rate is None else rate}
    if ppm is not None:
        who["ppm"] = ppm
    try:
        target = supplier_value(case, first, who)
        now = supplier_value(case, priced, who)
        slope = supplier_value(case, {**priced, "unit_price": priced["unit_price"] + 1}, who) - now
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return priced
    if slope <= 0:
        return priced
    indifferent = priced["unit_price"] + (target - now) / slope
    if indifferent >= priced["unit_price"] - 0.01:
        return priced
    out = {
        **priced,
        "unit_price": round(priced["unit_price"] - SURPLUS_SHARE * (priced["unit_price"] - indifferent), 2),
    }
    if out["unit_price"] <= 0 or not must_haves_ok(case, out, std):
        return priced
    return out if cost(case, out, std, supplier) <= cost(case, priced, std, supplier) else priced


# ---- concession ------------------------------------------------------------------------------------------------


def concession_step(case: dict, events: list[dict], supplier=None) -> float:
    """The share of the price gap we close this round, from the supplier's movement in total C (so a price cut paid
    for with worse payment, delivery or coverage is not a concession). Rebuilt from the thread: restart-safe."""
    proposals = next((m.get("term_sheets") or [] for m in reversed(events) if m.get("from") == "buyer"), [])
    commercial = set(case["negotiable"]) - {"unit_price"}
    std = standard(case, events)
    confirmed = any(
        commercial.issubset(sheet["terms"])
        and all(sheet["terms"][f] == proposed["terms"].get(f) for f in commercial)
        and must_haves_ok(case, sheet["terms"], std)
        for sheet in latest(events).get("term_sheets") or []
        for proposed in proposals
    )
    quotes = []
    for i, said in enumerate(events):
        if said.get("from") == "buyer" or not said.get("term_sheets"):
            continue
        prior_std = standard(case, events[: i + 1])
        costs = [
            cost(case, s["terms"], prior_std, supplier)
            for s in said["term_sheets"]
            if must_haves_ok(case, s["terms"], prior_std)
        ]
        quotes.append(min(costs, default=math.inf))
    if len(quotes) < 2 or not all(math.isfinite(c) for c in quotes[-2:]):
        return 0.0
    if quotes[-1] >= quotes[-2] * STILL_SHARE:  # a rounding-size or adverse counter is no concession
        return NUDGE_SHARE if sum(m.get("from") == "buyer" for m in events) >= 2 else 0.0
    if not confirmed:
        return UNCONFIRMED_SHARE
    remaining = max(1, moves_left(case, events) - 1)
    return 1 / max(2, min(3, remaining))


# ---- the sheets of one round -----------------------------------------------------------------------------------


def packages(
    case: dict,
    events: list[dict],
    supplier=None,
    concede: float = 1.0,
    rate: float | None = None,
    ppm: float | None = None,
) -> list[dict]:
    """Our 1-3 sheets for the thread's next message. `concede` < 1: the decision agent keeps margin - only that
    share of the step above the minimal (nudge) step; never more than the rules would concede, never less than the
    minimal step. `rate`, `ppm`: the supplier's own value of money and real defect rate (estimates) for pricing
    the exchanges."""
    std = standard(case, events)
    sheets = latest(events).get("term_sheets") or []
    terms = best_sheet(case, latest(events), std, supplier)
    b = case["buyer"]
    theirs = terms.get("unit_price") or b.get("current_unit_price") or b["fallback_unit_price"]
    prior = next(
        (m["term_sheets"][0]["terms"] for m in reversed(events) if m.get("from") == "buyer" and m.get("term_sheets")),
        None,
    )
    previous = None
    if prior:  # carry our concession in C, not as a cash price on different terms
        template = optimise(case, prior["unit_price"], std, supplier)
        equivalent = at_cost(case, template, cost(case, prior, std, supplier), std, supplier) if template else None
        previous = (equivalent or prior)["unit_price"]
    goal = b.get("category_goal")
    strategic = goal in STRATEGIC
    share = anchor_share(case)
    anchor = theirs * share
    if supplier and goal == "leverage" and share < ANCHOR_SHARE:  # deeper, but never under its cost
        anchor = max(anchor, min(theirs * ANCHOR_SHARE, credible_floor(supplier, theirs)))
    if supplier and strategic:  # never under what keeps the supplier at its sustainable margin (low-edge estimate)
        margin = (b.get("sustainable_margin_pct") or DEFAULT_SUSTAINABLE_MARGIN_PCT) / 100
        anchor = max(anchor, min(theirs, full_cost_low(supplier) / max(0.5, 1 - margin)))
    step = min(1.0, concession_step(case, events, supplier))
    price = previous + max(0.0, theirs - previous) * step if previous is not None else anchor
    prices = [round(max(0.01, price), 2)]
    if strategic and supplier:  # a slightly higher price can lower the squeeze charge more than it costs
        floor = round(price, 2) if previous is not None else 0.01
        prices += [round(min(theirs, max(floor, theirs * share)), 2) for share in CANDIDATE_SHARES]
    options = [t for t in (optimise(case, p, std, supplier) for p in prices) if t is not None]
    if not options:
        return []
    first = min(options, key=lambda t: cost(case, t, std, supplier))
    if prior:
        prior_cost = cost(case, prior, std, supplier)
        if step == 0:  # re-optimised terms may move the headline price: keep the whole package at our last bid
            first = at_cost(case, first, prior_cost, std, supplier) or first
        commercial = set(case["negotiable"]) - {"unit_price"}
        asked = [
            cost(case, s["terms"], std, supplier)
            for s in sheets
            if must_haves_ok(case, s["terms"], std)
            and commercial.issubset(s["terms"])
            and commercial.issubset(prior)
            and all(s["terms"][f] == prior[f] for f in commercial)
        ]
        if asked:  # it answered on our package: close the package gap, not a raw price gap
            budget = prior_cost + max(0.0, min(asked) - prior_cost) * step
            first = at_cost(case, first, budget, std, supplier) or first
    exchanges, preferred = _exchanges(case, first, terms, std, supplier, events)
    result = value_equivalent(case, first, exchanges, std, supplier, events, rate, ppm)
    late = _indexed_late(case, first, std, supplier)
    if late is not None and not any(p.get("index_clause") for p in result):
        # an indexed package can look dear under the central margin estimate though it lowers our cash cost and
        # stays within today's standing cost: keep one, unpriced, to test the supplier's risk preference
        standing_cost = cost(case, terms, std, supplier)
        cash_benefit = cost(case, late, std) <= cost(case, first, std)
        if cost(case, late, std, supplier) <= standing_cost and (terms.get("index_clause") or cash_benefit):
            result = [*result[:2], late]
    if preferred is not None:
        result.sort(key=lambda t: not same_exchange(t, preferred))
    if concede < 1 and prior and result:
        result = keep_margin(case, result, prior, events, std, supplier, concede, theirs)
    return result


def keep_margin(case: dict, sheets: list[dict], prior: dict, events: list[dict], std: dict, supplier, concede: float,
                theirs: float) -> list[dict]:  # fmt: skip
    """The decision agent concedes less than the planner: every sheet re-priced to the C between our last offer
    and the planner's new lead sheet - `concede` of the way above the minimal (nudge) step, so a trim still moves.
    In C, so it bites alike where the price is set by the squeeze charge. Credible: a sheet under the credible
    floor (credible_floor) is held at the floor, or left at the planner's price where that is lower; a price we
    already offered for the same package is never taken back."""
    lead_c, prior_c = cost(case, sheets[0], std, supplier), cost(case, prior, std, supplier)
    if not (math.isfinite(lead_c) and math.isfinite(prior_c)) or lead_c <= prior_c:
        return sheets
    budget = prior_c + (lead_c - prior_c) * (NUDGE_SHARE + (1 - NUDGE_SHARE) * max(0.0, min(1.0, concede)))
    floor = credible_floor(supplier, theirs)
    previous = [s["terms"] for m in events if m.get("from") == "buyer" for s in m.get("term_sheets") or []]
    out = []
    for sheet in sheets:
        priced = at_cost(case, sheet, budget, std, supplier)
        if priced is None:
            continue
        if priced["unit_price"] < floor - 0.005:  # under its full cost is no credible offer: hold at the floor
            floored = {**priced, "unit_price": math.ceil(floor * 100 - 1e-7) / 100}
            priced = floored if floored["unit_price"] < sheet["unit_price"] else sheet
        if any(same_exchange(priced, old) and priced["unit_price"] < old["unit_price"] for old in previous):
            continue
        out.append(priced)
    return out or sheets


def _exchanges(case: dict, first: dict, terms: dict, std: dict, supplier, events: list[dict]):
    """(candidate exchanges at the main sheet's price in the order they compete for the two extra slots, the
    supplier's own combination or None). Its own combination leads: it showed it can deliver those terms."""
    out: list[dict] = []
    options, b = case.get("term_options", {}), case["buyer"]
    negotiable = case["negotiable"]
    price = first["unit_price"]

    def add(sheet: dict | None, at: int | None = None) -> None:
        if sheet and sheet not in out and sheet != first:
            out.insert(at, sheet) if at is not None else out.append(sheet)

    payments, lengths = options.get("payment_days", []), options.get("contract_months", [])
    if "payment_days" in negotiable and payments:  # earlier payment for a lower price
        add(optimise(case, price, std, supplier, {"payment_days": min(payments)}))
    trade = None
    if b.get("category_goal") == "quality_first" and "chargeback_eur_per_complaint" in negotiable:
        fair = [v for v in options.get("chargeback_eur_per_complaint", []) if v <= b["cost_per_defect"]]
        if fair:
            trade = optimise(case, price, std, supplier, {"chargeback_eur_per_complaint": max(fair)})
    elif "index_clause" in negotiable:
        indexed = [v for v in options.get("index_clause", []) if v]
        if indexed:
            clause = max(indexed, key=lambda v: v.get("deadband_pct", 0))
            trade = optimise(case, price, std, supplier, {"index_clause": clause})
    elif "contract_months" in negotiable and lengths:
        trade = optimise(case, price, std, supplier, {"contract_months": max(lengths)})
    add(trade)
    if trade and trade.get("index_clause") and payments:  # finance, duration and material risk go together
        linked = {"index_clause": trade["index_clause"]}
        if "payment_days" in negotiable:
            linked["payment_days"] = min(payments)
        if "contract_months" in negotiable and lengths:
            linked["contract_months"] = max(lengths)
        add(optimise(case, price, std, supplier, linked), 0)
        late = _indexed_late(case, first, std, supplier)  # and material protection with the longest payment
        if late is not None:
            if late in out:
                out.remove(late)
            out.insert(1, late)
    if b.get("category_goal") == "quality_first" and {"ppm_commitment", "chargeback_eur_per_complaint"} <= set(
        negotiable
    ):
        caps = options.get("chargeback_eur_per_complaint", [])
        basic = [
            optimise(case, price, std, supplier, {"ppm_commitment": ppm, "chargeback_eur_per_complaint": min(caps)})
            for ppm in options.get("ppm_commitment", [])
            if caps
        ]
        add(max((t for t in basic if t), key=lambda t: t["ppm_commitment"], default=None))  # the simpler route
    deliveries = options.get("lead_time_days", [])
    if "lead_time_days" in negotiable and deliveries:  # more delivery time, if its price cut pays for the delay
        relaxed = [
            {**first, "lead_time_days": d}
            for d in deliveries
            if d > first["lead_time_days"] and must_haves_ok(case, {**first, "lead_time_days": d}, std)
        ]
        if relaxed:
            out.insert(1, max(relaxed, key=lambda t: t["lead_time_days"]))
    own = _own_combination(case, first, terms, events, std)
    if own is not None:
        out.insert(0, own)
    return out, own


def _own_combination(case: dict, first: dict, terms: dict, events: list[dict], std: dict) -> dict | None:
    """The supplier's own combination of terms (it showed it can deliver it), each field snapped to the nearest
    valid option - countered at our price. Only when its sheet is complete and covers our horizon, or when one of
    its terms breaks a must-have and needs the smallest repair."""
    commercial = set(case["negotiable"]) - {"unit_price"}
    repair_needed = any(f in terms and not must_haves_ok(case, {**first, f: terms[f]}, std) for f in commercial)
    full = closing_terms(case, terms, std)
    covers = full["contract_months"] == 0 or full["contract_months"] >= 12 * case["buyer"]["horizon_years"]
    if not ((set(case["negotiable"]).issubset(terms) and covers) or repair_needed):
        return None
    template, quoted = dict(first), standard_terms(events)
    for field in commercial:
        wanted = terms.get(field, quoted.get(field, first[field]))
        menu = list(case.get("term_options", {}).get(field) or [first[field]])
        if field in std and std[field] not in menu:
            menu.append(std[field])
        valid = [v for v in menu if must_haves_ok(case, {**first, field: v}, std)]

        def distance(value, wanted=wanted, fallback=first[field]):
            if isinstance(value, int | float) and isinstance(wanted, int | float):
                return abs(value - wanted)
            return 0 if value == wanted else 1 if value == fallback else 2

        if valid:
            template[field] = min(valid, key=distance)
    return template if must_haves_ok(case, template, std) else None


def _indexed_late(case: dict, first: dict, std: dict, supplier) -> dict | None:
    """Material protection with the longest payment (and contract): risk for cash terms."""
    options, negotiable = case.get("term_options", {}), case["negotiable"]
    indexed = [v for v in options.get("index_clause", []) if v]
    payments, lengths = options.get("payment_days", []), options.get("contract_months", [])
    if "index_clause" not in negotiable or not indexed or not payments or "payment_days" not in negotiable:
        return None
    forced = {"index_clause": max(indexed, key=lambda v: v.get("deadband_pct", 0)), "payment_days": max(payments)}
    if "contract_months" in negotiable and lengths:
        forced["contract_months"] = max(lengths)
    return optimise(case, first["unit_price"], std, supplier, forced)


# ---- several suppliers -----------------------------------------------------------------------------------------


def required_terms(case: dict, terms: dict, std: dict) -> dict | None:
    """A rival sheet with each broken must-have set to its bound - only to read its price as a market reference."""
    if terms.get("unit_price") is None:
        return None
    if must_haves_ok(case, terms, std):
        return terms
    patched = dict(terms)
    for rule in case["buyer"].get("must_haves", []):
        value = closing_terms(case, patched, std).get(rule["field"])
        if rule["op"] in OPS and value is not None and OPS[rule["op"]](value, rule["value"]):
            continue
        if rule["op"] not in {"<=", ">=", "=="}:
            return None
        patched[rule["field"]] = rule["value"]
    return patched if must_haves_ok(case, patched, std) else None


def rival_anchor(case: dict, sid: str, threads: dict[str, list[dict]], sheets: list[dict], estimates: dict) -> list:
    """Our first priced sheets to a bidder, re-priced to the anchor share of the best compliant rival's C when that
    is lower: an expensive supplier's opening must not anchor us above the market."""
    events = threads[sid]
    if any(m.get("from") == "buyer" and m.get("term_sheets") for m in events) or not sheets:
        return sheets
    own = (latest(events).get("term_sheets") or [{}])[0].get("terms", {}).get("unit_price") or math.inf
    rival_costs = []
    for rival in live_threads(threads):
        if rival == sid:
            continue
        rival_std = standard(case, threads[rival])
        for sheet in latest(threads[rival]).get("term_sheets") or []:
            terms = required_terms(case, sheet.get("terms") or {}, rival_std)
            if terms and (terms is sheet["terms"] or sheet["terms"]["unit_price"] >= RIVAL_PLAUSIBLE_SHARE * own):
                rival_costs.append(cost(case, terms, rival_std, estimates.get(rival)))
    if not rival_costs:
        return sheets
    std, supplier = standard(case, events), estimates.get(sid)
    budget = min(min(rival_costs) * anchor_share(case), min(cost(case, t, std, supplier) for t in sheets))
    floor = credible_floor(supplier, own if math.isfinite(own) else math.inf)
    competitive = []
    for sheet in sheets:  # the market anchor, but never under what is credible for this supplier
        priced = at_cost(case, sheet, budget, std, supplier)
        if priced is not None and priced["unit_price"] < floor:
            priced = {**sheet, "unit_price": round(floor, 2)} if round(floor, 2) < sheet["unit_price"] else sheet
        if priced is not None:
            competitive.append(priced)
    return competitive or sheets


# ---- the decision agent's package, bounded by the plan --------------------------------------------------------


def with_agent_package(case: dict, sheets: list[dict], changes: dict, events: list[dict], supplier=None) -> list[dict]:
    """The deterministic sheets with one package the decision agent proposed (non-price terms, e.g. what the
    supplier asked for in words), right after the lead sheet. The planner sets its price so that its C is no more
    than the cheapest deterministic sheet's: it can make the offer more attractive, never dearer for us. Left out
    when no such price exists, it breaks a must-have, it repeats a sheet, or it would take back a higher price we
    already offered for the same package."""
    if not sheets or not changes:
        return sheets
    std = standard(case, events)
    budget = min(cost(case, s, std, supplier) for s in sheets)
    priced = at_cost(case, complete_offer(case, {**sheets[0], **changes}), budget, std, supplier)
    if priced is None or any(same_exchange(priced, s) for s in sheets):
        return sheets
    previous = [s["terms"] for m in events if m.get("from") == "buyer" for s in m.get("term_sheets") or []]
    if any(same_exchange(priced, old) and priced["unit_price"] < old["unit_price"] for old in previous):
        return sheets
    return [sheets[0], priced, *sheets[1:]][:MAX_SHEETS]


# ---- the commitment, stated by code ----------------------------------------------------------------------------


def _amount(value: float) -> str:
    """A quantity in full with thousands separators (no scientific notation for a million units)."""
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


FIRM = " This is the firm quantity available; please price it without assuming additional volume."


def commercial_brief(case: dict, sheets: list[dict], events: list[dict], unit: str = "units") -> str:
    """The binding commitment of our lead package in plain words: the answer to "which volume can you commit to?",
    with the figures of the sheet itself (the model never writes a number)."""
    if not sheets:
        return ""
    first = closing_terms(case, sheets[0], standard_terms(events))
    months = first["contract_months"]
    text = f"Our lead package commits {_amount(first['volume_per_year'])} {unit} per year"
    text += f" for {months:g} months" if months else " as the stated order"
    text += "; the alternatives are separate complete offers, not cumulative commitments."
    if message.objections(events)["volume"] and "volume_per_year" not in case["negotiable"]:
        text += FIRM
    text += (
        f" For that commitment we offer EUR {first['unit_price']:.2f} per price unit, "
        f"payment in {first['payment_days']:g} days and delivery in {first['lead_time_days']:g} days."
    )
    if index := first.get("index_clause"):
        text += (
            f" The proposed {index['series']} clause covers {index['material_share'] * 100:g}% "
            f"with a {index['deadband_pct']:g}% deadband."
        )
    return text
