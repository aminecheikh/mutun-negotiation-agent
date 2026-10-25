"""Writer (owner: Saed): the deterministic e-mail whenever the decision agent follows the rules (no model call), a
pushed message's paragraph from the model only when it passes `check`, the agent's question after the paragraph."""

import json
from pathlib import Path

import pytest

from arena_kit.agents.buyer.contracts import CaseContext, Claim, Dossier, OfferPlan, Tactic
from arena_kit.agents.buyer.roles.writer import PUSH_LINE, LLMWriter, check
from arena_kit.agents.buyer.tools import message, planner
from arena_kit.cost import complete_offer

CASES = {c["case_id"]: c for c in json.loads((Path(__file__).parent / "fixtures/practice_cases.json").read_text())}
CASE = CASES["case:P10"]
SID = CASE["suppliers"][0]
CLAIM = Claim("On-time delivery was 79.5% (sc:70410@2026Q3).", ("sc:70410@2026Q3",), "delivery", 79.5, True, 1.0)
SUPPLIER_MAIL = "We offer EUR 31.70 per unit. Our calculation is our property; we do not share a breakdown."
STANDING = complete_offer(CASE, {"unit_price": 31.7})
EVENTS = [{"from": "sales@70410", "status": "open", "body": SUPPLIER_MAIL,
           "term_sheets": [{"term_sheet_id": "offer:70410-1", "terms": STANDING}]}]  # fmt: skip
PLAN = OfferPlan(SID, (complete_offer(CASE, {"unit_price": 28.26}),), (1.0,), "")
FOLLOW = Tactic(SID, "send", (0,))
PUSH = Tactic(SID, "send", (0,), price="push", rationale="it still moves in real steps")


def ctx(persona: str = "unknown") -> CaseContext:
    dossier = Dossier(SID, "Dichtungswerk", "DE", persona, (), None, False, ())
    return CaseContext(CASE, {SID: dossier}, {SID: (CLAIM,)}, None, unit="pc")


def never(system, user):
    raise AssertionError("the deterministic e-mail needs no model")


def expected(paragraph: str) -> str:
    brief = planner.commercial_brief(CASE, list(PLAN.term_sheets), EVENTS, "pc")
    return message.compose(paragraph, brief, [CLAIM.sentence])


def test_write_when_the_agent_follows_sends_the_deterministic_email_without_a_model_call():
    email = LLMWriter(never).write(ctx(), FOLLOW, PLAN, EVENTS)
    assert email.body == expected(message.NARRATIVES["confidential"])  # it refused a breakdown
    assert email.subject == f"Re: {CASE['title']}" and email.refs == CLAIM.refs


def test_write_when_the_agent_asks_a_question_puts_it_after_the_paragraph():
    tactic = Tactic(SID, "send", (0,), "Which contract length would let you improve the price?")
    email = LLMWriter(never).write(ctx(), tactic, PLAN, EVENTS)
    assert email.body == expected(f"{message.NARRATIVES['confidential']} {tactic.question}")


def test_write_when_pushed_uses_the_model_paragraph_that_passes_the_check():
    text = "We appreciate the effort so far. For these commitments the price has to come down further to agree."
    writer = LLMWriter(lambda system, user: json.dumps({"paragraph": text}))
    email = writer.write(ctx(), PUSH, PLAN, EVENTS)
    assert writer.last_used_model and email.body == expected(text)


@pytest.mark.parametrize(
    "text",
    [
        "Please lower the price to EUR 27 for these commitments.",  # a figure
        "Your scorecard sc:70410@2026Q3 shows weak delivery, so lower the price.",  # a data ID
        "We need an answer urgently, so please lower the price now.",  # a leak
        "Market prices are lower than your offer, please improve it.",  # data we do not cite
        "We have moved towards you; now the price has to come down.",  # a move we did not make
    ],
)
def test_write_when_the_pushed_paragraph_breaks_a_rule_uses_the_rule_paragraph(text):
    writer = LLMWriter(lambda system, user: json.dumps({"paragraph": text}))
    email = writer.write(ctx(), PUSH, PLAN, EVENTS)
    assert not writer.last_used_model and writer.last_problems
    assert email.body == expected(f"{message.NARRATIVES['confidential']} {PUSH_LINE}")


def test_write_when_pushed_without_a_model_uses_the_rule_paragraph_and_the_push_line():
    email = LLMWriter(None).write(ctx(), PUSH, PLAN, EVENTS)
    assert email.body == expected(f"{message.NARRATIVES['confidential']} {PUSH_LINE}")


def test_check_when_the_supplier_used_the_words_first_allows_them():
    text = "As you mention other suppliers yourself, please compare the packages and improve the price."
    assert check(text, "Other suppliers charge the same.") == []
    assert check(text) != []


def test_check_when_too_short_or_too_long_rejects():
    assert check("Lower it.") and check("Please improve the price. " * 60)
