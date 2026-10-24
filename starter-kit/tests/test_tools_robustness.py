"""Bands must work on cases we have never seen: every practice case file (9 were never played - a holdout) and
mutated copies of them - unknown suppliers and parts, bundles, other price levels, many options. These tests
check properties that must hold for any case, never numbers fitted to a practice case."""

import copy
import json
import time
from pathlib import Path

import pytest

from arena_kit.agents.buyer.tools import bands
from arena_kit.cost import complete_offer
from arena_kit.data import DataPackage

DATA = Path(__file__).parents[2] / "data"
CASES = json.loads((Path(__file__).parent / "fixtures/practice_cases.json").read_text())
MAX_SECONDS = 2.0  # bands run once per case before the first move; the 60 s clock is already running


@pytest.fixture(scope="module")
def package():
    return DataPackage(DATA)


def opening_threads(case: dict, markup: float = 1.06) -> dict[str, list[dict]]:
    """A plausible opening for any case: today's price (or the fallback) plus a markup, every field filled."""
    base = case["buyer"].get("current_unit_price") or case["buyer"]["fallback_unit_price"]
    terms = complete_offer(case, {"unit_price": round(base * markup, 2)})
    return {
        sid: [{"from": "supplier", "status": "open", "attachments": [],
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


@pytest.mark.parametrize("mutate", MUTATIONS, ids=IDS)
@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_bands_when_any_case_is_mutated_keep_their_invariants(case, mutate, package):
    case = mutate(case)
    threads = opening_threads(case)
    start = time.monotonic()
    result = bands.price_bands(case, threads, package)
    assert time.monotonic() - start < MAX_SECONDS
    assert set(result) == set(case["suppliers"])
    for sid, band in result.items():
        opening = threads[sid][0]["term_sheets"][0]["terms"]["unit_price"]
        assert 0 < band.target <= band.ceiling <= opening
        assert band.c_target <= band.c_ceiling + 0.01
        assert band.floor is None or band.floor > 0
        assert set(case["negotiable"]) <= set(band.target_terms)
        for field, allowed in case.get("term_options", {}).items():
            if field in case["negotiable"]:
                assert band.target_terms[field] in allowed, field


def test_bands_when_options_exceed_the_search_cap_still_consider_late_options(package):
    # the search samples large option spaces instead of taking the first combinations
    case = many_options(next(c for c in CASES if c["case_id"] == "case:P01"))
    band = bands.price_bands(case, opening_threads(case), package)["sup:70110"]
    assert band.target_terms["payment_days"] in case["term_options"]["payment_days"]
    packages = list(bands._packages(case, complete_offer(case, {"unit_price": 130.0})))
    assert len(packages) <= bands.MAX_PACKAGES
    for field, allowed in case["term_options"].items():
        assert {str(p[field]) for p in packages} == {str(o) for o in allowed}, field
