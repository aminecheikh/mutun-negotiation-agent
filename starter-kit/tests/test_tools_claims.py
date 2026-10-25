"""tools/claims.py: arguments that can only check out as verified - recomputed independently from facts.json on every
case file and on unseen suppliers and parts - and the rules that filter, order and pick them."""

import copy
import json
from pathlib import Path

import pytest

from arena_kit.agents.buyer.contracts import Claim
from arena_kit.agents.buyer.tools import claims
from arena_kit.cost import complete_offer
from arena_kit.data import DataPackage

DATA = Path(__file__).parents[2] / "data"
CASES = {c["case_id"]: c for c in json.loads((Path(__file__).parent / "fixtures/practice_cases.json").read_text())}
FACTS = json.loads((DATA / "facts.json").read_text())
RELEASE = DATA / "release-sunday" / "facts.json"  # the Sunday release adds citable IDs, as the arena does
if RELEASE.is_file():
    FACTS |= json.loads(RELEASE.read_text(encoding="utf-8"))
PCT_TOLERANCE, EUR_TOLERANCE = 0.5, 0.005  # brief/api.md "What counts": +-0.5 pt for %, else +-0.5 %


@pytest.fixture(scope="module")
def package():
    return DataPackage(DATA)


def fact(case: dict, ref: str) -> dict:
    return case.get("facts", {}).get(ref) or FACTS.get(ref) or {}


def threads_for(case: dict, markup: float = 1.06, body: str = "") -> dict[str, list[dict]]:
    base = case["buyer"].get("current_unit_price") or case["buyer"]["fallback_unit_price"]
    terms = complete_offer(case, {"unit_price": round(base * markup, 2)})
    return {
        sid: [{"from": f"sales@{sid[4:]}", "status": "open", "body": body, "attachments": [],
               "term_sheets": [{"term_sheet_id": f"offer:{sid[4:]}-0", "terms": terms}]}]
        for sid in case["suppliers"]
    }  # fmt: skip


def recomputed(case: dict, claim: Claim) -> float | None:
    """The figure the arena would compute from the claim's refs."""
    refs = claim.refs
    if claim.kind == "index":
        earlier, later = (fact(case, r)["value"] for r in refs)
        return (later / earlier - 1) * 100
    if claim.kind == "cost":
        return fact(case, refs[0])["unit_cost"]
    if claim.kind == "delivery":
        return fact(case, refs[0])["otd_pct"]
    if claim.kind == "quality":
        return fact(case, refs[0])["ppm"]
    if claim.kind == "volume":
        return fact(case, refs[0])["change_pct"]
    if claim.kind == "notice":
        return fact(case, refs[0])["days"]
    if claim.kind == "rival" and refs[0].startswith("quote:"):
        return fact(case, refs[0])["unit_price"]
    return None  # order history and news assert no figure; a live rival offer is checked by its ID


def new_supplier(case: dict) -> dict:
    out = copy.deepcopy(case)
    out["suppliers"] = ["sup:99999"]
    return out


def new_part(case: dict) -> dict:
    out = copy.deepcopy(case)
    out["parts"] = ["part:999999"]
    return out


@pytest.mark.parametrize("mutate", [lambda c: c, new_supplier, new_part], ids=["as_is", "new_sup", "new_part"])
@pytest.mark.parametrize("case", list(CASES.values()), ids=list(CASES))
def test_candidates_when_any_case_every_claim_checks_out(case, mutate, package):
    case = mutate(case)
    threads = threads_for(case)
    for sid in case["suppliers"]:
        found = claims.candidates(case, package, sid, threads)
        seen: set[str] = set()
        for claim in found:
            assert claim.favours_buyer and 0 < claim.strength <= 1 and claim.kind in claims.PRIORITY
            assert not seen & set(claim.refs), "an ID counts once per case"
            seen |= set(claim.refs)
            for ref in claim.refs:
                assert ref in claim.sentence, (ref, claim.sentence)
                assert fact(case, ref), ref
            expected = recomputed(case, claim)
            if expected is not None:
                tolerance = (
                    PCT_TOLERANCE if claim.kind in ("index", "delivery", "volume") else abs(expected) * EUR_TOLERANCE
                )
                assert abs(claim.figure - expected) <= tolerance + 0.051, (claim.sentence, expected)
        assert [c.strength for c in found] == sorted((c.strength for c in found), reverse=True)


def test_candidates_never_argue_for_the_seller(package):
    for case in CASES.values():
        threads = threads_for(case)
        for sid in case["suppliers"]:
            asking = threads[sid][0]["term_sheets"][0]["terms"]["unit_price"]
            for claim in claims.candidates(case, package, sid, threads):
                if claim.kind == "index":
                    assert claim.figure < 0, claim.sentence  # a driver that rose helps the supplier
                if claim.kind in ("cost", "history") or (claim.kind == "rival" and claim.figure is not None):
                    assert claim.figure < asking, claim.sentence


def test_candidates_when_supplier_says_a_series_is_hedged_drop_that_series(package):
    case = CASES["case:P01"]
    found = claims.candidates(case, package, "sup:70110", threads_for(case))
    index = next((c for c in found if c.kind == "index"), None)
    if index is None:
        pytest.skip("no falling driver in this case")
    name = index.sentence.split(" index ", 1)[0].removeprefix("The ")
    hedged = threads_for(case, body=f"Our {name} needs are covered at fixed prices until next year.")
    assert all(c.refs != index.refs for c in claims.candidates(case, package, "sup:70110", hedged))


def test_candidates_order_notice_and_rival_first_then_by_kind():
    found = [Claim(k, (f"x:{k}",), k, -1.0, True, 0.0) for k in ("news", "index", "history", "rival", "notice")]
    order = [c.kind for c in claims._ordered(found, [])]
    assert order[:2] == ["notice", "rival"] and order.index("index") < order.index("history") < order.index("news")


def test_candidates_when_the_supplier_ignored_a_kind_rank_it_after_the_ones_it_accepted():
    said = [{"from": "sales@x", "body": "We checked the index data you cited; it holds, but it does not change our "
             "calculation. We checked the forecast you cited; it holds and we took it into account."}]  # fmt: skip
    seen = claims.reactions(said)
    assert seen["index"] < 0 < seen["volume"]
    found = [Claim("i", ("idx:a", "idx:b"), "index", -2.0, True, 0.0), Claim("v", ("fc:1",), "volume", 5.0, True, 0.0)]
    assert [c.kind for c in claims._ordered(found, said)] == ["volume", "index"]


def test_candidates_when_several_suppliers_bid_cite_a_cheaper_live_rival(package):
    case = next(c for c in CASES.values() if len(c["suppliers"]) > 1)
    threads = threads_for(case)
    cheap, dear = case["suppliers"][0], case["suppliers"][1]
    terms = threads[dear][0]["term_sheets"][0]["terms"]
    threads[cheap][0]["term_sheets"][0]["terms"] = {**terms, "unit_price": round(terms["unit_price"] * 0.9, 2)}
    found = claims.candidates(case, package, dear, threads)
    assert found and found[0].kind == "rival" and f"offer:{cheap[4:]}-0" in found[0].refs
    assert all(
        c.kind != "rival" or not c.refs[0].startswith("offer:")
        for c in claims.candidates(case, package, cheap, threads)
    )


def test_default_choice_takes_two_arguments_first_then_three():
    found = tuple(Claim(str(i), (f"x:{i}",), "index", -1.0, True, 1 - i / 10) for i in range(5))
    case = {"suppliers": ["sup:1"], "buyer": {"category_goal": "leverage"}}
    first = [{"from": "s", "body": "Offer."}]
    assert claims.default_choice(case, found, first) == (0, 1)
    later = [*first, {"from": "buyer"}, {"from": "s", "body": "Offer."}]
    assert claims.default_choice(case, found, later) == (0, 1, 2)


def test_default_choice_when_supplier_argues_with_costs_leads_with_a_falling_index():
    found = (Claim("n", ("x:n",), "notice", 1.0, True, 1.0), Claim("v", ("fc:1",), "volume", 5.0, True, 0.8),
             Claim("i", ("idx:a", "idx:b"), "index", -2.0, True, 0.5))  # fmt: skip
    case = {"suppliers": ["sup:1"], "buyer": {"category_goal": "leverage"}}
    events = [{"from": "s", "body": "Our material costs rose sharply."}]
    assert claims.default_choice(case, found, events)[0] == 2


def test_candidates_when_no_data_package_and_no_threads_returns_only_case_facts():
    found = claims.candidates(CASES["case:P01"], None, "sup:70110")
    assert all(c.refs for c in found)


def test_live_rival_cites_a_lower_quote_even_with_other_terms_but_not_an_implausible_one(package):
    # sandbox D02: the rival's lower quote (its standard lead time broke our must-have) moved the bidder from our
    # first message on (.555 -> .702); a quote far below its price is cheap because of a term it breaks
    case = next(c for c in CASES.values() if len(c["suppliers"]) > 1)
    threads = threads_for(case)
    first, second = case["suppliers"][0], case["suppliers"][1]
    terms = threads[second][0]["term_sheets"][0]["terms"]
    other = {**terms, "unit_price": round(terms["unit_price"] * 0.93, 2), "lead_time_days": 999}
    threads[first][0]["term_sheets"][0]["terms"] = other
    found = claims._live_rival(case, second, threads, {})
    assert found and f"offer:{first[4:]}-0" in found[0].refs and found[0].figure == other["unit_price"]
    threads[first][0]["term_sheets"][0]["terms"] = {**other, "unit_price": round(terms["unit_price"] * 0.5, 2)}
    assert claims._live_rival(case, second, threads, {}) == []


@pytest.mark.parametrize("goal", ["leverage", "quality_first"])
def test_scorecard_when_the_supplier_meets_our_target_cites_no_scorecard(goal, package):
    """A scorecard on target praises the seller: never cited (company.md: on-time delivery target 95 %)."""
    case = copy.deepcopy(CASES["case:P05"])
    case["buyer"]["category_goal"] = goal
    sid = case["suppliers"][0]
    case["facts"] = {**case.get("facts", {}), f"sc:{sid[4:]}@2099Q4": {"supplier": sid, "quarter": "2099Q4",
                                                                        "otd_pct": 97.0, "ppm": 0}}  # fmt: skip
    cited = [c for c in claims.candidates(case, package, sid, threads_for(case)) if c.kind in ("delivery", "quality")]
    assert not cited, [c.sentence for c in cited]


def test_scorecard_when_delivery_is_under_target_cites_it(package):
    case = copy.deepcopy(CASES["case:P05"])
    sid = case["suppliers"][0]
    case["facts"] = {**case.get("facts", {}), f"sc:{sid[4:]}@2099Q4": {"supplier": sid, "quarter": "2099Q4",
                                                                        "otd_pct": 88.0, "ppm": 900}}  # fmt: skip
    found = claims.candidates(case, package, sid, threads_for(case))
    assert any(c.kind == "delivery" and c.figure == 88.0 for c in found)


def test_should_cost_when_the_estimate_is_above_todays_price_is_not_cited(package):
    case = copy.deepcopy(CASES["case:P01"])  # cost:310010 = 132.64
    case["buyer"]["current_unit_price"] = 126.53
    sid = case["suppliers"][0]
    found = claims.candidates(case, package, sid, threads_for(case, markup=1.10))
    assert not [c for c in found if c.kind == "cost"]
