"""Pre-verified claim sentences per supplier (owner: Amine).

Each `Claim` puts a figure next to its data IDs in one sentence, computed from facts.json (and the case's
own `facts`) the way the arena checks it (brief/api.md "What counts"), so it comes back `verified` - at worst
`irrelevant`, never `contradicted`. Only claims that favour us are returned, strongest first. `strength` only
ranks claims for the strategist; it never decides a price.

Kinds: index_change (the part's cost drivers and the series the supplier names itself), scorecard (on-time
delivery below target), quality (ppm above what it commits to), po_history (cheaper past orders of the part),
forecast (our demand rises: volume to trade), should_cost (estimate below its price), notice (a letter giving
less notice than the contract requires). Never another supplier's quote or offer: the organisers ruled out
inter-supplier comparison, so each supplier is argued with on its own data only.
"""

import re

from ....play import standing
from ..contracts import Claim
from . import estimates

MAX_CLAIMS = 10
OTD_TARGET_PCT = 95.0  # company.md: quality agreement, on-time delivery target 95 %
PO_LINES_CITED = (2, 4)  # brief/api.md: 2-8 po lines of the same part; we cite a few recent ones
NAMED_SERIES_BONUS = 0.3  # ranking only: a series the supplier names itself is about its costs
SERIES_TOKEN = re.compile(r"\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+\b")


class _Facts:
    """The values the arena checks against: the case's own facts first, then the package's facts.json."""

    def __init__(self, case: dict, package):
        self.case_facts, self.package = case.get("facts") or {}, package

    def get(self, ref: str) -> dict | None:
        return self.case_facts.get(ref) or (self.package.facts.get(ref) if self.package else None)

    def ids(self, prefix: str) -> list[str]:
        own = [k for k in self.case_facts if k.startswith(prefix)]
        known = [k for k in self.package.facts if k.startswith(prefix)] if self.package else []
        return sorted(set(own) | set(known))


def candidates(case: dict, package, supplier_id: str, threads: dict[str, list[dict]] | None = None) -> tuple:
    facts = _Facts(case, package)
    threads = threads or {}
    ask, asked_pct = _position(case, facts, threads, supplier_id)
    named = _named_series(package, threads.get(supplier_id, []))
    found = list(_scorecard(facts, supplier_id, threads))
    found += _notice(case, facts, supplier_id)
    for part in case.get("parts", []):
        found += _index_changes(package, facts, part, asked_pct, named)
        found += _po_history(package, facts, part, supplier_id, ask)
        found += _forecast(facts, part)
        found += _should_cost(facts, part, ask)
    return _strongest(found)


def _strongest(found: list[Claim]) -> tuple[Claim, ...]:
    """Strongest first; an ID counts once per case, so no two claims share one."""
    out, used = [], set()
    for claim in sorted(found, key=lambda c: -c.strength):
        if used & set(claim.refs):
            continue
        used |= set(claim.refs)
        out.append(claim)
    return tuple(out[:MAX_CLAIMS])


def _position(case: dict, facts: _Facts, threads: dict, sid: str) -> tuple[float | None, float]:
    """(the price the supplier asks, its asked increase in %) from its standing sheet or its letter."""
    letter = facts.get(case.get("trigger_doc") or "") or {}
    letter = letter if letter.get("supplier") == sid else {}
    ask = standing(threads[sid])[1].get("unit_price") if threads.get(sid) else None
    ask = ask or letter.get("asked_price")
    current = case.get("buyer", {}).get("current_unit_price")
    if ask and current:
        return ask, (ask / current - 1) * 100
    return ask, letter.get("asked_pct") or 0.0


def _named_series(package, events: list[dict]) -> list[str]:
    """Index series the supplier names in its own e-mails, in order of appearance."""
    if package is None:
        return []
    known = {r["id"] for r in package.query("SELECT id FROM index_series")}
    text = " ".join(str(m.get("body", "")) for m in events if m.get("from") != "buyer")
    return list(dict.fromkeys(t for t in SERIES_TOKEN.findall(text) if t in known))


def _claim(sentence: str, refs, kind: str, figure: float | None, strength: float) -> Claim:
    return Claim(sentence, tuple(refs), kind, figure, True, round(min(max(strength, 0.0), 1.0), 3))


def _index_changes(package, facts: _Facts, part: str, asked_pct: float, named: list[str]) -> list[Claim]:
    """The 12-month change of each driver that rose less than the supplier asks for (or fell)."""
    if package is None:
        return []
    sql = "SELECT series_id, weight FROM cost_drivers WHERE part_id = ?"
    weights = {r["series_id"]: r["weight"] for r in package.query(sql, (part,))}
    names = {r["id"]: r["name"] for r in package.query("SELECT id, name FROM index_series")}
    out = []
    for series in dict.fromkeys([*named, *weights]):
        change = estimates.index_change(package, series)
        earlier, later = (facts.get(change.earlier_id), facts.get(change.later_id)) if change else (None, None)
        if not (earlier and later):
            continue
        pct = round((later["value"] / earlier["value"] - 1) * 100, 1)
        if pct >= asked_pct:
            continue
        strength = min(1.0, (asked_pct - pct) / 10) * (0.5 + min(weights.get(series, 0.0), 0.5))
        strength += NAMED_SERIES_BONUS if series in named else 0.0
        sentence = (
            f"{_short_name(names.get(series, series))} changed {pct:+.1f}% from {earlier['month']} to {later['month']} "
            f"({change.earlier_id}, {change.later_id})."
        )
        out.append(_claim(sentence, (change.earlier_id, change.later_id), "index_change", pct, strength))
    return out


def _short_name(name: str, limit: int = 60) -> str:
    """A series name short enough for an e-mail: no parenthetical, cut at a comma when long."""
    short = name.split("(")[0].strip().rstrip(",")
    while len(short) > limit and "," in short:
        short = short.rsplit(",", 1)[0].strip()
    return short


def _scorecard(facts: _Facts, sid: str, threads: dict) -> list[Claim]:
    """On-time delivery below our target (latest scorecard), defects above its commitment (another quarter)."""
    rows = [r for r in reversed(facts.ids("sc:")) if (facts.get(r) or {}).get("supplier") == sid]
    out = []
    if rows and (otd := facts.get(rows[0]).get("otd_pct")) is not None and otd < OTD_TARGET_PCT:
        quarter = facts.get(rows[0]).get("quarter", "")
        sentence = (
            f"Your on-time delivery was {otd:.1f}% in the 12 months to {quarter} ({rows[0]}), "
            f"below our {OTD_TARGET_PCT:g}% target."
        )
        out.append(_claim(sentence, (rows[0],), "scorecard", otd, (OTD_TARGET_PCT - otd) / 20))
    committed = standing(threads[sid])[1].get("ppm_commitment") if threads.get(sid) else None
    spare = rows[1:] if out else rows
    if committed and spare and (ppm := facts.get(spare[0]).get("ppm")) is not None and ppm > committed:
        sentence = f"Your defect rate was {ppm:g} ppm on our quality record ({spare[0]}), above your {committed} ppm."
        out.append(_claim(sentence, (spare[0],), "quality", ppm, (ppm - committed) / max(ppm, 1)))
    return out


def _po_history(package, facts: _Facts, part: str, sid: str, ask: float | None) -> list[Claim]:
    """2-4 recent order lines of the part below the ask - this supplier's own first, else any supplier's."""
    if package is None or not ask:
        return []
    sql = "SELECT id FROM po_lines WHERE part_id = ? {} ORDER BY order_date DESC"
    for extra, params in (("AND supplier_id = ?", (part, sid)), ("", (part,))):
        lines = [r["id"] for r in package.query(sql.format(extra), params, limit=40)]
        prices = [(r, (facts.get(r) or {}).get("unit_price")) for r in lines]
        cheaper = [(r, p) for r, p in prices if p is not None and p < ask]
        if len(cheaper) >= PO_LINES_CITED[0]:
            cited = cheaper[: PO_LINES_CITED[1]]
            listed = ", ".join(f"EUR {p:.2f}" for p in sorted({p for _, p in cited}))
            refs = [r for r, _ in cited]
            sentence = f"Our recent orders for this part were placed at {listed} per unit ({', '.join(refs)})."
            mean = sum(p for _, p in cited) / len(cited)
            return [_claim(sentence, refs, "po_history", max(p for _, p in cited), (ask - mean) / ask * 10)]
    return []


def _forecast(facts: _Facts, part: str) -> list[Claim]:
    """Our latest demand forecast change for the part, if demand rises: volume we can trade."""
    rows = facts.ids(f"fc:{part.split(':', 1)[-1]}@")
    latest = (facts.get(rows[-1]) or {}) if rows else {}
    change = latest.get("change_pct")
    if not change or change <= 0 or latest.get("part") != part:
        return []
    sentence = f"Our demand forecast for this part changed {change:+.1f}% ({rows[-1]})."
    # ranked below price evidence: it is volume to trade, not a reason the price is wrong
    return [_claim(sentence, (rows[-1],), "forecast", change, change / 40)]


def _should_cost(facts: _Facts, part: str, ask: float | None) -> list[Claim]:
    ref = f"cost:{part.split(':', 1)[-1]}"
    row = facts.get(ref) or {}
    cost = row.get("unit_cost")
    if not cost or not ask or cost >= ask or row.get("part") != part:
        return []
    sentence = f"Our should-cost estimate for this part is EUR {cost:.2f} per unit ({ref})."
    return [_claim(sentence, (ref,), "should_cost", cost, (ask - cost) / ask * 5)]


def _notice(case: dict, facts: _Facts, sid: str) -> list[Claim]:
    """A letter of the supplier that gives less notice than its framework contract requires."""

    def own(ref: str | None) -> dict:
        row = (facts.get(ref) if ref else None) or {}
        return row if row.get("supplier") == sid and row.get("days") else {}

    contract = next((r for r in facts.ids("ctr:") if "#" not in r and own(r)), None)
    letter = next((r for r in [case.get("trigger_doc"), *facts.ids("doc:")] if own(r)), None)
    if not (contract and letter):
        return []
    days, required = own(letter)["days"], own(contract)["days"]
    if days >= required:
        return []
    sentence = (
        f"Your letter of {own(letter).get('date', '')} gives {days} days' notice ({letter}), "
        f"while our contract requires {required} days ({contract})."
    )
    return [_claim(sentence, (letter, contract), "notice", days, (required - days) / required + 0.3)]
