"""S4 profiler (owner: Saed): the model's profile only where the evidence backs it, cache first, live only for
case facts, the rules' dossier whenever anything goes wrong."""

import json

from arena_kit.agents.buyer.contracts import Dossier
from arena_kit.agents.buyer.roles import LLMProfiler
from arena_kit.agents.buyer.roles.profiler import CACHE, apply, load_cache, validated

SID = "sup:70999"
EVIDENCE = (
    "2026-07-01 call_note 'Material purchasing': bought their copper forward until March 2027 (doc:A1)",
    "2026-04-26 meeting_note 'Annual review': price changes above 2 % need a pricing committee (doc:A2)",
    "2026-10-05 email 'Holiday shutdown': plant closed 23 Dec to 6 Jan (doc:A3)",
)
DOSSIER = Dossier(SID, "Kupferwerk Test", "DE", "unknown", ("contract_months",), 0.5, False, EVIDENCE)
GOOD = {
    "persona": "monopolist",
    "levers": ["effective_date", "made_up", "payment_days"],
    "traits": [
        {"trait": "hedged", "until": "2027-03", "why": "copper bought forward", "id": "doc:A1"},
        {"trait": "pricing_committee", "until": "soon", "why": "committee above 2 %", "id": "doc:A2"},
        {"trait": "hedged", "until": None, "why": "an invented source", "id": "doc:ZZZ"},
        {"trait": "telepathy", "until": None, "why": "?", "id": "doc:A1"},
    ],
    "decisive": [EVIDENCE[1], "a line that is not in the evidence"],
    "confidence": 0.7,
}


def test_validated_keeps_only_what_the_evidence_backs():
    p = validated(GOOD, DOSSIER)
    assert p["persona"] == "monopolist" and p["levers"] == ["effective_date", "payment_days"]
    assert [(t["trait"], t["until"], t["id"]) for t in p["traits"]] == [
        ("hedged", "2027-03", "doc:A1"),
        ("pricing_committee", None, "doc:A2"),
    ]
    assert p["decisive"] == [EVIDENCE[1]]


def test_validated_when_persona_unknown_word_keeps_rules_persona():
    assert validated({"persona": "villain", "confidence": "x"}, DOSSIER)["persona"] == "unknown"


def test_apply_puts_trait_lines_first_and_levers_in_front():
    d = apply(DOSSIER, validated(GOOD, DOSSIER))
    assert d.persona == "monopolist" and d.levers[:2] == ("effective_date", "payment_days")
    assert d.evidence[0] == "profile: hedged until 2027-03 - copper bought forward (doc:A1)"
    assert set(EVIDENCE) <= set(d.evidence)


def test_apply_when_low_confidence_keeps_a_known_rules_persona():
    rules = Dossier(SID, "x", "DE", "partner", (), None, False, EVIDENCE)
    assert apply(rules, {**validated(GOOD, rules), "confidence": 0.2}).persona == "partner"


def test_refine_when_cached_and_no_case_facts_makes_no_call():
    def boom(system, user):
        raise AssertionError("no LLM call expected")

    profiler = LLMProfiler(boom, cache={SID: validated(GOOD, DOSSIER)})
    assert profiler.refine({}, DOSSIER).persona == "monopolist"


def test_refine_when_case_brings_facts_calls_the_model_once():
    facts = Dossier(SID, "x", "DE", "unknown", (), None, False, ('case fact doc:N1: {"subject": "new letter"}',))
    calls = []

    def complete(system, user):
        calls.append(user)
        return json.dumps({"persona": "cash_short", "levers": ["payment_days"], "traits": [], "confidence": 0.8})

    profiler = LLMProfiler(complete, cache={})
    assert profiler.refine({}, facts).persona == "cash_short"
    assert profiler.refine({}, facts).persona == "cash_short" and len(calls) == 1  # cached for this process


def test_refine_when_model_fails_returns_rules_dossier():
    profiler = LLMProfiler(lambda s, u: "no json here", cache={})
    assert profiler.refine({}, DOSSIER) == DOSSIER


def test_precomputed_cache_is_valid_for_every_supplier():
    cache = load_cache(CACHE)
    assert len(cache) >= 40
    for sid, p in cache.items():
        assert p["persona"] in {"cash_short", "full_order_book", "monopolist", "partner", "price_fighter", "unknown"}
        assert all(t["id"] for t in p["traits"]), sid
