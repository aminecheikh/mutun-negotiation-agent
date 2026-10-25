"""The arguments we may cite to a supplier, ready to paste (owner: Amine; provenance: docs/hybrid.md).

Each `Claim` puts its figure next to the data IDs the arena checks, computed from facts.json and the case's own
facts. Built per supplier every round, then:
  filtered   never an argument for the seller: an index that rose, a rival or a should-cost not under its price,
             an order history not cheaper than its ask, an index claim on a series it says it has hedged
  ordered    notice > rival > quality > index > volume > cost > delivery > history > news, then by this supplier's
             own reactions ("took it into account" up, "does not change our calculation" down)
`default_choice` is the rule's pick for a message: two arguments in our first message, three later, a falling index
first when the supplier argues with costs, a live cheaper rival first when several suppliers bid.
"""

import re
from dataclasses import replace

from ....cost import must_haves_ok
from ....play import latest, live_threads
from ..contracts import Claim
from . import message

PRIORITY = ("notice", "rival", "quality", "index", "volume", "cost", "delivery", "history", "news")
WEAK_SELLER_NEWS = {
    "free_capacity",
    "credit_watch",
    "credit_downgrade",
    "short_time_work",
    "restructuring",
    "supply_normalises",
    "customer_loss",
}  # fmt: skip  (customer_loss: Sunday release, idle capacity)
RIVAL_PLAUSIBLE_SHARE = 0.8  # a rival quote under 80 % of this supplier's price is cheap for a reason (planner too)
HISTORY_LINES = 4  # recent order lines cited (the arena checks 2-8 po lines of one part)
FIRST_MESSAGE_CLAIMS, LATER_CLAIMS = 2, 3
COST_OBJECTION = re.compile(r"\b(?:costs?|prices?|material|energy|wages|index|inflation)\b", re.I)
HEDGE = re.compile(r"hedg|covered at fixed|fixed.price.{0,20}cover|secured at fixed", re.I)
REACTION_KINDS = (  # first match wins: the words a supplier uses for what it checked
    ("index", r"index|indices|series"), ("delivery", r"deliver|on-time|punctual"),
    ("volume", r"forecast|demand|volume"), ("cost", r"should-?cost|cost estimate|estimate"),
    ("notice", r"notice|contract"), ("quality", r"quality|ppm|defect|complaint"),
    ("rival", r"quote|offer|rival|alternative"), ("history", r"order history|orders|purchase|price history"),
    ("news", r"press|news|report"),
)  # fmt: skip


def month_number(month: str) -> int:
    year, mon = map(int, month[:7].split("-"))
    return year * 12 + mon


class _Facts:
    """The values the arena checks against: the case's own facts first, then the package's facts.json."""

    def __init__(self, case: dict, package):
        self.case_facts = case.get("facts") or {}
        self.package_facts = package.facts if package is not None else {}

    def get(self, ref: str | None) -> dict:
        if not ref:
            return {}
        return self.case_facts.get(ref) or self.package_facts.get(ref) or {}

    def items(self, prefix: str):
        seen = set()
        for source in (self.case_facts, self.package_facts):
            for ref, fact in source.items():
                if ref.startswith(prefix) and ref not in seen:
                    seen.add(ref)
                    yield ref, fact


def _claim(sentence: str, refs, kind: str, figure: float | None = None) -> Claim:
    return Claim(sentence, tuple(refs), kind, figure, True, 0.0)


# ---- what the data offers ----------------------------------------------------------------------------------------


def _index(case: dict, package, facts: _Facts) -> list[Claim]:
    """Each cost driver's move over an explicit window ending at its newest month (3-12 months, the start at the
    window's highest value: a recent decline can sit inside an annual rise)."""
    series = set()
    if package is not None:
        for part in case.get("parts", []):
            series |= {r["series_id"] for r in package.query("SELECT series_id FROM cost_drivers WHERE part_id = ?",
                                                            (part,))}  # fmt: skip
    series |= {facts.get(ref).get("series") for ref in case.get("refs", []) if ref.startswith("idx:")} - {None}
    points = [(ref, f) for ref, f in facts.items("idx:") if f.get("month")]
    newest = max((month_number(f["month"]) for _, f in points), default=0)
    found = []
    for name in sorted(s for s in series if s):
        rows = sorted(((ref, f) for ref, f in points if f.get("series") == name), key=lambda rf: rf[1]["month"])
        if len(rows) < 2:
            continue
        end_id, end = rows[-1]
        end_month = month_number(end["month"])
        if newest - end_month > 12:
            continue
        window = [
            (ref, f) for ref, f in rows if 3 <= end_month - month_number(f["month"]) <= 12 and float(f["value"]) > 0
        ]
        if not window:
            continue
        start_id, start = max(window, key=lambda rf: float(rf[1]["value"]))
        change = (float(end["value"]) / float(start["value"]) - 1) * 100
        sentence = (
            f"The {name} index changed {change:+.1f}% from {start['month']} to {end['month']} ({start_id}, {end_id})."
        )
        found.append(_claim(sentence, (start_id, end_id), "index", round(change, 1)))
    return sorted(found, key=lambda c: c.figure)


def _scorecard(case: dict, facts: _Facts, sid: str) -> list[Claim]:
    cards = [(ref, f) for ref, f in facts.items("sc:") if f.get("supplier") == sid]
    if not cards:
        return []
    ref, card = max(cards, key=lambda rf: rf[1].get("quarter", ""))
    if case["buyer"].get("category_goal") == "quality_first" and card.get("ppm") is not None:
        return [_claim(f"Recorded quality was {float(card['ppm']):g} ppm ({ref}).", (ref,), "quality", card["ppm"])]
    if card.get("otd_pct") is not None:
        return [
            _claim(f"On-time delivery was {float(card['otd_pct']):g}% ({ref}).", (ref,), "delivery", card["otd_pct"])
        ]
    return []


def _should_cost(case: dict, facts: _Facts) -> list[Claim]:
    out = []
    for part in case.get("parts", []):
        ref = f"cost:{part.split(':', 1)[-1]}"
        unit_cost = facts.get(ref).get("unit_cost")
        if unit_cost is not None and facts.get(ref).get("part", part) == part:
            sentence = (f"Our should-cost estimate is EUR {float(unit_cost):.2f} per price unit ({ref}); please help "
                        "us reconcile this estimate with your cost breakdown.")  # fmt: skip
            out.append(_claim(sentence, (ref,), "cost", float(unit_cost)))
    return out


def _forecast(case: dict, facts: _Facts) -> list[Claim]:
    out = []
    for part in case.get("parts", []):
        rows = sorted(((r, f) for r, f in facts.items("fc:") if f.get("part") == part),
                      key=lambda rf: rf[0].rsplit("@", 1)[-1], reverse=True)  # fmt: skip
        if rows and rows[0][1].get("change_pct") is not None and float(rows[0][1]["change_pct"]) > 0:
            ref, f = rows[0]
            out.append(_claim(f"The demand forecast changed {float(f['change_pct']):+.1f}% ({ref}).", (ref,), "volume",
                              float(f["change_pct"])))  # fmt: skip
    return out


def _alternative_quote(case: dict, facts: _Facts, sid: str) -> list[Claim]:
    """The case's own alternative quote, when it is current leverage: a quote for this part from another supplier
    whose complete terms meet our must-haves."""
    alt = case["buyer"].get("alternative") or {}
    ref = alt.get("ref")
    if alt.get("kind") != "quote" or not ref or not must_haves_ok(case, alt.get("terms") or {}):
        return []
    fields = set(alt.get("terms") or {}) | set(case.get("fixed_terms", {}))
    if not all(rule["field"] in fields for rule in case["buyer"].get("must_haves", [])):
        return []
    fact = facts.get(ref)
    if fact.get("part") not in case.get("parts", []) or fact.get("supplier") == sid or not fact.get("unit_price"):
        return []
    return [_claim(f"The alternative quote is EUR {float(fact['unit_price']):.2f} per price unit ({ref}).", (ref,),
                   "rival", float(fact["unit_price"]))]  # fmt: skip


def _history(case: dict, facts: _Facts, sid: str) -> list[Claim]:
    """Our recent orders of the part with this supplier (no figure asserted: the data's own values are checked)."""
    out = []
    for part in case.get("parts", []):
        lines = sorted(((r, f) for r, f in facts.items("po:")
                        if f.get("supplier") == sid and f.get("part") == part and f.get("unit_price")),
                       key=lambda rf: rf[1].get("date", ""), reverse=True)[:HISTORY_LINES]  # fmt: skip
        if len(lines) >= 2:
            refs = tuple(r for r, _ in lines)
            mean = sum(float(f["unit_price"]) for _, f in lines) / len(lines)
            out.append(_claim(f"Please compare our recent order history with you for this part ({', '.join(refs)}).",
                              refs, "history", round(mean, 2)))  # fmt: skip
    return out


def _news(facts: _Facts, sid: str) -> list[Claim]:
    """Press reports that weaken the seller's position (idle capacity, cash or credit pressure, easing supply)."""
    return [
        _claim(
            f'We noted the report "{f.get("headline", "press report")}" ({ref}) and aim for a package that suits '
            "your situation.",
            (ref,),
            "news",
        )  # fmt: skip
        for ref, f in sorted(facts.items("news:"))
        if f.get("supplier") == sid and f.get("topic") in WEAK_SELLER_NEWS
    ]


def _notice(case: dict, facts: _Facts, sid: str) -> list[Claim]:
    """The trigger letter gives less notice than the supplier's framework contract requires."""
    letter = case.get("trigger_doc")
    notice = facts.get(letter)
    if notice.get("days") is None or notice.get("supplier") != sid:
        return []
    for ref, contract in facts.items("ctr:"):
        if "#" in ref or contract.get("supplier") != sid or not contract.get("days"):
            continue
        if float(notice["days"]) < float(contract["days"]):
            sentence = (f"The letter gives {float(notice['days']):g} days of notice against the agreed "
                        f"{float(contract['days']):g} days ({letter}, {ref}).")  # fmt: skip
            return [_claim(sentence, (letter, ref), "notice", float(notice["days"]))]
    return []


def _live_rival(case: dict, sid: str, threads: dict[str, list[dict]], estimates: dict) -> list[Claim]:
    """The lowest live price another bidder quotes under this supplier's price - what the supplier engine reads ("the
    buyer shows a much lower rival quote: I give half the step now"). Freight, payment or a broken must-have of the
    rival do not matter for that true statement, only plausibility: a quote under RIVAL_PLAUSIBLE_SHARE of this
    supplier's price is cheap because of a term it breaks and is not cited. Sandbox D02 (2026-10-25): citing the
    rival's lower quote from our first message on raised the score from .555 to .702."""
    events = threads.get(sid) or []
    own_sheets = (latest(events).get("term_sheets") or []) if events else []
    own_price = min((s["terms"]["unit_price"] for s in own_sheets if s["terms"].get("unit_price") is not None),
                    default=float("inf"))  # fmt: skip
    told = " ".join(str(m.get("body") or "") for m in events if m.get("from") == "buyer")
    rivals = []
    for rival in live_threads(threads):
        if rival == sid:
            continue
        for sheet in latest(threads[rival]).get("term_sheets") or []:
            ref, terms = sheet.get("term_sheet_id"), sheet.get("terms") or {}
            price = terms.get("unit_price")
            if not ref or price is None or price >= own_price or ref in told:
                continue
            if price < RIVAL_PLAUSIBLE_SHARE * own_price:
                continue
            rivals.append((price, ref))
    if not rivals:
        return []
    price, ref = min(rivals)
    return [
        _claim(f"Another supplier currently quotes EUR {price:.2f} per price unit ({ref}).", (ref,), "rival", price)
    ]


# ---- filtered and ordered ----------------------------------------------------------------------------------------


def candidates(case: dict, package, supplier_id: str, threads: dict | None = None, estimates: dict | None = None,
               context: str = "") -> tuple[Claim, ...]:  # fmt: skip
    """Every argument we may cite to this supplier this round, best first (`strength` is the rank, 1 = best).
    `context`: its letters and contracts (message.supplier_documents) - read for a hedge on a named series."""
    threads, estimates = threads or {}, estimates or {}
    facts = _Facts(case, package)
    found = [
        *_notice(case, facts, supplier_id),
        *_alternative_quote(case, facts, supplier_id),
        *_index(case, package, facts),
        *_scorecard(case, facts, supplier_id),
        *_should_cost(case, facts),
        *_forecast(case, facts),
        *_history(case, facts, supplier_id),
        *_news(facts, supplier_id),
    ]
    if len(case.get("suppliers", [])) > 1:
        found = [*_live_rival(case, supplier_id, threads, estimates), *found]
    events = threads.get(supplier_id) or []
    ordered = _ordered(_relevant(found, events, context), events)
    n = len(ordered)
    return tuple(replace(c, strength=round((n - i) / n, 3)) for i, c in enumerate(ordered))


def _asking(events: list[dict]) -> float:
    prices = [s["terms"]["unit_price"] for s in latest(events).get("term_sheets") or []
              if (s.get("terms") or {}).get("unit_price") is not None] if events else []  # fmt: skip
    return min(prices, default=float("inf"))


def _relevant(found: list[Claim], events: list[dict], context: str) -> list[Claim]:
    """Drop what argues for the seller or what it already ruled out."""
    asking = _asking(events)
    replies = [m for m in events if m.get("from") != "buyer"]
    reply = str(replies[-1].get("body") or "") if replies else ""
    sentences = re.split(r"[.!?\n]", f"{context}\n{reply}")
    hedged = {s.lower() for s in sentences if HEDGE.search(s)}
    out = []
    seen_refs: set[str] = set()
    for c in found:
        if seen_refs & set(c.refs):  # each data ID counts once
            continue
        if c.kind == "index" and (c.figure is None or c.figure >= 0):
            continue  # a driver that rose argues for the seller
        if c.kind in ("rival", "cost") and c.figure is not None and c.figure >= asking:
            continue
        if c.kind == "history" and (c.figure is None or c.figure >= asking):
            continue
        if c.kind == "index":
            name = c.sentence.split(" index ", 1)[0].removeprefix("The ").lower()
            if any(name in s for s in hedged):
                continue
        seen_refs |= set(c.refs)
        out.append(c)
    return out


def reactions(events: list[dict]) -> dict[str, int]:
    """How this supplier treated each kind of argument: +1 'took it into account', -1 'does not change our
    calculation' - from its own sentences, so it adapts to any supplier."""
    score: dict[str, int] = {}
    for m in events:
        if m.get("from") == "buyer":
            continue
        for sentence in re.split(r"(?<=[.!?])\s+|\n", str(m.get("body") or "")):
            low = sentence.lower()
            if "holds" not in low and "checked" not in low and "verified" not in low:
                continue
            kind = next((k for k, pattern in REACTION_KINDS if re.search(pattern, low)), None)
            if kind is None:
                continue
            if re.search(r"does not change|doesn.t change|not change our", low):
                score[kind] = score.get(kind, 0) - 1
            elif re.search(r"took it into account|taken into account|fair point", low):
                score[kind] = score.get(kind, 0) + 1
    return score


def _ordered(found: list[Claim], events: list[dict]) -> list[Claim]:
    rank = {kind: i for i, kind in enumerate(PRIORITY)}
    by_kind = sorted(found, key=lambda c: rank.get(c.kind, len(PRIORITY)))
    seen = reactions(events)
    return sorted(by_kind, key=lambda c: (0 if c.kind in ("notice", "rival") else 1, -seen.get(c.kind, 0)))


def default_choice(case: dict, claims: tuple[Claim, ...], events: list[dict], documents: str = "") -> tuple[int, ...]:
    """The rule's arguments for this message (indices into `claims`); `documents`: its letters and contracts."""
    if not claims:
        return ()
    ours = sum(m.get("from") == "buyer" for m in events)
    limit = LATER_CLAIMS if ours else FIRST_MESSAGE_CLAIMS
    chosen = list(range(min(limit, len(claims))))
    replies = [m for m in events if m.get("from") != "buyer"]
    reply = str(replies[-1].get("body") or "") if replies else ""
    goal = case["buyer"].get("category_goal")
    locked = message.signals(documents, events)["price_locked"]
    if COST_OBJECTION.search(reply) and goal not in ("quality_first", "secure_supply") and not locked:
        falling = next((i for i, c in enumerate(claims) if c.kind == "index"), None)  # a counterpoint on its costs
        if falling is not None and falling not in chosen:
            chosen = [falling, *chosen][:limit]
    if len(case.get("suppliers", [])) > 1:
        rival = next((i for i, c in enumerate(claims) if c.kind == "rival"), None)
        if rival is not None:
            chosen = [rival, *(i for i in chosen if i != rival)][:limit]
    return tuple(dict.fromkeys(chosen))
