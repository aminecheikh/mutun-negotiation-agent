"""The agents' tools: every piece of data and every computation, read-only, callable by name.

Three groups (each agent gets the subset its job needs, see agents.py):

  data          the whole data package: curated views (supplier, documents, history, prices, payments and
                delivery, indices and cost drivers, should-cost, quotes, forecast, contracts) plus generic access
                (list_tables, sql_query, lookup_id, list_ids, read_document) - nothing is out of reach
  thread        the e-mails as written and the attachments' text
  computation   the shared deterministic tools: price band, concession fit, lever trades, verified arguments,
                the cost C of any package, the supplier's offers priced, and the offer engine's counter proposals

Numbers come from code: an agent asks a tool instead of doing arithmetic. Tools never write, never send and never
reach the arena; the live feed (events, screen, leaderboard) is not reachable from here by design.
"""

import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass

from ...cost import cost_breakdown, supplier_value
from ..buyer.contracts import BehaviourRead, Tactic
from . import emails, guard
from . import facts as F
from . import terms as T

ROWS = 40
DOC_CHARS = 6000
AS_OF = "2026-09-30"  # data package README: everything is as of this date


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: dict  # JSON schema "properties"
    required: tuple[str, ...]
    handler: Callable


class ToolContext:
    """What every tool may read for one decision: the case state and a snapshot of the threads."""

    def __init__(self, state: F.CaseState, threads: dict[str, list[dict]], package=None):
        self.state, self.threads, self.package = state, threads, package

    def events(self, supplier_id: str) -> list[dict]:
        if supplier_id not in self.threads:
            raise ValueError(f"no thread with {supplier_id!r}; suppliers in this case: {sorted(self.threads)}")
        return self.threads[supplier_id]

    def rows(self, sql: str, *params, limit: int = ROWS) -> list[dict]:
        if self.package is None:
            return []
        return self.package.query(sql, params, limit)

    def facts(self, supplier_id: str) -> F.ThreadFacts:
        return F.thread_facts(self.state, supplier_id, self.events(supplier_id))


class Toolset:
    """The tools one agent may call: `.schemas` for the model, `.run(name, args)` for the loop."""

    def __init__(self, tools: list[Tool], ctx: ToolContext):
        self.tools, self.ctx = {t.name: t for t in tools}, ctx
        self.schemas = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": {"type": "object", "properties": t.params, "required": list(t.required)},
            }
            for t in tools
        ]

    def run(self, name: str, args: dict):
        tool = self.tools.get(name)
        if tool is None:
            raise ValueError(f"unknown tool {name!r}")
        missing = [r for r in tool.required if r not in args]
        if missing:
            raise ValueError(f"missing argument(s) {missing}")
        clean = {k: v for k, v in args.items() if k in tool.params}
        return _compact(tool.handler(self.ctx, **clean))


def _compact(x):
    """Round floats and drop empty values: shorter results, same information."""
    if isinstance(x, float):
        return None if math.isinf(x) or math.isnan(x) else round(x, 4)
    if isinstance(x, dict):
        return {k: _compact(v) for k, v in x.items() if v not in (None, "", [], {})}
    if isinstance(x, list | tuple):
        return [_compact(v) for v in x]
    return x


SID = {"type": "string", "description": "a supplier ID of this case, e.g. sup:70110"}
PART = {"type": "string", "description": "a part ID, e.g. part:310010"}
TERMS_ITEMS = {
    "type": "array",
    "description": 'terms other than the price: [{"field": "payment_days", "value": "60"}, ...]; values are the '
    'labels listed in the decision frame or "as supplier"; fields you leave out keep the supplier\'s current value',
    "items": {
        "type": "object",
        "properties": {"field": {"type": "string"}, "value": {"type": "string"}},
        "required": ["field", "value"],
    },
}


# ---- data: curated views ------------------------------------------------------------------------------------


def supplier_overview(ctx: ToolContext, supplier_id: str):
    master = ctx.rows(
        "SELECT id, name, country, payment_days, skonto_days, skonto_pct, incoterm, category, "
        "standard_lead_time_days, abc_class, blocked, contract_id FROM suppliers WHERE id = ?",
        supplier_id,
    )
    parts = list(ctx.state.case.get("parts") or ())
    marks = ",".join("?" * len(parts)) or "''"
    dossier = getattr(ctx.state.ctx, "dossiers", {}).get(supplier_id)
    return {
        "as_of": AS_OF,
        "master": master[0] if master else None,
        "public_intel": (ctx.rows("SELECT * FROM supplier_intel WHERE supplier_id = ?", supplier_id) or [None])[0],
        "scorecards_latest_first": ctx.rows(
            "SELECT quarter, otd_pct, otd_confirmed_pct, ppm, qn_count, open_8d_overdue, score, class, colour "
            "FROM scorecards WHERE supplier_id = ? ORDER BY quarter DESC LIMIT 4",
            supplier_id,
        ),
        "our_spend": ctx.rows(
            "SELECT fiscal_year, spend_eur, invoice_count FROM spend_cube WHERE supplier_id = ? "
            "ORDER BY fiscal_year DESC LIMIT 3",
            supplier_id,
        ),
        "its_parts_with_us": ctx.rows(
            "SELECT part_id, status, quota_pct, approved_since, tooling_owner FROM sourcing WHERE supplier_id = ?",
            supplier_id,
        ),
        "approved_sources_for_case_parts": ctx.rows(
            f"SELECT part_id, COUNT(*) AS approved_suppliers FROM sourcing WHERE part_id IN ({marks}) GROUP BY part_id",
            *parts,
        ),
        "rule_based_profile": asdict(dossier) if dossier else None,
    }


def _release_items(ctx: ToolContext, supplier_id: str) -> list[dict]:
    pkg = ctx.package
    if pkg is None or not getattr(pkg, "release", None):
        return []
    out = []
    for prefix in ("doc:", "news:"):
        for ref in pkg.ids(prefix):
            row = pkg.lookup(ref) or {}
            if row.get("root") and row.get("supplier_id") in (supplier_id, None, ""):
                out.append(
                    {k: row.get(k) for k in ("id", "date", "kind", "topic", "subject", "headline") if row.get(k)}
                )
    return out[:20]


def supplier_documents(ctx: ToolContext, supplier_id: str, limit: int = 25):
    case_facts = ctx.state.case.get("facts") or {}
    own = [
        {"id": ref, **{k: v for k, v in fact.items() if k != "body"}}
        for ref, fact in case_facts.items()
        if isinstance(fact, dict) and (fact.get("supplier") in (supplier_id, None) or ref.startswith(("doc:", "news:")))
    ]
    return {
        "as_of": AS_OF,
        "how_to_read": "read_document(id) gives the full text; newest items and this case's own facts weigh most",
        "this_case_facts": own[:20],
        "sunday_release": _release_items(ctx, supplier_id),
        "documents_newest_first": ctx.rows(
            "SELECT id, date, kind, subject, part_ids, asked_pct, asked_price, effective_date, notice_days "
            "FROM documents WHERE supplier_id = ? ORDER BY date DESC LIMIT ?",
            supplier_id,
            min(int(limit), ROWS),
        ),
        "news_about_it": ctx.rows(
            "SELECT id, date, topic, headline FROM news WHERE supplier_id = ? ORDER BY date DESC LIMIT 10", supplier_id
        ),
        "market_news": ctx.rows(
            "SELECT id, date, topic, headline FROM news WHERE supplier_id IS NULL OR supplier_id = '' "
            "ORDER BY date DESC LIMIT 8"
        ),
    }


def negotiation_history(ctx: ToolContext, supplier_id: str):
    rows = ctx.rows(
        "SELECT date, part_id, type, asked_pct, settled_pct, note FROM negotiation_history WHERE supplier_id = ? "
        "ORDER BY date DESC LIMIT 20",
        supplier_id,
    )
    gives = [
        r["asked_pct"] - r["settled_pct"]
        for r in rows
        if r.get("asked_pct") is not None and r.get("settled_pct") is not None
    ]
    return {
        "events_newest_first": rows,
        "summary": {
            "events": len(rows),
            "mean_points_given_from_ask_to_settlement": round(sum(gives) / len(gives), 2) if gives else None,
        },
    }


def price_history(ctx: ToolContext, part_id: str, supplier_id: str = ""):
    where, params = (
        ("part_id = ? AND supplier_id = ?", (part_id, supplier_id)) if supplier_id else ("part_id = ?", (part_id,))
    )
    return {
        "part": (
            ctx.rows(
                "SELECT id, description, category, material_group, grade, net_weight_kg, uom, price_unit, "
                "standard_price, "
                "abc_class FROM parts WHERE id = ?",
                part_id,
            )
            or [None]
        )[0],
        "price_conditions_newest_first": ctx.rows(
            f"SELECT supplier_id, valid_from, valid_to, price, price_unit, basis, event_id FROM price_conditions "
            f"WHERE {where} ORDER BY valid_from DESC LIMIT 15",
            *params,
        ),
        "recent_orders": ctx.rows(
            f"SELECT id, order_date, supplier_id, qty, price, price_unit, price_note FROM po_lines WHERE {where} "
            f"ORDER BY order_date DESC LIMIT 12",
            *params,
        ),
    }


def payment_and_delivery(ctx: ToolContext, supplier_id: str):
    return {
        "as_of": AS_OF,
        "its_terms": (
            ctx.rows(
                "SELECT payment_days, skonto_days, skonto_pct, incoterm, standard_lead_time_days FROM suppliers "
                "WHERE id = ?",
                supplier_id,
            )
            or [None]
        )[0],
        "how_we_pay_it": (
            ctx.rows(
                "SELECT COUNT(*) AS invoices, ROUND(AVG(julianday(paid_date) - julianday(due_date)), 1) "
                "AS avg_days_paid_after_due, "
                "ROUND(AVG(discount_taken), 3) AS share_with_cash_discount FROM invoices WHERE supplier_id = ? "
                "AND paid_date IS NOT NULL AND invoice_date >= '2025-10-01'",
                supplier_id,
            )
            or [None]
        )[0],
        "its_delivery": (
            ctx.rows(
                "SELECT COUNT(*) AS order_lines, "
                "ROUND(AVG(julianday(first_confirmed_date) - julianday(requested_date)), 1) "
                "AS avg_days_confirmed_after_requested FROM po_lines WHERE supplier_id = ? "
                "AND first_confirmed_date IS NOT NULL "
                "AND order_date >= '2025-10-01'",
                supplier_id,
            )
            or [None]
        )[0],
        "its_quality_last_12_months": (
            ctx.rows(
                "SELECT COUNT(*) AS complaints, SUM(complaint_qty) AS defective_qty, ROUND(SUM(cost_recharged_eur), 0) "
                "AS recharged_eur, SUM(CASE WHEN d8_date IS NULL THEN 1 ELSE 0 END) AS open_8d "
                "FROM quality_notifications "
                "WHERE supplier_id = ? AND qn_date >= '2025-10-01'",
                supplier_id,
            )
            or [None]
        )[0],
    }


def _change(ctx: ToolContext, series_id: str) -> dict | None:
    values = ctx.rows(
        "SELECT month, value FROM index_values WHERE series_id = ? ORDER BY month DESC LIMIT 13", series_id
    )
    if len(values) < 2:
        return None
    latest, earlier = values[0], values[-1] if len(values) == 13 else values[-1]
    pct = (latest["value"] / earlier["value"] - 1) * 100 if earlier["value"] else None
    return {
        "series": series_id,
        "from": earlier["month"],
        "to": latest["month"],
        "change_pct": round(pct, 2) if pct is not None else None,
        "ids": [f"idx:{series_id}@{earlier['month']}", f"idx:{series_id}@{latest['month']}"],
    }


def market_index(ctx: ToolContext, series_id: str, months: int = 24):
    series_id = series_id.removeprefix("idx:").split("@")[0]
    meta = ctx.rows(
        "SELECT id, name, unit, source, frequency, last_month, formula FROM index_series WHERE id = ?", series_id
    )
    return {
        "series": meta[0] if meta else None,
        "twelve_month_change": _change(ctx, series_id),
        "values_newest_first": ctx.rows(
            "SELECT month, value FROM index_values WHERE series_id = ? ORDER BY month DESC LIMIT ?",
            series_id,
            max(1, min(int(months), 36)),
        ),
    }


def cost_drivers(ctx: ToolContext, part_id: str):
    drivers = ctx.rows("SELECT series_id, weight, source FROM cost_drivers WHERE part_id = ?", part_id)
    out, weighted, covered = [], 0.0, 0.0
    for d in drivers:
        change = _change(ctx, d["series_id"])
        out.append({**d, "twelve_month_change": change})
        if change and change["change_pct"] is not None and d.get("weight"):
            weighted += d["weight"] * change["change_pct"]
            covered += d["weight"]
    return {
        "drivers": out,
        "weighted_change_pct_of_driven_cost": round(weighted, 2) if covered else None,
        "share_of_cost_covered_by_drivers": round(covered, 3) if covered else None,
        "note": "weights are our category team's estimate of the part's cost structure",
    }


def should_cost(ctx: ToolContext, part_id: str):
    return {"estimates": ctx.rows("SELECT * FROM should_cost WHERE part_id = ? ORDER BY estimate_date DESC", part_id)}


def part_quotes(ctx: ToolContext, part_id: str):
    return {
        "note": "internal benchmarks only: never cite or mention another supplier's quote or offer to a supplier",
        "rfqs": ctx.rows(
            "SELECT id, rfq_date, qty_per_year, purpose, status FROM rfqs WHERE part_id = ? "
            "ORDER BY rfq_date DESC LIMIT 6",
            part_id,
        ),
        "quotes_newest_first": ctx.rows(
            "SELECT id, quote_date, supplier_id, unit_price, price_unit, lead_time_days, incoterm, payment_days, "
            "tooling_eur, awarded, remarks FROM quotes WHERE part_id = ? ORDER BY quote_date DESC LIMIT 10",
            part_id,
        ),
    }


def demand_forecast(ctx: ToolContext, part_id: str):
    snaps = ctx.rows(
        "SELECT id, snapshot_month, horizon_months, qty_next_12m, uom, shared_with_suppliers FROM forecasts "
        "WHERE part_id = ? ORDER BY snapshot_month DESC LIMIT 4",
        part_id,
    )
    lines = ctx.rows("SELECT target_month, qty FROM forecast_lines WHERE fc_id = ? ORDER BY target_month LIMIT 12",
                     snaps[0]["id"]) if snaps else []  # fmt: skip
    return {"snapshots_newest_first": snaps, "latest_monthly": lines}


def contract_terms(ctx: ToolContext, supplier_id: str):
    contracts = ctx.rows(
        "SELECT id, type, valid_from, valid_to, payment_days, incoterm, notice_days, price_basis FROM contracts "
        "WHERE supplier_id = ? ORDER BY valid_to DESC LIMIT 5",
        supplier_id,
    )
    clauses = []
    for c in contracts[:3]:
        for cl in ctx.rows("SELECT id, clause, type, series, material_share, deadband_pct, days, text "
            "FROM contract_clauses "
                           "WHERE contract_id = ? LIMIT 12", c["id"]):  # fmt: skip
            cl["text"] = str(cl.get("text") or "")[:240]
            clauses.append(cl)
    return {"contracts": contracts, "clauses": clauses}


# ---- data: generic access -------------------------------------------------------------------------------------


def list_tables(ctx: ToolContext):
    return ctx.package.tables() if ctx.package is not None else {}


def sql_query(ctx: ToolContext, sql: str):
    if ctx.package is None:
        raise ValueError("no data package loaded")
    return {"rows": ctx.package.query(sql, (), ROWS), "max_rows": ROWS}


def lookup_id(ctx: ToolContext, id: str):  # noqa: A002 - the tool's public argument name
    own = (ctx.state.case.get("facts") or {}).get(id)
    if own is not None:
        return {"id": id, "source": "this case", "fact": own}
    row = ctx.package.lookup(id) if ctx.package is not None else None
    if row is None:
        raise ValueError(f"unknown ID {id!r}")
    return {"id": id, "row": {k: (str(v)[:600] if isinstance(v, str) else v) for k, v in row.items()}}


def list_ids(ctx: ToolContext, prefix: str):
    ids = ctx.package.ids(prefix) if ctx.package is not None else []
    own = [k for k in (ctx.state.case.get("facts") or {}) if k.startswith(prefix)]
    return {"ids": (own + ids)[:200], "total": len(own) + len(ids)}


def read_document(ctx: ToolContext, ref: str):
    own = (ctx.state.case.get("facts") or {}).get(ref)
    if isinstance(own, dict):
        text = own.get("body") or own.get("text") or json.dumps(own, ensure_ascii=False)
        return {"ref": ref, "source": "this case", "text": str(text)[:DOC_CHARS]}
    if ctx.package is None:
        raise ValueError("no data package loaded")
    return {"ref": ref, "text": str(ctx.package.document(ref))[:DOC_CHARS]}


def case_file(ctx: ToolContext):
    case = ctx.state.case
    return {
        k: case.get(k)
        for k in (
            "case_id",
            "type",
            "title",
            "briefing",
            "trigger_doc",
            "parts",
            "suppliers",
            "negotiable",
            "fixed_terms",
            "term_options",
            "buyer",
            "round_limit",
            "refs",
        )
    }


# ---- thread ----------------------------------------------------------------------------------------------------


def thread_transcript(ctx: ToolContext, supplier_id: str, last_n: int = 0):
    return emails.transcript(ctx.events(supplier_id), int(last_n) or None)


def read_attachment(ctx: ToolContext, supplier_id: str, name: str):
    wanted = name.lower().strip()
    for m in reversed(ctx.events(supplier_id)):
        for att in m.get("attachments") or []:
            if wanted in str(att.get("name") or "").lower():
                return {"name": att.get("name"), "text": str(att.get("text") or "(not readable)")[:8000]}
    raise ValueError(f"no attachment named like {name!r} in this thread")


# ---- computation ------------------------------------------------------------------------------------------------


def _sheet_view(state: F.CaseState, s: F.Sheet) -> dict:
    return {"id": s.id, "terms": T.describe(s.terms), "c_eur": s.c}


def supplier_offers(ctx: ToolContext, supplier_id: str):
    f = ctx.facts(supplier_id)
    st = ctx.state
    return {
        "status": f.status,
        "messages_left": f.moves_left,
        "standing_sheet": _sheet_view(st, f.standing) if f.standing else None,
        "its_other_sheets": [_sheet_view(st, s) for s in f.alternatives],
        "standing_acceptable": f.acceptable,
        "why_not_acceptable": f.why_not,
        "it_accepted_our_offer": f.accepted_ours,
        "our_offer_on_the_table": [_sheet_view(st, s) for s in f.our_offer],
        "its_prices_so_far": list(f.supplier_prices),
        "our_prices_so_far": list(f.our_prices),
        "best_opening_c_eur": st.c_open,
        "alternative_c_eur": st.c_alt,
    }


def price_package(ctx: ToolContext, supplier_id: str, unit_price: float, terms: list | None = None):
    st, f = ctx.state, ctx.facts(supplier_id)
    standing = f.standing.terms if f.standing else {}
    sheet, problems = T.parse_sheet(st.case, unit_price, terms or [], standing, f.std)
    if sheet.get("unit_price") is None:
        raise ValueError("; ".join(problems))
    full = guard.normalise(st, sheet, f) or sheet
    c = F.cost(st, supplier_id, full, f.std)
    breakdown = cost_breakdown(st.case, full, f.std, st.estimates.get(supplier_id))
    est = st.estimates.get(supplier_id)
    margin = None
    if est and est.get("full_cost"):
        try:
            worth = supplier_value(st.case, full, est)
            margin = round((worth - est["full_cost"]) / worth * 100, 1) if worth else None
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            margin = None
    check = guard.check_counter(st, f, [full])
    return {
        "sheet": T.describe(full),
        "c_eur": c,
        "cost_breakdown_eur": {k: round(v) for k, v in breakdown.items() if v},
        "vs_its_standing_eur": None if not f.standing or math.isinf(f.standing.c) else round(c - f.standing.c),
        "vs_our_current_offer_eur": None if math.isinf(f.our_last_c) else round(c - f.our_last_c),
        "estimated_supplier_margin_pct": margin,
        "value_problems": problems,
        "as_a_counter": {
            "allowed": bool(check.sheets),
            "sent_as": T.describe(check.sheets[0]) if check.sheets else None,
            "corrections": check.notes,
        },
    }


def propose_counter(ctx: ToolContext, supplier_id: str, levers: list | None = None):
    st, events = ctx.state, ctx.events(supplier_id)
    f = ctx.facts(supplier_id)
    chosen = tuple(str(x) for x in (levers or ()) if str(x) in st.negotiable) or ("unit_price",)
    read = BehaviourRead(supplier_id, 0.5, 0.2, (), ())
    plan = st.tools.plan_offers(st.ctx, supplier_id, events, Tactic(supplier_id, "send", chosen), read)
    check = guard.check_counter(st, f, list(plan.term_sheets))
    return {
        "levers": list(chosen),
        "engine_rationale": plan.rationale,
        "sheets": [{"terms": T.describe(s), "c_eur": F.cost(st, supplier_id, s, f.std)} for s in check.sheets],
        "corrections": check.notes,
    }


def price_band(ctx: ToolContext, supplier_id: str):
    band = getattr(ctx.state.ctx, "bands", {}).get(supplier_id)
    cred = getattr(ctx.state.ctx, "credibility", {}).get(supplier_id)
    out = asdict(band) if band else {"note": "no band: no price from this supplier yet"}
    if band:
        out["target_terms"] = T.describe(band.target_terms) if band.target_terms else None
    out["credibility"] = asdict(cred) if cred else None
    out["how_to_read"] = (
        "floor = estimated full cost (it never sells below); fair = today's price moved by its cost drivers; target "
        "= the cheapest package our model thinks it could accept (optimistic); ceiling = the most we would pay; "
        "credibility.lowest_price = the lowest price an offer may carry and still be taken seriously"
    )
    return out


def concession_fit(ctx: ToolContext, supplier_id: str):
    fit = getattr(ctx.state.ctx, "fits", {}).get(supplier_id)
    f = ctx.facts(supplier_id)
    return {
        "fit": asdict(fit) if fit else None,
        "its_prices": list(f.supplier_prices),
        "our_prices": list(f.our_prices),
        "how_to_read": "reservation_estimate = where its concessions converge; p_room = probability it can still go "
        "at least 1 % lower; shrinking = each step smaller than the last (it is near its limit)",
    }


def lever_trades(ctx: ToolContext, supplier_id: str):
    trades = getattr(ctx.state.ctx, "trades", {}).get(supplier_id, ())
    return {
        "trades": [
            {
                "field": t.field,
                "its_value": T.label(t.theirs),
                "ask_for": T.label(t.ours),
                "saves_eur": round(t.eur),
                "worth_per_unit_eur": round(t.per_unit, 3),
            }
            for t in trades[:12]
        ],
        "how_to_read": "each single term change against its quote that saves us money at the same price; a trade it "
        "also values (its wants) is the cheapest to get",
    }


def verified_arguments(ctx: ToolContext, supplier_id: str):
    return {
        "arguments": [asdict(a) for a in F.arguments(ctx.state, supplier_id)],
        "how_to_use": "cite at most 3 per e-mail by token (e.g. [[A1]]); each is a finished sentence whose figure and "
        "IDs the arena checks - it can only come back verified",
    }


# ---- the catalogue ---------------------------------------------------------------------------------------------

CATALOGUE: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool(
            "supplier_overview",
            "The supplier at a glance from the data package: master data (payment terms, "
            "cash discount, standard lead time), public intel (owner type, staff, turnover, equity ratio, credit "
            "index now and 12 months ago), the last four quarterly scorecards (on-time delivery, ppm, open 8Ds), our "
            "spend with it, its parts with us (quota, tooling owner), how many approved sources the case parts have, "
            "and the rule-based profile (persona, levers, evidence lines with IDs).",
            {"supplier_id": SID},
            ("supplier_id",),
            supplier_overview,
        ),
        Tool(
            "supplier_documents",
            "Everything written about or by the supplier, newest first: letters, e-mails, "
            "meeting and call notes (documents), press news about it, general market news, this case's own new "
            "facts and the Sunday release. Lists IDs, dates and subjects; use read_document for the full text.",
            {"supplier_id": SID, "limit": {"type": "integer", "description": "max documents (default 25)"}},
            ("supplier_id",),
            supplier_documents,
        ),
        Tool(
            "negotiation_history",
            "Past price events with the supplier (renewals, increases): what it asked, "
            "where it settled, and the note explaining why - the best evidence of how far it usually gives.",
            {"supplier_id": SID},
            ("supplier_id",),
            negotiation_history,
        ),
        Tool(
            "price_history",
            "A part's price history (price conditions with the event behind each) and its recent "
            "order lines with prices, optionally for one supplier; plus the part master (material, weight, "
            "price unit).",
            {"part_id": PART, "supplier_id": {"type": "string", "description": "optional supplier ID"}},
            ("part_id",),
            price_history,
        ),
        Tool(
            "payment_and_delivery",
            "Last 12 months: how we pay the supplier (days after due, cash discount taken), "
            "how it delivers (days confirmed after requested), its complaints, recharged costs and open 8Ds, and its "
            "standard terms - cash pressure and capacity show up here.",
            {"supplier_id": SID},
            ("supplier_id",),
            payment_and_delivery,
        ),
        Tool(
            "market_index",
            "One market index series (e.g. PPI-C2451, LCI-C, CU-NOTATION): metadata, monthly values "
            "and its 12-month change with the two citable idx IDs.",
            {"series_id": {"type": "string"}, "months": {"type": "integer", "description": "how many months (max 36)"}},
            ("series_id",),
            market_index,
        ),
        Tool(
            "cost_drivers",
            "A part's cost drivers (index series and weights, our category team's estimate) with "
            "each series' 12-month change and the weighted change: what the market justifies for this part.",
            {"part_id": PART},
            ("part_id",),
            cost_drivers,
        ),
        Tool(
            "should_cost",
            "Bottom-up should-cost estimates for a part (material, conversion, overhead, confidence).",
            {"part_id": PART},
            ("part_id",),
            should_cost,
        ),
        Tool(
            "part_quotes",
            "Past RFQs and supplier quotes for a part (internal benchmark of the market price). Never "
            "cite or mention another supplier's quote or offer in an e-mail.",
            {"part_id": PART},
            ("part_id",),
            part_quotes,
        ),
        Tool(
            "demand_forecast",
            "The part's demand forecast snapshots (next 12 months) and the latest monthly lines.",
            {"part_id": PART},
            ("part_id",),
            demand_forecast,
        ),
        Tool(
            "contract_terms",
            "The supplier's framework contracts and clauses (index clauses, notice periods, "
            "payment terms, price basis).",
            {"supplier_id": SID},
            ("supplier_id",),
            contract_terms,
        ),
        Tool(
            "list_tables",
            "All tables of the data package with columns and row counts (for sql_query).",
            {},
            (),
            list_tables,
        ),
        Tool(
            "sql_query",
            "One read-only SQL query (SELECT or WITH) on the data package (SQLite), max 40 rows. Use when "
            "no curated tool answers the question.",
            {"sql": {"type": "string"}},
            ("sql",),
            sql_query,
        ),
        Tool(
            "lookup_id",
            "The fact behind any citable ID (this case's facts first, then the package), e.g. "
            "sc:70110@2026Q3, po:4500010001-10, doc:..., news:...",
            {"id": {"type": "string"}},
            ("id",),
            lookup_id,
        ),
        Tool(
            "list_ids",
            "All citable IDs starting with a prefix, e.g. 'idx:LCI-C@' or 'doc:2026'.",
            {"prefix": {"type": "string"}},
            ("prefix",),
            list_ids,
        ),
        Tool(
            "read_document",
            "The full text of a letter, e-mail, meeting or call note, news item or contract by ID.",
            {"ref": {"type": "string"}},
            ("ref",),
            read_document,
        ),
        Tool(
            "case_file",
            "The whole case file: briefing, need, rates, category goal, must-haves, alternative, "
            "negotiable fields and options, fixed terms, round limit.",
            {},
            (),
            case_file,
        ),
        Tool(
            "thread_transcript",
            "The e-mail thread with a supplier as written, oldest first (or only the last N).",
            {"supplier_id": SID, "last_n": {"type": "integer"}},
            ("supplier_id",),
            thread_transcript,
        ),
        Tool(
            "read_attachment",
            "The full text of an attachment in the thread (quote PDF, cost breakdown XLSX, 8D "
            "report, delivery schedule) by (part of) its file name.",
            {"supplier_id": SID, "name": {"type": "string"}},
            ("supplier_id", "name"),
            read_attachment,
        ),
        Tool(
            "supplier_offers",
            "The supplier's current position, priced: its standing sheet (the only one we can "
            "accept) and other sheets with their total cost C, whether the standing sheet is acceptable and why not, "
            "whether it accepted our offer, our offer on the table, both price paths, the best opening C and our "
            "alternative's C.",
            {"supplier_id": SID},
            ("supplier_id",),
            supplier_offers,
        ),
        Tool(
            "price_package",
            "The total cost C (EUR, the score's measure) of any package with this supplier, by "
            "component; the difference to its standing sheet and to our current offer; the supplier's "
            "estimated margin; "
            "and whether it may be sent as a counter (with the corrections the hard rules would make).",
            {"supplier_id": SID, "unit_price": {"type": "number"}, "terms": TERMS_ITEMS},
            ("supplier_id", "unit_price"),
            price_package,
        ),
        Tool(
            "propose_counter",
            "The offer engine's counter for this round given the levers you want to trade: 1-3 "
            "full sheets (a main sheet plus equal-cost variants changing one term each) with their C, already "
            "checked against the hard rules. The engine paces concessions (anchor, Boulware curve, reciprocity).",
            {
                "supplier_id": SID,
                "levers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "negotiable fields to trade, e.g. payment_days",
                },
            },
            ("supplier_id",),
            propose_counter,
        ),
        Tool(
            "price_band",
            "Where the price can land with this supplier: floor (its estimated full cost), fair price "
            "(cost drivers), target package, ceiling, their C, how each was derived, and the lowest credible price.",
            {"supplier_id": SID},
            ("supplier_id",),
            price_band,
        ),
        Tool(
            "concession_fit",
            "The supplier's own price path fitted: where its concessions converge, its last step, "
            "whether steps shrink, and the probability it can still go at least 1 % lower.",
            {"supplier_id": SID},
            ("supplier_id",),
            concession_fit,
        ),
        Tool(
            "lever_trades",
            "Each single term change (payment days, lead time, volume, contract length, dates, "
            "Incoterm, clauses, quality) that saves us money against its quote, in EUR and per unit.",
            {"supplier_id": SID},
            ("supplier_id",),
            lever_trades,
        ),
        Tool(
            "verified_arguments",
            "Data arguments we may still cite to this supplier: finished sentences whose "
            "figures and IDs are checked against the data the arena uses, with kind and strength, by token.",
            {"supplier_id": SID},
            ("supplier_id",),
            verified_arguments,
        ),
    ]
}


def toolset(names: list[str], ctx: ToolContext) -> Toolset:
    return Toolset([CATALOGUE[n] for n in names if n in CATALOGUE], ctx)
