"""tools/claims.py: claim sentences that can only check out as verified - on real rows, recomputed
independently from facts.json, and as properties over every case file and unseen suppliers and parts."""

import copy
import json
import re
from pathlib import Path

import pytest

from arena_kit.agents.buyer.tools import claims
from arena_kit.data import DataPackage

DATA = Path(__file__).parents[2] / "data"
CASES = {c["case_id"]: c for c in json.loads((Path(__file__).parent / "fixtures/practice_cases.json").read_text())}
FACTS = json.loads((DATA / "facts.json").read_text())
PCT_TOLERANCE, EUR_TOLERANCE = 0.5, 0.005  # brief/api.md "What counts": +-0.5 pt for %, else +-0.5 %


@pytest.fixture(scope="module")
def package():
    return DataPackage(DATA)


def threads_for(case: dict, prices: dict[str, float], body: str = "") -> dict[str, list[dict]]:
    return {
        sid: [{"from": f"sales@{sid[4:]}", "status": "open", "body": body, "attachments": [],
               "term_sheets": [{"term_sheet_id": f"offer:{sid[4:]}-0", "terms": {"unit_price": price}}]}]
        for sid, price in prices.items()
    }  # fmt: skip


def fact(case: dict, ref: str) -> dict | None:
    return case.get("facts", {}).get(ref) or FACTS.get(ref)


def recomputed(case: dict, claim, threads) -> float | None:
    """The figure the arena would compute for the claim's refs (brief/api.md "What counts")."""
    refs = claim.refs
    if claim.kind == "index_change":
        earlier, later = (fact(case, r)["value"] for r in refs)
        return (later / earlier - 1) * 100
    if claim.kind in ("rival_quote", "should_cost"):
        f = fact(case, refs[0])
        return f.get("unit_price") or f.get("unit_cost")
    if claim.kind == "rival_offer":
        sid = next(s for s, ev in threads.items() if ev[-1]["term_sheets"][0]["term_sheet_id"] == refs[0])
        return threads[sid][-1]["term_sheets"][0]["terms"]["unit_price"]
    if claim.kind == "scorecard":
        return sum(fact(case, r)["otd_pct"] for r in refs) / len(refs)
    if claim.kind == "quality":
        return sum(fact(case, r)["ppm"] for r in refs) / len(refs)
    if claim.kind == "forecast":
        return fact(case, refs[0])["change_pct"]
    if claim.kind == "notice":
        return fact(case, refs[0])["days"]
    return None  # po_history: the arena checks the order prices themselves


def assert_checks_out(case: dict, claim, threads) -> None:
    for ref in claim.refs:
        assert ref in claim.sentence, (ref, claim.sentence)
    expected = recomputed(case, claim, threads)
    if expected is None:
        return
    if claim.kind in ("index_change", "forecast", "scorecard"):
        assert abs(claim.figure - expected) <= PCT_TOLERANCE, claim
    else:
        assert abs(claim.figure - expected) <= max(abs(expected) * EUR_TOLERANCE, 0.01), claim


P01_THREADS = {"sup:70110": 138.55}


def test_candidates_when_index_fell_cites_both_months_with_the_change(package):
    case = CASES["case:P01"]
    found = claims.candidates(case, package, "sup:70110", threads_for(case, P01_THREADS))
    claim = next(c for c in found if c.refs == ("idx:PPI-D351@2025-08", "idx:PPI-D351@2026-08"))
    assert claim.kind == "index_change" and round(claim.figure, 1) == -3.7 and claim.favours_buyer
    assert "-3.7%" in claim.sentence


def test_candidates_when_supplier_asks_more_than_its_drivers_rose_includes_the_rising_drivers(package):
    case = CASES["case:P01"]  # asks +9.5 %: iron/steel +6.5 % and labour +2.7 % are both below it
    found = claims.candidates(case, package, "sup:70110", threads_for(case, P01_THREADS))
    series = {c.refs[0].split("@")[0] for c in found if c.kind == "index_change"}
    assert {"idx:PPI-C241", "idx:LCI-C", "idx:PPI-D351"} <= series


def test_candidates_when_supplier_asks_no_increase_skips_drivers_that_rose(package):
    case = CASES["case:P01"]
    current = case["buyer"]["current_unit_price"]
    found = claims.candidates(case, package, "sup:70110", threads_for(case, {"sup:70110": current}))
    series = {c.refs[0].split("@")[0] for c in found if c.kind == "index_change"}
    assert "idx:PPI-D351" in series and "idx:PPI-C241" not in series


def test_candidates_when_supplier_names_a_series_ranks_it_first(package):
    case = CASES["case:P01"]
    body = "Our costs move with PPI-D351 and other drivers."
    found = claims.candidates(case, package, "sup:70110", threads_for(case, P01_THREADS, body))
    first_index = next(c for c in found if c.kind == "index_change")
    assert first_index.refs[0].startswith("idx:PPI-D351@")


def test_candidates_when_letter_gives_less_notice_than_contract_cites_both(package):
    case = CASES["case:P01"]  # its letter gives 51 days, the framework contract requires 90
    found = claims.candidates(case, package, "sup:70110", threads_for(case, P01_THREADS))
    notice = next(c for c in found if c.kind == "notice")
    assert notice.refs[0].startswith("doc:") and notice.refs[1].startswith("ctr:") and notice.figure == 51


def test_candidates_when_scorecard_is_red_cites_on_time_delivery(package):
    case = CASES["case:P11"]
    found = claims.candidates(case, package, "sup:70140", threads_for(case, {"sup:70140": 47.0}))
    otd = next(c for c in found if c.kind == "scorecard")
    assert otd.refs == ("sc:70140@2026Q3",) and otd.figure == 63.5


def test_candidates_when_demand_forecast_rose_cites_the_latest_change(package):
    case = CASES["case:P01"]
    found = claims.candidates(case, package, "sup:70110", threads_for(case, P01_THREADS))
    fc = next(c for c in found if c.kind == "forecast")
    assert fc.refs[0].startswith("fc:310010@") and fc.figure > 0


def test_candidates_when_past_orders_were_cheaper_cites_two_to_eight_lines_of_the_part(package):
    case = CASES["case:P01"]
    found = claims.candidates(case, package, "sup:70110", threads_for(case, P01_THREADS))
    po = next(c for c in found if c.kind == "po_history")
    assert 2 <= len(po.refs) <= 8
    assert {FACTS[r]["part"] for r in po.refs} == {"part:310010"}
    assert all(FACTS[r]["unit_price"] < 138.55 for r in po.refs)


def test_candidates_when_rfq_rival_offers_less_cites_its_offer_to_the_dearer_bidder(package):
    case = CASES["case:P05"]
    prices = {"sup:70210": 36.0, "sup:70240": 31.5, "sup:70250": 38.5}
    threads = threads_for(case, prices)
    dear = claims.candidates(case, package, "sup:70250", threads)
    cheapest = claims.candidates(case, package, "sup:70240", threads)
    rival = next(c for c in dear if c.kind == "rival_offer")
    assert rival.refs == ("offer:70240-0",) and rival.figure == 31.5
    assert not [c for c in cheapest if c.kind == "rival_offer"]


def test_candidates_when_case_only_facts_hold_a_letter_uses_them():
    # hidden cases carry facts that exist only in the case: a new letter and its contract
    case = copy.deepcopy(CASES["case:P01"])
    case["suppliers"], case["trigger_doc"] = ["sup:99999"], "doc:NEW-1"
    case["facts"] = {
        "doc:NEW-1": {"supplier": "sup:99999", "date": "2026-10-01", "days": 30, "type": "letter"},
        "ctr:NEW-1": {"supplier": "sup:99999", "days": 90},
    }
    found = claims.candidates(case, None, "sup:99999", threads_for(case, {"sup:99999": 140.0}))
    notice = next(c for c in found if c.kind == "notice")
    assert notice.refs == ("doc:NEW-1", "ctr:NEW-1") and notice.figure == 30


def opening_prices(case: dict) -> dict[str, float]:
    base = case["buyer"].get("current_unit_price") or case["buyer"]["fallback_unit_price"]
    return {sid: round(base * (1.04 + 0.02 * i), 2) for i, sid in enumerate(case["suppliers"])}


def unknown_supplier(case: dict) -> dict:
    out = copy.deepcopy(case)
    out["suppliers"] = ["sup:99999"]
    return out


def unknown_part(case: dict) -> dict:
    out = copy.deepcopy(case)
    out["parts"] = ["part:999999"]
    return out


@pytest.mark.parametrize("mutate", [lambda c: c, unknown_supplier, unknown_part], ids=["as_is", "new_sup", "new_part"])
@pytest.mark.parametrize("case", list(CASES.values()), ids=list(CASES))
def test_candidates_when_any_case_every_claim_checks_out(case, mutate, package):
    case = mutate(case)
    threads = threads_for(case, opening_prices(case))
    offers = {ev[-1]["term_sheets"][0]["term_sheet_id"] for ev in threads.values()}
    for sid in case["suppliers"]:
        found = claims.candidates(case, package, sid, threads)
        assert len(found) <= claims.MAX_CLAIMS
        seen: set[str] = set()
        for claim in found:
            assert claim.favours_buyer and 0 <= claim.strength <= 1
            assert all(fact(case, r) is not None or r in offers for r in claim.refs), claim.refs
            assert not seen & set(claim.refs), "an ID counts once per case"
            seen |= set(claim.refs)
            if claim.kind == "index_change":
                assert len(claim.refs) == 2
                months = [re.search(r"@(\d{4})-(\d{2})$", r).groups() for r in claim.refs]
                (y1, m1), (y2, m2) = ((int(y), int(m)) for y, m in months)
                assert 0 < (y2 - y1) * 12 + (m2 - m1) <= 12
            assert_checks_out(case, claim, threads)


def test_candidates_when_no_data_package_and_no_threads_returns_empty():
    assert claims.candidates(CASES["case:P01"], None, "sup:70110") == ()


def test_data_tools_when_prepared_and_refreshed_pass_threads_to_claims(package):
    from arena_kit.agents.buyer.tools import DataTools

    case = CASES["case:P01"]
    sheet = {"term_sheet_id": "offer:70110-0", "terms": {"unit_price": 138.55, "effective_date": "2026-11-01"}}
    body = "PPI-D351 rose 1.0 % in twelve months and is 6 % of our cost."
    threads = {"sup:70110": [{"from": "sales@70110", "status": "open", "body": body, "term_sheets": [sheet]}]}
    tools = DataTools(package)
    ctx = tools.prepare(case, threads)
    assert ctx.claims["sup:70110"] == claims.candidates(case, package, "sup:70110", threads)
    assert tools.refresh(ctx, threads).claims == ctx.claims
