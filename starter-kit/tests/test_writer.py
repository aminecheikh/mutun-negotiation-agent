"""Writer v1 (owner: Saed): the check that stops invented numbers, IDs and leaks, the persona template, and a
replay of recorded supplier e-mails from runs/sessions/ (skipped when none were harvested)."""

import json
from pathlib import Path

import pytest

from arena_kit.agents.buyer.contracts import CaseContext, Claim, Dossier, OfferPlan, Tactic
from arena_kit.agents.buyer.roles.writer import LLMWriter, check, template

SID = "sup:70110"
CLAIM = Claim(
    sentence="Electricity producer prices changed -3.7% from 2025-08 to 2026-08 "
    "(idx:PPI-D351@2025-08, idx:PPI-D351@2026-08).",
    refs=("idx:PPI-D351@2025-08", "idx:PPI-D351@2026-08"),
    kind="index_change",
    figure=-3.7,
    favours_buyer=True,
    strength=0.6,
)
SUPPLIER_MAIL = "Guten Tag, we can offer EUR 138.55 per unit from 1 November 2026. Could you confirm your volumes?"
EVENTS = [{"from": "sales@70110", "status": "open", "subject": "Angebot HN volute casing", "body": SUPPLIER_MAIL}]
RUNS = Path(__file__).parents[1] / "runs" / "sessions"


def ctx(persona: str = "partner") -> CaseContext:
    dossier = Dossier(SID, "Giesserei Brandlhuber", "DE", persona, ("payment_days",), None, False, ())
    case = {"case_id": "case:T01", "title": "HN volute casings", "briefing": ""}
    return CaseContext(case, {SID: dossier}, {}, {SID: (CLAIM,)}, None)


TACTIC = Tactic(SID, "send", ("payment_days", "effective_date"), (0,), "Could you send us a cost breakdown?")
PLAN = OfferPlan(SID, ({"unit_price": 129.9, "payment_days": 60},), (100.0,), "")


def body(*middle: str) -> str:
    return "\n".join(["Dear Sir or Madam,", *middle, "Kind regards\nHollerau Pumpenwerke, Purchasing"])


def run_check(text: str) -> list[str]:
    return check(text, [CLAIM.sentence], set(CLAIM.refs), SUPPLIER_MAIL, "HN volute casings")


def test_check_when_clean_email_has_no_problems():
    assert run_check(body("thank you for your offer.", CLAIM.sentence, "Please find our proposal attached.")) == []


def test_check_when_claim_sentence_changed_reports_it():
    changed = CLAIM.sentence.replace("-3.7%", "-4%")
    assert any("claim sentence" in p for p in run_check(body(changed)))


def test_check_when_own_number_reports_it():
    problems = run_check(body(CLAIM.sentence, "We can pay EUR 125.00 per unit."))
    assert any("numbers" in p for p in problems)


def test_check_when_number_repeats_supplier_figure_allows_it():
    assert run_check(body(CLAIM.sentence, "Your price of EUR 138.55 is too high for us.")) == []


def test_check_when_unknown_data_id_reports_it():
    problems = run_check(body(CLAIM.sentence, "Your scorecard sc:70110@2026Q3 shows weak delivery."))
    assert any("data IDs" in p for p in problems)


@pytest.mark.parametrize("leak", ["We need this urgently.", "Our budget is limited.", "We have no alternative."])
def test_check_when_leak_reports_it(leak):
    assert any("leak" in p for p in run_check(body(CLAIM.sentence, leak)))


def test_template_when_partner_acknowledges_cooperation_and_passes_check():
    email = template(ctx("partner"), TACTIC, PLAN, [CLAIM.sentence], EVENTS)
    assert "cooperation" in email.body and email.subject == "Re: Angebot HN volute casing"
    assert run_check(email.body) == []


def test_writer_when_model_invents_a_price_falls_back_to_template_with_refs():
    def complete(system, user):
        return json.dumps({"subject": "Re: casings", "body": body(CLAIM.sentence, "We offer EUR 120.00.")})

    email = LLMWriter(complete).write(ctx(), TACTIC, PLAN, EVENTS)
    assert "120.00" not in email.body and CLAIM.sentence in email.body and email.refs == CLAIM.refs


def test_writer_when_model_follows_rules_keeps_its_email():
    text = body(
        "thank you for your offer and the long cooperation.", CLAIM.sentence, "Could you send us a cost breakdown?"
    )

    def complete(system, user):
        assert '"persona": "partner"' in user and '"round": 1' in user
        return json.dumps({"subject": "Re: casings", "body": text})

    email = LLMWriter(complete).write(ctx(), TACTIC, PLAN, EVENTS)
    assert email.body == text and email.refs == CLAIM.refs


def test_writer_when_no_model_uses_persona_template():
    email = LLMWriter(None).write(ctx("cash_short"), TACTIC, PLAN, EVENTS)
    assert "cash flow" in email.body and TACTIC.question in email.body


def recorded_supplier_mails() -> list[tuple[str, dict]]:
    mails = []
    for path in sorted(RUNS.glob("*.json")) if RUNS.exists() else []:
        raw = path.read_bytes()
        try:  # harvest.py writes with the platform encoding (cp1252 on Windows)
            record = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError:
            record = json.loads(raw.decode("cp1252"))
        for step in (record["session"].get("result") or {}).get("trace") or []:
            move = step.get("move") or {}
            if step.get("actor") != "buyer" and (move.get("text") or move.get("body")):
                mails.append(
                    (
                        path.stem,
                        {
                            "from": step["actor"],
                            "status": move.get("status"),
                            "body": move.get("text") or move.get("body"),
                        },
                    )
                )
    return mails


@pytest.mark.skipif(not recorded_supplier_mails(), reason="no harvested sessions in runs/sessions/")
def test_writer_on_recorded_supplier_mails_every_template_passes_check():
    for persona in ("partner", "cash_short", "full_order_book", "monopolist", "price_fighter", "unknown"):
        for name, mail in recorded_supplier_mails():
            email = LLMWriter(None).write(ctx(persona), TACTIC, PLAN, [mail])
            problems = check(email.body, [CLAIM.sentence], set(CLAIM.refs), mail["body"], "HN volute casings")
            assert problems == [], (name, persona, problems)


# ---- truthfulness: describe only what the sheets contain (sandbox D01, 16:59) -------------------------


def thread(prices_theirs, ours=None):
    events = []
    for i, p in enumerate(prices_theirs):
        terms = {"unit_price": p, "payment_days": 30, "contract_months": 12}
        events.append(
            {"from": "sales@70110", "status": "open", "body": "Our offer.", "term_sheets": [{"terms": terms}]}
        )
        if ours and i < len(ours):
            events.append({"from": "buyer", "term_sheets": [{"terms": {"unit_price": ours[i]}}]})
    return events


def test_template_when_sheet_trades_nothing_claims_no_trade():
    plan = OfferPlan(SID, ({"unit_price": 48.67, "payment_days": 30, "contract_months": 12},), (1.0,), "")
    email = template(ctx("unknown"), TACTIC, plan, [], thread([52.9, 51.9], ours=[48.67]))
    assert "payment" not in email.body.lower() and "commitment" not in email.body.lower()
    assert "moved towards" not in email.body  # 48.67 again is no move
    assert "the step you made" in email.body  # it came down from 52.90 to 51.90


def test_template_when_packages_trade_terms_names_each_trade():
    sheets = (
        {"unit_price": 49.5, "payment_days": 60, "contract_months": 12},
        {"unit_price": 50.0, "payment_days": 30, "contract_months": 24},
    )
    email = template(ctx("unknown"), TACTIC, OfferPlan(SID, sheets, (1.0, 2.0), ""), [], thread([52.9], ours=[]))
    assert "The first package offers a longer payment term." in email.body
    assert "The second package offers a longer commitment." in email.body


def test_template_when_we_raise_our_offer_says_we_moved():
    plan = OfferPlan(SID, ({"unit_price": 49.5, "payment_days": 30, "contract_months": 12},), (1.0,), "")
    email = template(ctx("unknown"), TACTIC, plan, [], thread([52.9, 51.9], ours=[48.67]))
    assert "We have moved towards your position." in email.body
