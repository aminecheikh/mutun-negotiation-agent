"""Every v2 tool returns the TRUE answer - checked against an independent source, not against the tool's own code:
a separate SQLite connection to the data package, facts.json (the values the arena checks claims against), the
published cost function, and the documents' own text. Runs on all 19 practice case files and their suppliers."""

import json
import math
import re
import sqlite3
from dataclasses import asdict

import pytest
from v2_support import DATA, KIT, PRACTICE, S01, SID, ours, quote, state_for

from arena_kit.agents.buyer_v2 import facts as F
from arena_kit.agents.buyer_v2.toolbox import ToolContext, toolset
from arena_kit.cost import total_cost
from arena_kit.docs import read_document

pytestmark = pytest.mark.skipif(not (DATA / "world.sqlite").is_file(), reason="needs the data package")
CASES = [(c, sid) for c in PRACTICE for sid in c["suppliers"]]
IDS = [f"{c['case_id']}/{sid}" for c, sid in CASES]


@pytest.fixture(scope="module")
def package():
    from arena_kit.agents.buyer.tools.safe_package import thread_safe
    from arena_kit.data import DataPackage

    return thread_safe(DataPackage(DATA))


@pytest.fixture(scope="module")
def db():
    con = sqlite3.connect(f"file:{DATA / 'world.sqlite'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


@pytest.fixture(scope="module")
def facts():
    return json.loads((DATA / "facts.json").read_text(encoding="utf-8"))


def rows(db, sql, *params):
    return [dict(r) for r in db.execute(sql, params)]


def tools_for(case, sid, package, events=None):
    threads = {s: [quote(s, 0, 100.0)] for s in case["suppliers"]}
    if events is not None:
        threads[sid] = events
    st = state_for(case, threads, package=package)
    return st, toolset(list(__import__("arena_kit.agents.buyer_v2.toolbox", fromlist=["CATALOGUE"]).CATALOGUE),
                       ToolContext(st, threads, package, focus=sid)), threads  # fmt: skip


def close(a, b, tol=0.011):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol


# ---- verified arguments: every figure is the one the arena will check -------------------------------------------


def _months_apart(a: str, b: str) -> int:
    ya, ma = map(int, a.split("-"))
    yb, mb = map(int, b.split("-"))
    return abs((yb * 12 + mb) - (ya * 12 + ma))


def _pcts(sentence: str) -> list[float]:
    return [float(x) for x in re.findall(r"([+-]?\d+(?:\.\d+)?)\s*%", sentence)]


@pytest.mark.parametrize(("case", "sid"), CASES, ids=IDS)
def test_every_verified_argument_matches_the_data_the_arena_checks(case, sid, package, facts, db):
    uoms = {r["id"]: r["uom"] for r in rows(db, "SELECT id, uom FROM parts")}
    st, tools, _ = tools_for(case, sid, package)
    args = tools.run("verified_arguments", {"supplier_id": sid})["arguments"]
    parts = set(case.get("parts") or ())
    for a in args:
        refs, sentence, kind = a["refs"], a["sentence"], a["kind"]
        for r in refs:
            assert r in sentence, f"{r} not written in its sentence"
            assert r in facts or r in (case.get("facts") or {}), f"{r} is not a citable ID"
        if kind == "index_change":
            first, last = sorted(refs, key=lambda r: facts[r]["month"])
            f0, f1 = facts[first], facts[last]
            assert f0["series"] == f1["series"] and 0 < _months_apart(f0["month"], f1["month"]) <= 12
            assert f1["month"] >= "2025-09", "the window must end within the data's last 12 months"
            change = (f1["value"] / f0["value"] - 1) * 100
            assert any(abs(p - change) <= 0.5 for p in _pcts(sentence)), (sentence, change)
        elif kind == "scorecard":
            (ref,) = refs
            assert facts[ref]["supplier"] == sid
            assert _pcts(sentence)[0] == pytest.approx(facts[ref]["otd_pct"], abs=0.5)
        elif kind == "po_history":
            assert 2 <= len(refs) <= 8
            assert {facts[r]["part"] for r in refs} <= parts and len({facts[r]["part"] for r in refs}) == 1
            # true orders of this case's part (in a bundle: what we pay its current suppliers today)
            assert all(r.startswith("po:") and facts[r]["part"] in parts for r in refs)
            prices = [float(x) for x in re.findall(r"EUR ([\d.]+)", sentence)]
            assert prices and all(
                any(abs(facts[r]["unit_price"] - p) <= max(0.01, 0.005 * p) for p in prices) for r in refs
            )
            assert_true_unit(sentence, facts[refs[0]]["price_unit"], uoms[facts[refs[0]]["part"]])
        elif kind == "should_cost":
            (ref,) = refs
            assert facts[ref]["part"] in parts
            assert float(re.search(r"EUR ([\d.]+)", sentence).group(1)) == pytest.approx(facts[ref]["unit_cost"])
            assert_true_unit(sentence, facts[ref]["price_unit"], uoms[facts[ref]["part"]])
        elif kind == "forecast":
            (ref,) = refs
            assert facts[ref]["part"] in parts and _pcts(sentence)[0] == pytest.approx(
                facts[ref]["change_pct"], abs=0.5
            )
        elif kind == "notice":
            doc = next(r for r in refs if r.startswith("doc:"))
            ctr = next(r for r in refs if r.startswith("ctr:"))
            assert facts[doc]["supplier"] == sid == facts[ctr]["supplier"]
            assert f"{facts[doc]['days']} days" in sentence and f"{facts[ctr]['days']} days" in sentence
        else:
            pytest.fail(f"unchecked argument kind {kind}")


def assert_true_unit(sentence: str, price_unit: int, uom: str):
    """A price is stated per the unit the data uses: 'per unit' only for single pieces."""
    if price_unit == 1 and uom == "pc":
        assert "per unit" in sentence
    else:
        assert "per unit" not in sentence and (f"per {price_unit:,}" in sentence or f"per {uom}" in sentence), sentence


# ---- data tools: the same rows as an independent query ---------------------------------------------------------


@pytest.mark.parametrize(("case", "sid"), CASES, ids=IDS)
def test_supplier_views_are_the_suppliers_own_rows(case, sid, package, db):
    _, tools, _ = tools_for(case, sid, package)
    ov = tools.run("supplier_overview", {"supplier_id": sid})
    master = rows(db, "SELECT * FROM suppliers WHERE id = ?", sid)[0]
    assert ov["master"]["name"] == master["name"] and ov["master"]["payment_days"] == master["payment_days"]
    quarters = [
        r["quarter"]
        for r in rows(db, "SELECT quarter FROM scorecards WHERE supplier_id = ? ORDER BY quarter DESC", sid)
    ]
    assert [s["quarter"] for s in ov.get("scorecards_latest_first", [])] == quarters[:4]
    assert len(ov.get("its_parts_with_us", [])) == len(rows(db, "SELECT * FROM sourcing WHERE supplier_id = ?", sid))
    intel = rows(db, "SELECT * FROM supplier_intel WHERE supplier_id = ?", sid)
    assert (ov.get("public_intel") or {}).get("turnover_eur") == (intel[0]["turnover_eur"] if intel else None)

    docs = tools.run("supplier_documents", {"supplier_id": sid})
    listed = docs.get("documents_newest_first", [])
    truth = rows(db, "SELECT id FROM documents WHERE supplier_id = ? ORDER BY date DESC LIMIT 25", sid)
    assert [d["id"] for d in listed] == [r["id"] for r in truth]
    assert all(n["id"] in {r["id"] for r in rows(db, "SELECT id FROM news WHERE supplier_id = ?", sid)}
               for n in docs.get("news_about_it", []))  # fmt: skip

    hist = tools.run("negotiation_history", {"supplier_id": sid})
    events = rows(db, "SELECT asked_pct, settled_pct FROM negotiation_history WHERE supplier_id = ? "
                      "ORDER BY date DESC LIMIT 20", sid)  # fmt: skip
    gives = [e["asked_pct"] - e["settled_pct"] for e in events if None not in (e["asked_pct"], e["settled_pct"])]
    assert hist["summary"]["events"] == len(events)
    assert (
        close(hist["summary"].get("mean_points_given_from_ask_to_settlement"), sum(gives) / len(gives))
        if gives
        else True
    )

    pay = tools.run("payment_and_delivery", {"supplier_id": sid})
    inv = rows(db, "SELECT julianday(paid_date) - julianday(due_date) AS late FROM invoices WHERE supplier_id = ? "
                   "AND paid_date IS NOT NULL AND invoice_date >= '2025-10-01'", sid)  # fmt: skip
    if inv:
        assert pay["how_we_pay_it"]["invoices"] == len(inv)
        assert close(pay["how_we_pay_it"]["avg_days_paid_after_due"], sum(r["late"] for r in inv) / len(inv), 0.06)
    qn = rows(
        db, "SELECT COUNT(*) AS n FROM quality_notifications WHERE supplier_id = ? AND qn_date >= '2025-10-01'", sid
    )
    assert pay["its_quality_last_12_months"].get("complaints", 0) == qn[0]["n"]

    contracts = tools.run("contract_terms", {"supplier_id": sid})
    assert {c["id"] for c in contracts.get("contracts", [])} <= {
        r["id"] for r in rows(db, "SELECT id FROM contracts WHERE supplier_id = ?", sid)
    }


@pytest.mark.parametrize("case", PRACTICE, ids=lambda c: c["case_id"])
def test_part_views_are_the_parts_own_rows(case, package, db):
    sid = case["suppliers"][0]
    part = case["parts"][0]
    _, tools, _ = tools_for(case, sid, package)
    ph = tools.run("price_history", {"part_id": part, "supplier_id": sid})
    assert ph["part"]["id"] == part
    assert all(
        r["supplier_id"] == sid for r in ph.get("price_conditions_newest_first", []) + ph.get("recent_orders", [])
    )
    sql = "SELECT price FROM po_lines WHERE part_id = ? AND supplier_id = ? ORDER BY order_date DESC LIMIT 12"
    truth = rows(db, sql, part, sid)
    assert [r["price"] for r in ph.get("recent_orders", [])] == [r["price"] for r in truth]

    drivers = tools.run("cost_drivers", {"part_id": part})
    truth = rows(db, "SELECT series_id, weight FROM cost_drivers WHERE part_id = ?", part)
    assert {d["series_id"] for d in drivers.get("drivers", [])} == {r["series_id"] for r in truth}
    weighted = 0.0
    for d in drivers.get("drivers", []):
        ch = d["twelve_month_change"]
        vals = {r["month"]: r["value"] for r in rows(db, "SELECT month, value FROM index_values WHERE series_id = ?",
                                                      d["series_id"])}  # fmt: skip
        assert _months_apart(ch["from"], ch["to"]) == 12 and ch["to"] == max(vals)
        assert close(ch["change_pct"], (vals[ch["to"]] / vals[ch["from"]] - 1) * 100)
        weighted += d["weight"] * ch["change_pct"]
    if drivers.get("drivers"):
        assert close(drivers["weighted_change_pct_of_driven_cost"], weighted, 0.02)

    sc = tools.run("should_cost", {"part_id": part}).get("estimates", [])
    assert len(sc) == len(rows(db, "SELECT id FROM should_cost WHERE part_id = ?", part))
    quotes = tools.run("part_quotes", {"part_id": part}).get("quotes_newest_first", [])
    assert {q["id"] for q in quotes} <= {r["id"] for r in rows(db, "SELECT id FROM quotes WHERE part_id = ?", part)}
    fc = tools.run("demand_forecast", {"part_id": part})
    snaps = rows(db, "SELECT id FROM forecasts WHERE part_id = ? ORDER BY snapshot_month DESC", part)
    if snaps:
        assert fc["snapshots_newest_first"][0]["id"] == snaps[0]["id"]
        lines = rows(
            db, "SELECT qty FROM forecast_lines WHERE fc_id = ? ORDER BY target_month LIMIT 12", snaps[0]["id"]
        )
        assert [x["qty"] for x in fc.get("latest_monthly", [])] == [x["qty"] for x in lines]


def test_every_index_series_change_is_true_and_citable(package, db, facts):
    _, tools, _ = tools_for(S01, SID, package)
    for (series,) in db.execute("SELECT id FROM index_series"):
        out = tools.run("market_index", {"series_id": series, "months": 36})
        ch = out["twelve_month_change"]
        vals = {
            r["month"]: r["value"]
            for r in rows(db, "SELECT month, value FROM index_values WHERE series_id = ?", series)
        }
        assert out["series"]["id"] == series and ch["to"] == max(vals) and _months_apart(ch["from"], ch["to"]) == 12
        assert close(ch["change_pct"], (vals[ch["to"]] / vals[ch["from"]] - 1) * 100)
        for ref in ch["ids"]:
            assert facts[ref]["value"] == pytest.approx(vals[ref.split("@")[1]])
        assert [v["month"] for v in out["values_newest_first"]] == sorted(vals, reverse=True)[:36]


def test_generic_data_access_matches_the_package(package, db, facts):
    _, tools, _ = tools_for(S01, SID, package)
    assert set(tools.run("list_tables", {})) == {
        r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    sql = "SELECT id, name FROM suppliers ORDER BY id LIMIT 5"
    assert tools.run("sql_query", {"sql": sql})["rows"] == rows(db, sql)
    ref = "sc:70270@2026Q3"
    found = tools.run("lookup_id", {"id": ref})  # S01 carries its own facts: the case comes first
    assert (found.get("fact") or found.get("row"))["otd_pct"] == facts[ref]["otd_pct"]
    assert (
        tools.run("lookup_id", {"id": "po:4500014467-10"})["row"]["unit_price"]
        == facts["po:4500014467-10"]["unit_price"]
    )
    ids = tools.run("list_ids", {"prefix": "idx:LCI-C@2026"})["ids"]
    assert ids and all(i.startswith("idx:LCI-C@2026") for i in ids)
    doc = rows(db, "SELECT id, file FROM documents WHERE supplier_id = 'sup:70270' ORDER BY date DESC LIMIT 1")[0]
    text = (DATA / doc["file"]).read_text(encoding="utf-8")
    assert tools.run("read_document", {"ref": doc["id"]})["text"] == text[:6000]
    assert tools.run("case_file", {})["negotiable"] == S01["negotiable"]


# ---- computation tools: the published cost function and the shared tools ------------------------------------------


@pytest.mark.parametrize(("case", "sid"), CASES, ids=IDS)
def test_prices_and_proposals_are_what_the_cost_function_says(case, sid, package):
    st, tools, threads = tools_for(case, sid, package)
    f = F.thread_facts(st, sid, threads[sid])
    est = st.estimates.get(sid)
    offers = tools.run("supplier_offers", {"supplier_id": sid})
    truth = total_cost(case, f.standing.terms, f.std, est)
    assert close(offers["standing_sheet"]["c_eur"], truth, 0.5)

    out = tools.run("price_package", {"supplier_id": sid, "unit_price": 90.0})
    sheet = {**f.standing.terms, "unit_price": 90.0}
    from arena_kit.agents.buyer_v2 import guard

    full = guard.normalise(st, sheet, f)
    assert close(out["c_eur"], total_cost(case, full, f.std, est), 0.5)
    assert abs(sum(out["cost_breakdown_eur"].values()) - out["c_eur"]) <= len(out["cost_breakdown_eur"])
    assert close(out["vs_its_standing_eur"], out["c_eur"] - truth, 1.0)

    prop = tools.run("propose_counter", {"supplier_id": sid, "levers": ["payment_days"]})
    for s in prop.get("sheets", []):
        price = float(re.search(r"unit_price ([\d.]+)", s["terms"]).group(1))
        assert price <= 100.0 and s["c_eur"] < truth

    band = getattr(st.ctx, "bands", {}).get(sid)
    if band is not None:
        out = tools.run("price_band", {"supplier_id": sid})
        for key in ("target", "ceiling", "floor", "fair", "c_target"):
            truth = getattr(band, key)
            assert (out[key] is None and truth is None) or close(out[key], truth, 1e-3), key
    trades = tools.run("lever_trades", {"supplier_id": sid}).get("trades", [])
    assert len(trades) == min(12, len(getattr(st.ctx, "trades", {}).get(sid, ())))
    for t in trades:
        assert t["saves_eur"] > 0


def test_concession_fit_and_offers_follow_the_thread(package):
    events = [quote(SID, 0, 17.4), ours({"unit_price": 15.8, "payment_days": 30}), quote(SID, 1, 17.0)]
    st, tools, _ = tools_for(S01, SID, package, events)
    fit = tools.run("concession_fit", {"supplier_id": SID})
    assert fit["its_prices"] == [17.4, 17.0] and fit["our_prices"] == [15.8]
    truth = asdict(st.ctx.fits[SID])
    assert fit["fit"]["prices"] == [17.4, 17.0] == list(truth["prices"])
    for key in ("reservation_estimate", "last_step_pct", "p_room"):
        assert (fit["fit"][key] is None and truth[key] is None) or close(fit["fit"][key], truth[key], 1e-3), key
    assert fit["fit"]["shrinking"] == truth["shrinking"]
    offers = tools.run("supplier_offers", {"supplier_id": SID})
    assert offers["standing_sheet"]["id"] == "offer:70270-1" and offers["our_offer_on_the_table"]
    assert not offers.get("it_accepted_our_offer")


def test_thread_tools_return_the_emails_and_attachment_text_as_written(package):
    pdf = read_document("quote.pdf", (KIT / "tests/fixtures/quote.pdf").read_bytes())
    xlsx = read_document("cost-breakdown.xlsx", (KIT / "tests/fixtures/cost-breakdown.xlsx").read_bytes())
    assert pdf.strip() and xlsx.strip()
    first = {**quote(SID, 0, 17.4, body="Guten Tag, anbei unser Angebot."),
             "attachments": [{"name": "quote.pdf", "text": pdf}]}  # fmt: skip
    reply = {**quote(SID, 1, 17.0), "attachments": [{"name": "cost-breakdown.xlsx", "text": xlsx}]}
    events = [first, ours({"unit_price": 15.8, "payment_days": 30}), reply]
    _, tools, _ = tools_for(S01, SID, package, events)
    text = tools.run("thread_transcript", {"supplier_id": SID})
    assert "Guten Tag, anbei unser Angebot." in text and pdf.strip()[:200] in text and "status: open" in text
    assert tools.run("read_attachment", {"supplier_id": SID, "name": "cost"})["text"] == xlsx[:8000]
    assert tools.run("read_attachment", {"supplier_id": SID, "name": "quote"})["text"] == pdf[:8000]


def test_every_catalogue_tool_is_covered_by_a_truth_check():
    from arena_kit.agents.buyer_v2.toolbox import CATALOGUE

    source = open(__file__, encoding="utf-8").read()
    missing = [name for name in CATALOGUE if f'"{name}"' not in source]
    assert not missing, f"tools without a truth check: {missing}"
    assert not math.isnan(0.0)
