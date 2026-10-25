"""tools/message.py: the deterministic e-mail - which paragraph the rules choose, what they read from the supplier's
documents and replies, and the fixed layout (intro, paragraph, commitment, arguments, sign-off)."""

import copy
import json
from pathlib import Path

import pytest

from arena_kit.agents.buyer.tools import message, planner
from arena_kit.cost import complete_offer
from arena_kit.data import DataPackage

DATA = Path(__file__).parents[2] / "data"
CASES = {c["case_id"]: c for c in json.loads((Path(__file__).parent / "fixtures/practice_cases.json").read_text())}


@pytest.fixture(scope="module")
def package():
    return DataPackage(DATA)


def case_with(goal: str = "leverage", negotiable=("unit_price", "contract_months", "payment_days")) -> dict:
    case = copy.deepcopy(CASES["case:P10"])
    case["buyer"]["category_goal"] = goal
    case["negotiable"] = list(negotiable)
    return case


def reply(body: str) -> dict:
    return {"from": "sales@x", "status": "open", "body": body}


def thread(*bodies: str, ours: int = 0) -> list[dict]:
    events = []
    for i, body in enumerate(bodies):
        events.append(reply(body))
        if i < ours:
            events.append({"from": "buyer", "body": "Our proposal."})
    return events


@pytest.mark.parametrize(
    ("goal", "documents", "events", "key"),
    [
        ("leverage", "", thread("We do not share our calculation."), "confidential"),
        ("secure_supply", "", thread("Offer attached."), "secure_supply"),
        ("quality_first", "", thread("Offer attached."), "quality_first"),
        ("leverage", "", thread("A price reduction is impossible this year."), "price_locked"),
        ("leverage", "Prices are set centrally by our head office.", thread("Offer attached."), "price_locked"),
        ("leverage", "", thread("Our copper is hedged until spring."), "hedged"),
        ("leverage", "Our bank is watching our liquidity.", thread("Offer attached."), "cash"),
        ("leverage", "", thread("Offer attached.", "Offer attached.", "No movement on your side.", ours=2), "approval"),
        ("leverage", "", thread("Offer attached."), "continuity"),
    ],
)
def test_narrative_key_follows_objection_then_goal_then_signals(goal, documents, events, key):
    assert message.narrative_key(case_with(goal), documents, events) == key


def test_narrative_key_when_a_breakdown_is_refused_answers_that_before_the_goal():
    events = thread("Our calculation is confidential.")
    assert message.narrative_key(case_with("secure_supply"), "", events) == "confidential"


def test_narrative_key_when_cash_matters_but_payment_is_fixed_does_not_offer_early_payment():
    case = case_with(negotiable=("unit_price",))
    assert message.narrative_key(case, "We need early payment.", thread("Offer attached.")) == "default"


def test_narrative_key_when_stalled_before_our_second_message_keeps_the_paragraph():
    assert message.narrative_key(case_with(), "", thread("No movement on your side.")) == "continuity"


def test_objections_read_only_its_latest_reply():
    events = thread("We do not share our calculation.", "Which volume can you commit to?", ours=1)
    assert message.objections(events) == {
        "volume": True,
        "breakdown_refused": False,
        "figures_asked": False,
        "stalled": False,
    }


def test_signals_read_documents_and_every_reply():
    events = thread("Our management decides.", "Early payment would help our cash flow.", ours=1)
    sig = message.signals("", events)
    assert sig["approval_needed"] and sig["cash_sensitive"] and not sig["price_locked"] and not sig["hedged"]


def test_compose_lays_the_e_mail_out_line_by_line():
    body = message.compose("Paragraph.", "Brief.", ["Argument one.", "Argument two."])
    assert body.split("\n") == [message.INTRO, "Paragraph.", "Brief.", "Argument one.", "Argument two.",
                                message.SIGN_OFF]  # fmt: skip


def test_commercial_brief_states_the_lead_package_and_a_firm_volume_when_asked():
    case = CASES["case:P10"]
    sheet = complete_offer(case, {"unit_price": 28.26})
    asked = [{"from": "sales@x", "status": "open", "body": "Which volume can you commit to?",
              "term_sheets": [{"term_sheet_id": "o", "terms": sheet}]}]  # fmt: skip
    brief = planner.commercial_brief(case, [sheet], asked, "pc")
    assert brief.startswith("Our lead package commits ") and " pc per year" in brief and "EUR 28.26" in brief
    if "volume_per_year" not in case["negotiable"]:
        assert planner.FIRM.strip() in brief


def test_supplier_documents_reads_its_letters_and_contracts(package):
    for case in CASES.values():
        for sid in case["suppliers"]:
            text = message.supplier_documents(case, package, sid)
            assert isinstance(text, str)
    case = CASES["case:P01"]
    assert message.supplier_documents(case, package, case["suppliers"][0])  # its contract at least
    assert message.supplier_documents(case, None, case["suppliers"][0]) == ""


def test_unit_of_reads_the_part_and_gives_units_for_a_bundle(package):
    single = next(c for c in CASES.values() if len(c["parts"]) == 1)
    assert message.unit_of(single, package) in message.UNITS
    bundle = next((c for c in CASES.values() if len(c["parts"]) > 1), None)
    if bundle:
        assert message.unit_of(bundle, package) == "units"
    assert message.unit_of(single, None) == "units"


@pytest.mark.parametrize("asked", [
    "Which figures is that based on?",
    "Please send us the exact figures for the costs you say our late deliveries caused.",
    "What data do you base this on?",
])  # fmt: skip
def test_narrative_when_the_supplier_asks_for_our_figures_answers_with_the_references(asked):
    case = {"buyer": {"category_goal": "leverage"}, "negotiable": ["unit_price"]}
    events = [{"from": "supplier", "body": asked}]
    assert message.objections(events)["figures_asked"]
    assert message.narrative_key(case, "", events) == "figures"
    assert not any(ch.isdigit() for ch in message.NARRATIVES["figures"])


def test_compose_thanks_the_supplier_once():
    for key in message.NARRATIVES:
        body = message.compose(message.NARRATIVES[key], "", [])
        assert body.count("Thank you for your proposal") <= 1, key
