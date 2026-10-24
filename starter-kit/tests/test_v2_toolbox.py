"""v2 tools on the real data package: every tool answers on every practice case with plain JSON, the generic data
access stays read-only, and the computation tools apply the same hard limits as the guard."""

import json

import pytest
from v2_support import DATA, PRACTICE, S01, SID, ours, quote, state_for

from arena_kit.agents.buyer_v2 import agents as A
from arena_kit.agents.buyer_v2.toolbox import CATALOGUE, ToolContext, toolset

pytestmark = pytest.mark.skipif(not (DATA / "world.sqlite").is_file(), reason="needs the data package")


@pytest.fixture(scope="module")
def package():
    from arena_kit.agents.buyer.tools.safe_package import thread_safe
    from arena_kit.data import DataPackage

    return thread_safe(DataPackage(DATA))


def args_for(name: str, case: dict, sid: str) -> dict:
    part = (case.get("parts") or ["part:310010"])[0]
    return {
        "supplier_id": {"supplier_id": sid},
        "part_id": {"part_id": part},
        "series_id": {"series_id": "LCI-C"},
        "sql": {"sql": "SELECT id, name FROM suppliers LIMIT 3"},
        "id": {"id": f"sc:{sid[4:]}@2026Q3"},
        "prefix": {"prefix": "idx:LCI-C@2026"},
        "ref": {"ref": case.get("trigger_doc") or "doc:20251003-70310-N"},
        "name": {"name": "quote"},
        "unit_price": {"unit_price": 90.0},
    }


def call_args(name: str, case: dict, sid: str) -> dict:
    tool, out = CATALOGUE[name], {}
    known = args_for(name, case, sid)
    for param in tool.params:
        out.update(known.get(param, {}))
    return out


@pytest.mark.parametrize("case", PRACTICE, ids=lambda c: c["case_id"])
def test_every_tool_answers_on_every_practice_case(case, package):
    threads = {s: [quote(s, 0, 100.0)] for s in case["suppliers"]}
    st = state_for(case, threads, package=package)
    ctx = ToolContext(st, threads, package)
    sid = case["suppliers"][0]
    every = toolset(list(CATALOGUE), ctx)
    for name in CATALOGUE:
        if name == "read_attachment":
            continue  # the test threads carry no attachment
        out = every.run(name, call_args(name, case, sid))
        json.dumps(out, ensure_ascii=False)  # plain JSON, no dataclasses or inf


def test_agent_tool_subsets_exist_and_stay_read_only():
    for spec in A.SPECS.values():
        assert set(spec.tools) <= set(CATALOGUE)
    assert set(A.NEGOTIATOR_TOOLS) <= set(CATALOGUE)
    for name in CATALOGUE:
        assert not any(word in name for word in ("send", "accept", "walk", "write", "delete", "events", "leaderboard"))


def test_sql_query_is_select_only(package):
    threads = {SID: [quote(SID, 0, 17.4)]}
    ctx = ToolContext(state_for(S01, threads, package=package), threads, package)
    tools = toolset(["sql_query"], ctx)
    assert tools.run("sql_query", {"sql": "SELECT COUNT(*) AS n FROM suppliers"})["rows"][0]["n"] > 0
    with pytest.raises(ValueError):
        tools.run("sql_query", {"sql": "DELETE FROM suppliers"})


def test_lookup_reads_this_cases_own_facts_first(package):
    case = dict(S01, facts={"doc:NEW-1": {"supplier": SID, "body": "We raise prices by 4 %."}})
    threads = {SID: [quote(SID, 0, 17.4)]}
    ctx = ToolContext(state_for(case, threads, package=package), threads, package)
    tools = toolset(["lookup_id", "read_document", "supplier_documents"], ctx)
    assert tools.run("lookup_id", {"id": "doc:NEW-1"})["source"] == "this case"
    assert "4 %" in tools.run("read_document", {"ref": "doc:NEW-1"})["text"]
    assert any(f["id"] == "doc:NEW-1" for f in tools.run("supplier_documents", {"supplier_id": SID})["this_case_facts"])


def test_price_package_reports_the_hard_limit_corrections(package):
    events = [quote(SID, 0, 17.4), ours({"unit_price": 15.8, "payment_days": 30}), quote(SID, 1, 17.0)]
    threads = {SID: events}
    ctx = ToolContext(state_for(S01, threads, package=package), threads, package)
    out = toolset(["price_package"], ctx).run(
        "price_package", {"supplier_id": SID, "unit_price": 18.0, "terms": [{"field": "payment_days", "value": "60"}]}
    )
    assert out["c_eur"] > 0 and out["as_a_counter"]["allowed"]
    assert any("above its offer" in n for n in out["as_a_counter"]["corrections"])


def test_propose_counter_returns_only_sheets_the_guard_allows(package):
    events = [quote(SID, 0, 17.4)]
    threads = {SID: events}
    ctx = ToolContext(state_for(S01, threads, package=package), threads, package)
    out = toolset(["propose_counter"], ctx).run("propose_counter", {"supplier_id": SID, "levers": ["payment_days"]})
    assert 1 <= len(out["sheets"]) <= 3
    assert all("unit_price" in s["terms"] for s in out["sheets"])


def test_an_unknown_supplier_or_missing_argument_is_a_readable_error(package):
    threads = {SID: [quote(SID, 0, 17.4)]}
    ctx = ToolContext(state_for(S01, threads, package=package), threads, package)
    tools = toolset(["supplier_offers"], ctx)
    with pytest.raises(ValueError, match="no thread"):
        tools.run("supplier_offers", {"supplier_id": "sup:1"})
    with pytest.raises(ValueError, match="missing"):
        tools.run("supplier_offers", {})
