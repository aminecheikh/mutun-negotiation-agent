"""The deterministic layer must work on cases we have never seen: every practice case file (9 were never played - a
holdout) and mutated copies of them - unknown suppliers and parts, bundles, other price levels, many options. These
tests check properties that must hold for any case, never numbers fitted to a practice case."""

import copy
import json
import time
from pathlib import Path

import pytest

from arena_kit.agents.buyer.tools import DataTools, claims, estimates, planner
from arena_kit.cost import complete_offer, must_haves_ok
from arena_kit.data import DataPackage

DATA = Path(__file__).parents[2] / "data"
CASES = json.loads((Path(__file__).parent / "fixtures/practice_cases.json").read_text())
MAX_SECONDS = 5.0  # one round of the planner, well inside the 48 s move budget


@pytest.fixture(scope="module")
def package():
    return DataPackage(DATA)


def opening_threads(case: dict, markup: float = 1.06) -> dict[str, list[dict]]:
    """A plausible opening for any case: today's price (or the fallback) plus a markup, every field filled."""
    base = case["buyer"].get("current_unit_price") or case["buyer"]["fallback_unit_price"]
    terms = complete_offer(case, {"unit_price": round(base * markup, 2)})
    return {
        sid: [{"from": "supplier", "status": "open", "attachments": [], "body": "Offer.",
               "term_sheets": [{"term_sheet_id": f"offer:{sid}-0", "terms": terms}]}]
        for sid in case["suppliers"]
    }  # fmt: skip


def unknown_supplier(case: dict) -> dict:
    out = copy.deepcopy(case)
    out["suppliers"] = ["sup:99999"]
    return out


def unknown_part(case: dict) -> dict:
    out = copy.deepcopy(case)
    out["parts"] = ["part:999999"]
    return out


def bundle(case: dict) -> dict:
    out = copy.deepcopy(case)
    out["parts"] = [*case["parts"], "part:310010"]
    return out


def prices_times_100(case: dict) -> dict:
    out = copy.deepcopy(case)
    for field in ("current_unit_price", "fallback_unit_price"):
        if out["buyer"].get(field):
            out["buyer"][field] *= 100
    if (alt := out["buyer"].get("alternative") or {}).get("terms", {}).get("unit_price"):
        alt["terms"]["unit_price"] *= 100
    return out


def many_options(case: dict) -> dict:
    out = copy.deepcopy(case)
    out["negotiable"] = list(dict.fromkeys([*case["negotiable"], "payment_days", "lead_time_days", "contract_months"]))
    options = out.setdefault("term_options", {})
    options["payment_days"] = [14, 30, 45, 60, 75, 90, 120]
    options["lead_time_days"] = [7, 14, 21, 28, 35, 42, 56]
    options["contract_months"] = [0, 6, 12, 18, 24, 36]
    return out


MUTATIONS = [lambda c: c, unknown_supplier, unknown_part, bundle, prices_times_100, many_options]
IDS = ["as_is", "unknown_supplier", "unknown_part", "bundle", "prices_x100", "many_options"]


def valid_for_the_arena(case: dict, sheet: dict, std: dict) -> None:
    assert set(case["negotiable"]) <= set(sheet)
    assert sheet["unit_price"] > 0
    for field in case["negotiable"]:
        allowed = case.get("term_options", {}).get(field)
        if allowed and field != "unit_price":
            assert sheet[field] in allowed or sheet[field] == std.get(field), field
    assert must_haves_ok(case, sheet, std)


@pytest.mark.parametrize("mutate", MUTATIONS, ids=IDS)
@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_planner_when_any_case_is_mutated_sends_valid_sheets_fast(case, mutate, package):
    case = mutate(case)
    for sid, events in opening_threads(case).items():
        std = planner.standard(case, events)
        supplier = estimates.supplier_estimate(package, case, sid, std)
        start = time.monotonic()
        first = planner.packages(case, events, supplier)
        assert time.monotonic() - start < MAX_SECONDS
        assert 1 <= len(first) <= planner.MAX_SHEETS
        for sheet in first:
            valid_for_the_arena(case, sheet, std)
        # a second round: the supplier moved on its own terms
        theirs = events[0]["term_sheets"][0]["terms"]
        moved = {**theirs, "unit_price": round(theirs["unit_price"] * 0.99, 2)}
        reply = {"from": "supplier", "status": "open", "attachments": [], "body": "Revised offer.",
                 "term_sheets": [{"term_sheet_id": f"offer:{sid}-1", "terms": moved}]}  # fmt: skip
        events = [*events, {"from": "buyer", "term_sheets": [{"terms": t} for t in first]}, reply]
        second = planner.packages(case, events, supplier)
        for sheet in second:
            valid_for_the_arena(case, sheet, std)
        assert second[0]["unit_price"] <= theirs["unit_price"]


@pytest.mark.parametrize("mutate", MUTATIONS, ids=IDS)
@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_tools_when_any_case_is_mutated_prepare_a_context(case, mutate, package):
    case = mutate(case)
    ctx = DataTools(package).prepare(case, opening_threads(case))
    assert set(ctx.claims) == set(case["suppliers"])
    for found in ctx.claims.values():
        refs = [r for c in found for r in c.refs]
        assert len(refs) == len(set(refs))  # each data ID once
        assert all(c.favours_buyer and c.kind in claims.PRIORITY for c in found)


# ---- the decision agent's package: bounded by the deterministic plan ----------------------------------------------


def agent_packages(case: dict, lead: dict) -> list[dict]:
    """Every single-term package an agent could propose: each other option of each negotiable non-price term."""
    out = []
    for field, options in (case.get("term_options") or {}).items():
        if field in case["negotiable"] and field != "unit_price":
            out += [{field: value} for value in options if value != lead.get(field)]
    return out


@pytest.mark.parametrize("mutate", MUTATIONS, ids=IDS)
def test_agent_package_never_costs_more_than_the_deterministic_plan(mutate, package):
    added = 0
    for case in map(mutate, CASES):
        for sid, events in opening_threads(case).items():
            std = planner.standard(case, events)
            supplier = estimates.supplier_estimate(package, case, sid, std)
            sheets = planner.packages(case, events, supplier)
            bound = min(planner.cost(case, s, std, supplier) for s in sheets)
            for proposed in agent_packages(case, sheets[0]):
                out = planner.with_agent_package(case, sheets, proposed, events, supplier)
                assert out[0] == sheets[0] and len(out) <= planner.MAX_SHEETS  # the lead sheet stays the planner's
                if out == sheets:
                    continue  # no price keeps it within the plan's C, or it repeats a sheet: left out
                added += 1
                mine = out[1]
                assert all(mine[k] == v for k, v in proposed.items())  # the agent's terms, priced by code
                assert planner.cost(case, mine, std, supplier) <= bound + 0.005  # never dearer than the rules
                assert must_haves_ok(case, mine, std)
    assert added  # the bound leaves the agent real room


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_planner_trim_keeps_margin_within_the_minimal_step_and_follow_never_under_full_cost(case, package):
    for sid, events in opening_threads(case).items():
        std = planner.standard(case, events)
        supplier = estimates.supplier_estimate(package, case, sid, std)
        first = planner.packages(case, events, supplier)
        theirs = events[0]["term_sheets"][0]["terms"]
        moved = {**theirs, "unit_price": round(theirs["unit_price"] * 0.97, 2)}
        events = [*events, {"from": "buyer", "term_sheets": [{"terms": t} for t in first]},
                  {"from": "supplier", "status": "open", "attachments": [], "body": "Revised offer.",
                   "term_sheets": [{"term_sheet_id": f"offer:{sid}-1", "terms": moved}]}]  # fmt: skip
        follow, trim, push = (planner.packages(case, events, supplier, concede) for concede in (1.0, 0.5, 0.0))
        if not (follow and trim and push):
            continue
        c_follow, c_trim, c_push = (planner.cost(case, s[0], std, supplier) for s in (follow, trim, push))
        assert c_push - 0.01 <= c_trim <= c_follow + 0.01  # never more than the planner concedes, never less than push
        if supplier and trim[0]["unit_price"] < follow[0]["unit_price"]:
            assert trim[0]["unit_price"] >= min(moved["unit_price"], supplier["full_cost"]) - 0.01  # credible
