"""What the data says about one supplier, before any e-mail (owner: Amine; the Profiler role refines it).

`build` fills the numbers (history, second source) and a persona from transparent rules, and collects
evidence lines - each with its data IDs - for the Profiler (LLM) to judge. Sources: suppliers, supplier_intel,
spend_cube, invoices, scorecards, quality_notifications, sourcing, contracts + contract_clauses, forecasts,
negotiation_history, news, documents + correspondence/, and the case's own `facts`.

Persona rules score signals; the persona with the most points (>= MIN_PERSONA_SCORE) wins, ties in the order
of PERSONA_ORDER (strong, specific signals before weak, common ones). Nothing names a supplier or a case:
thresholds come from company.md / SCORECARD.md or are stated first principles.
"""

import json
from collections import defaultdict

from ..contracts import Dossier
from . import estimates

AS_OF = "2026-09-30"  # the data package is frozen at this date (data/README.md)
YEAR_AGO = "2025-09-30"
MAX_EVIDENCE = 15
MAX_DOCS = 12  # most recent documents read per supplier
OTD_TARGET_PCT = 95.0  # company.md: on-time delivery target
LONG_RELATIONSHIP_YEARS = 10  # first principle: a decade of supply is a relationship worth protecting
WEAK_CREDIT = max(limit for limit, _ in estimates.CREDIT_PREMIA)  # Creditreform-style: above this is weak
MIN_PERSONA_SCORE = 2  # one weak signal is not a persona
PERSONA_ORDER = ("cash_short", "full_order_book", "price_fighter", "monopolist", "partner")
LEVERS = {
    "cash_short": ("payment_days", "effective_date"),
    "full_order_book": ("lead_time_days", "volume_per_year", "contract_months"),
    "price_fighter": ("volume_per_year", "contract_months"),
    "monopolist": ("contract_months", "index_clause"),
    "partner": ("contract_months", "volume_per_year"),
}
# news topics in the data -> (persona, points); topics not listed are neutral. A press report about the
# supplier's situation now (3) outweighs a remark in one note (1) and background traits (at most 1 together)
TOPIC_SIGNALS = {
    "credit_watch": ("cash_short", 3),
    "credit_downgrade": ("cash_short", 3),
    "restructuring": ("cash_short", 2),
    "short_time_work": ("price_fighter", 3),
    "free_capacity": ("price_fighter", 3),
    "new_plant": ("price_fighter", 1),
    "full_order_book": ("full_order_book", 3),
    "allocation": ("full_order_book", 3),
    "chip_shortage": ("full_order_book", 2),
    "big_order": ("full_order_book", 1),
    "approvals": ("monopolist", 1),
}
# plain business phrases in letters and notes -> persona (1 point per document)
PHRASE_SIGNALS = {
    "cash_short": ("pay earlier", "bank is watching", "liquidity", "cash flow", "overdue", "faster payment"),
    "full_order_book": ("fully booked", "no free capacity", "full capacity", "allocat", "longer delivery"),
    "price_fighter": ("new customers", "free capacity", "additional parts", "short-time", "on hold"),
    "partner": ("reference customer", "long-standing", "partnership"),
}
FACT_KEYS = ("supplier", "supplier_id")


def build(case: dict, package, supplier_id: str) -> Dossier:
    facts = _fact_lines(case, supplier_id)
    row = _one(package, "SELECT * FROM suppliers WHERE id = ?", supplier_id) if package else None
    if row is None:  # unknown to the data: only what the case itself says
        second = _second_source(case, package, supplier_id) if package else False
        return Dossier(supplier_id, supplier_id, None, "unknown", (), None, second, tuple(facts[:MAX_EVIDENCE]))
    scores: dict[str, int] = defaultdict(int)
    signal_lines, other_notes = _signals(package, supplier_id, row, scores)
    second = _second_source(case, package, supplier_id)
    _structure_signals(case, package, supplier_id, second, scores)
    persona = _persona(scores)
    evidence = [*facts, *signal_lines, *_numbers(case, package, supplier_id, row), *other_notes]
    return Dossier(
        supplier_id=supplier_id,
        name=row["name"],
        country=row["country"],
        persona=persona,
        levers=_levers(scores, persona),
        history_give_pct=_history_give(package, supplier_id),
        second_source=second,
        evidence=tuple(evidence[:MAX_EVIDENCE]),
    )


def _one(package, sql: str, *params) -> dict | None:
    rows = package.query(sql, params, limit=1)
    return rows[0] if rows else None


def _fact_lines(case: dict, supplier_id: str) -> list[str]:
    """Case-only facts (hidden cases) about this supplier, newest first."""
    facts = [
        (fid, f) for fid, f in (case.get("facts") or {}).items() if any(f.get(k) == supplier_id for k in FACT_KEYS)
    ]
    facts.sort(key=lambda item: str(item[1].get("date", "")), reverse=True)
    lines = []
    for fid, fact in facts:
        body = {k: v for k, v in fact.items() if k not in FACT_KEYS}
        lines.append(f"case fact {fid}: {json.dumps(body, ensure_ascii=False, default=str)[:160]}")
    return lines


def _persona(scores: dict[str, int]) -> str:
    best = max(PERSONA_ORDER, key=lambda p: (scores.get(p, 0), -PERSONA_ORDER.index(p)))
    return best if scores.get(best, 0) >= MIN_PERSONA_SCORE else "unknown"


def _levers(scores: dict[str, int], persona: str) -> tuple[str, ...]:
    """The persona's levers first, then those of every other persona with enough signals."""
    ranked = [persona] + [p for p in PERSONA_ORDER if p != persona and scores.get(p, 0) >= MIN_PERSONA_SCORE]
    return tuple(dict.fromkeys(lever for p in ranked for lever in LEVERS.get(p, ())))


def _signals(package, sid: str, row: dict, scores: dict[str, int]) -> tuple[list[str], list[str]]:
    """(signal lines, other notes): news, skonto and the letters and notes that hit a persona phrase score the
    personas and come first; notes without a hit go after the numbers."""
    lines = []
    for n in package.query(
        "SELECT id, date, topic, headline FROM news WHERE supplier_id = ? AND topic != 'noise' ORDER BY date DESC",
        (sid,),
    ):
        if hit := TOPIC_SIGNALS.get(n["topic"]):
            scores[hit[0]] += hit[1]
        lines.append(f"{n['date']} news ({n['topic']}): {n['headline']} ({n['id']})")
    if row.get("skonto_pct"):
        scores["cash_short"] += 2  # it pays to get cash early: its own discount says money is dear to it
        skonto = f"{row['skonto_pct']:g}% skonto within {row['skonto_days']} days, net {row['payment_days']}"
        lines.append(f"It offers {skonto} ({sid})")
    hits, others = _document_lines(package, sid, scores)
    return [*lines, *hits], others


def _document_lines(package, sid: str, scores: dict[str, int]) -> tuple[list[str], list[str]]:
    docs = package.query(
        "SELECT id, date, kind, subject FROM documents WHERE supplier_id = ? ORDER BY date DESC", (sid,), limit=MAX_DOCS
    )
    hit_lines, other_lines = [], []
    for d in docs:
        text = _body(package, d["id"])
        hits = {p for p, phrases in PHRASE_SIGNALS.items() if any(ph in text.lower() for ph in phrases)}
        for persona in hits:
            scores[persona] += 1
        line = f"{d['date']} {d['kind']} '{d['subject']}': {_excerpt(text)} ({d['id']})"
        if hits:
            hit_lines.append(line)
        elif d["kind"] in ("call_note", "meeting_note", "letter"):
            other_lines.append(line)
    return hit_lines, other_lines


def _body(package, doc_id: str) -> str:
    """The document text without its title and header line."""
    try:
        text = package.document(doc_id)
    except (FileNotFoundError, ValueError):
        return ""
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    return " ".join(lines[1:]) if len(lines) > 1 else " ".join(lines)


def _excerpt(text: str, limit: int = 200) -> str:
    """The note's opening, whole words; notes are one or two sentences (no sentence splitting: 'Co.', 'o.o.')."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3].rsplit(" ", 1)[0] + "..."


def _structure_signals(case: dict, package, sid: str, second_source: bool, scores: dict[str, int]) -> None:
    """Persona points from structure: credit, equity, sourcing position, relationship length, ownership."""
    intel = _one(package, "SELECT * FROM supplier_intel WHERE supplier_id = ?", sid) or {}
    ci, ci_before, equity = intel.get("credit_index"), intel.get("credit_index_12m_ago"), intel.get("equity_ratio_pct")
    if ci is not None and ci > WEAK_CREDIT:
        scores["cash_short"] += 1
    if ci is not None and ci_before is not None and ci > ci_before:
        scores["cash_short"] += 1  # a worsening rating: its bank is watching
    if equity is not None and equity < estimates.THIN_EQUITY_PCT:
        scores["cash_short"] += 1
    if not second_source:
        scores["monopolist"] += 1  # we have no qualified alternative for the part
    if case["buyer"].get("category_goal") == "secure_supply":
        scores["monopolist"] += 1  # company.md: bottleneck category, the supplier knows it
    since = _relationship_since(case, package, sid)
    long_relationship = bool(since) and int(AS_OF[:4]) - int(since[:4]) >= LONG_RELATIONSHIP_YEARS
    owner_run = intel.get("owner_type") in ("family", "founder")  # remembers how it was treated (brief, section 2)
    if long_relationship or owner_run:
        scores["partner"] += 1  # background traits: one point together, they are common


def _relationship_since(case: dict, package, sid: str) -> str | None:
    marks = ", ".join("?" for _ in case["parts"])
    row = _one(
        package, f"SELECT MIN(approved_since) AS s FROM sourcing WHERE supplier_id = ? AND part_id IN ({marks})",
        sid, *case["parts"],
    )  # fmt: skip
    return row["s"] if row else None


def _second_source(case: dict, package, sid: str) -> bool:
    """Another active or qualified supplier exists for every part of the case."""
    for part in case["parts"]:
        other = _one(
            package,
            "SELECT supplier_id FROM sourcing "
            "WHERE part_id = ? AND supplier_id != ? AND status IN ('active', 'qualified')",
            part, sid,
        )  # fmt: skip
        if other is None:
            return False
    return bool(case["parts"])


def _history_give(package, sid: str) -> float | None:
    row = _one(
        package,
        "SELECT AVG(asked_pct - settled_pct) AS g FROM negotiation_history "
        "WHERE supplier_id = ? AND asked_pct IS NOT NULL",
        sid,
    )
    return row["g"] if row and row["g"] is not None else None


def _numbers(case: dict, package, sid: str, row: dict) -> list[str]:
    """Factual lines from the numbers, most decision-relevant first."""
    lines = [_credit_line(package, sid), _share_line(package, sid), _payment_line(package, sid)]
    lines += [_scorecard_line(package, sid), _quality_line(package, sid), _contract_line(package, sid)]
    lines += [_history_line(package, sid), *(_forecast_line(package, p) for p in case["parts"])]
    lines += [_sourcing_line(case, package, sid)]
    return [line for line in lines if line]


def _credit_line(package, sid: str) -> str | None:
    i = _one(package, "SELECT * FROM supplier_intel WHERE supplier_id = ?", sid)
    if not i:
        return None
    trend = f", 12 months ago {i['credit_index_12m_ago']}" if i["credit_index_12m_ago"] is not None else ""
    return (
        f"{i['owner_type'] or 'unknown'} {i['legal_form']} founded {i['founded']}, {i['staff']} staff, turnover "
        f"{i['turnover_band']}, equity {i['equity_ratio_pct']}%, credit index {i['credit_index']}{trend} ({sid})"
    )


def _turnover(package, sid: str) -> float | None:
    i = _one(package, "SELECT turnover_eur, turnover_band FROM supplier_intel WHERE supplier_id = ?", sid)
    if not i:
        return None
    if i["turnover_eur"]:
        return float(i["turnover_eur"])
    try:  # small firms file only a band, e.g. "2-5 M EUR": take its middle
        low, high = i["turnover_band"].split()[0].split("-")
        return (float(low) + float(high)) / 2 * 1e6
    except (AttributeError, ValueError, IndexError):
        return None


def _share_line(package, sid: str) -> str | None:
    spend = _one(
        package,
        "SELECT fiscal_year, SUM(spend_eur) AS eur FROM spend_cube WHERE supplier_id = ? "
        "GROUP BY fiscal_year ORDER BY fiscal_year DESC",
        sid,
    )
    turnover = _turnover(package, sid)
    if not spend or not spend["eur"]:
        return None
    share = f" = {spend['eur'] / turnover:.1%} of its turnover" if turnover else ""
    return f"We bought EUR {spend['eur']:,.0f} from it in {spend['fiscal_year']}{share} ({sid})"


def _payment_line(package, sid: str) -> str | None:
    r = _one(
        package,
        "SELECT COUNT(*) AS n, AVG(julianday(paid_date) - julianday(due_date)) AS late, SUM(discount_taken) AS disc, "
        "MAX(id) AS last FROM invoices "
        "WHERE supplier_id = ? AND paid_date IS NOT NULL AND due_date > ? AND due_date <= ?",
        sid, YEAR_AGO, AS_OF,
    )  # fmt: skip
    if not r or not r["n"]:
        return None
    return (
        f"We paid its {r['n']} invoices of the last 12 months {r['late']:+.0f} days vs due on average, "
        f"skonto taken {r['disc'] or 0}x (e.g. {r['last']})"
    )


def _scorecard_line(package, sid: str) -> str | None:
    rows = package.query(
        "SELECT id, otd_pct, ppm, colour, open_8d_overdue FROM scorecards WHERE supplier_id = ? ORDER BY quarter DESC",
        (sid,), limit=5,
    )  # fmt: skip
    if not rows:
        return None
    now, before = rows[0], rows[-1] if len(rows) == 5 else None
    vs_target = "below" if now["otd_pct"] < OTD_TARGET_PCT else "meets"
    trend = f"; a year before {before['otd_pct']}% / {before['ppm']} ppm ({before['id']})" if before else ""
    return (
        f"Scorecard {now['colour']}: OTD {now['otd_pct']}% ({vs_target} our {OTD_TARGET_PCT:g}% target), "
        f"{now['ppm']} ppm, {now['open_8d_overdue']} overdue 8D ({now['id']}){trend}"
    )


def _quality_line(package, sid: str) -> str | None:
    r = _one(
        package,
        "SELECT COUNT(*) AS n, SUM(cost_recharged_eur) AS eur, MAX(id) AS last FROM quality_notifications "
        "WHERE supplier_id = ? AND supplier_caused = 1 AND qn_date > ?",
        sid, YEAR_AGO,
    )  # fmt: skip
    if not r or not r["n"]:
        return None
    return f"{r['n']} supplier-caused complaints in 12 months, EUR {r['eur'] or 0:,.0f} recharged (e.g. {r['last']})"


def _contract_line(package, sid: str) -> str | None:
    c = _one(package, "SELECT * FROM contracts WHERE supplier_id = ? ORDER BY valid_to DESC", sid)
    if not c:
        return None
    index = _one(package, "SELECT id, series FROM contract_clauses WHERE contract_id = ? AND type = 'index'", c["id"])
    clause = f", index clause on {index['series']} ({index['id']})" if index else ", no index clause"
    return (
        f"Framework {c['id']} valid to {c['valid_to']}, {c['notice_days']} days' notice, "
        f"{c['payment_days']} days payment, {c['incoterm']}, price basis {c['price_basis']}{clause}"
    )


def _history_line(package, sid: str) -> str | None:
    rows = package.query(
        "SELECT date, part_id, asked_pct, settled_pct, note FROM negotiation_history "
        "WHERE supplier_id = ? AND asked_pct IS NOT NULL ORDER BY date DESC",
        (sid,), limit=3,
    )  # fmt: skip
    if not rows:
        return None
    past = "; ".join(f"{r['date'][:4]} asked {r['asked_pct']:+g}% settled {r['settled_pct']:+g}%" for r in rows)
    notes = {r["note"] for r in rows if r["note"]}
    return f"Past renewals ({rows[0]['part_id']}): {past}" + (f" - note: {'; '.join(notes)}" if notes else "")


def _forecast_line(package, part_id: str) -> str | None:
    rows = package.query(
        "SELECT id, snapshot_month, qty_next_12m FROM forecasts WHERE part_id = ? ORDER BY snapshot_month DESC",
        (part_id,), limit=5,
    )  # fmt: skip
    if len(rows) < 5 or not rows[-1]["qty_next_12m"]:
        return None
    now, before = rows[0], rows[-1]
    change = now["qty_next_12m"] / before["qty_next_12m"] - 1
    return (
        f"Our demand for {part_id} next 12 months: {now['qty_next_12m']} ({now['id']}), "
        f"{change:+.1%} vs a year earlier ({before['id']})"
    )


def _sourcing_line(case: dict, package, sid: str) -> str | None:
    parts = []
    for part in case["parts"]:
        rows = package.query("SELECT supplier_id, status, quota_pct FROM sourcing WHERE part_id = ?", (part,))
        if rows:
            parts.append(f"{part}: " + ", ".join(f"{r['supplier_id']} {r['status']} {r['quota_pct']}%" for r in rows))
    return f"Sourcing - {'; '.join(parts)}" if parts else None
