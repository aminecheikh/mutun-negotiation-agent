"""tools/dossier.py: known rows of the real data package, and properties that must hold for any case."""

import copy
import json
import re
import time
from pathlib import Path

import pytest

from arena_kit.agents.buyer.tools import dossier
from arena_kit.data import DataPackage

DATA = Path(__file__).parents[2] / "data"
CASES = json.loads((Path(__file__).parent / "fixtures/practice_cases.json").read_text())
BY_ID = {c["case_id"]: c for c in CASES}
PERSONAS = {"cash_short", "full_order_book", "monopolist", "partner", "price_fighter", "unknown"}
ID = re.compile(r"\b[a-z]+:[\w.@#/-]+")


@pytest.fixture(scope="module")
def package():
    return DataPackage(DATA)


def case_for(supplier_id: str, part_id: str) -> dict:
    case = copy.deepcopy(BY_ID["case:P01"])
    case["suppliers"], case["parts"] = [supplier_id], [part_id]
    return case


def test_build_when_supplier_offers_skonto_is_cash_short_and_values_payment_days(package):
    d = dossier.build(case_for("sup:70210", "part:320070"), package, "sup:70210")
    assert d.persona == "cash_short" and "payment_days" in d.levers
    assert any("skonto" in line for line in d.evidence)


def test_build_when_liquidity_notes_and_credit_watch_is_cash_short(package):
    d = dossier.build(case_for("sup:70280", "part:330010"), package, "sup:70280")
    assert d.persona == "cash_short"
    assert any("doc:" in line for line in d.evidence) and any("news:" in line for line in d.evidence)


def test_build_when_news_says_fully_booked_is_full_order_book(package):
    d = dossier.build(BY_ID["case:P09"], package, "sup:70220")
    assert d.persona == "full_order_book" and "lead_time_days" in d.levers


def test_build_when_supplier_has_renewals_reports_mean_concession(package):
    rows = package.query(
        "SELECT AVG(asked_pct - settled_pct) AS g FROM negotiation_history "
        "WHERE supplier_id = 'sup:70110' AND asked_pct IS NOT NULL"
    )
    d = dossier.build(BY_ID["case:P01"], package, "sup:70110")
    assert d.history_give_pct == pytest.approx(rows[0]["g"])
    assert d.name and d.country == "DE"


def test_build_when_part_is_dual_sourced_reports_second_source(package):
    assert dossier.build(BY_ID["case:P01"], package, "sup:70110").second_source is True
    assert dossier.build(case_for("sup:70120", "part:310031"), package, "sup:70120").second_source is False


def test_build_when_known_supplier_every_evidence_line_carries_an_id(package):
    d = dossier.build(BY_ID["case:P01"], package, "sup:70110")
    assert d.evidence and all(ID.search(line) for line in d.evidence)
    assert any("turnover" in line for line in d.evidence)


def test_build_when_supplier_is_unknown_uses_only_case_facts(package):
    case = case_for("sup:99999", "part:999999")
    case["facts"] = {"doc:new-letter": {"supplier": "sup:99999", "date": "2026-10-02", "asked_price": 12.5}}
    d = dossier.build(case, package, "sup:99999")
    assert d.name == "sup:99999" and d.persona == "unknown" and d.history_give_pct is None
    assert len(d.evidence) == 1 and "doc:new-letter" in d.evidence[0]


def test_build_when_no_data_package_returns_a_bare_dossier():
    d = dossier.build(BY_ID["case:P01"], None, "sup:70110")
    assert d.supplier_id == "sup:70110" and d.persona == "unknown" and d.evidence == ()


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_build_when_any_case_keeps_its_invariants(case, package):
    for sid in [*case["suppliers"], "sup:99999"]:
        start = time.monotonic()
        d = dossier.build(case, package, sid)
        assert time.monotonic() - start < 0.5
        assert d.supplier_id == sid and d.persona in PERSONAS
        assert len(d.evidence) <= dossier.MAX_EVIDENCE
        assert all(isinstance(lever, str) for lever in d.levers)
