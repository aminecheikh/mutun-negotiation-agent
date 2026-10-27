"""Shared helpers for the v2 tests: case files, thread builders, a scripted model transport."""

import json
import re
import time
from pathlib import Path

from arena_kit.agents.buyer.tools import DataTools
from arena_kit.agents.buyer_v2 import facts as F
from arena_kit.agents.buyer_v2.brain import Brain
from arena_kit.agents.buyer_v2.config import V2Config

KIT = Path(__file__).parents[1]
DATA = KIT.parent / "data"
S01 = json.loads((KIT / "arena_kit/sim_cases/S01.json").read_text(encoding="utf-8"))  # one supplier
S02 = json.loads((KIT / "arena_kit/sim_cases/S02.json").read_text(encoding="utf-8"))  # RFQ, two suppliers
PRACTICE = json.loads((KIT / "tests/fixtures/practice_cases.json").read_text(encoding="utf-8"))
SID = S01["suppliers"][0]


def quote(sid, n, price, status="open", days=30, extra_sheets=(), body="Our offer.", **terms):
    sheet = {"term_sheet_id": f"offer:{sid[4:]}-{n}", "terms": {"unit_price": price, "payment_days": days, **terms}}
    sheets = [sheet] + [
        {"term_sheet_id": f"offer:{sid[4:]}-{n}-{i}", "terms": t} for i, t in enumerate(extra_sheets, 1)
    ]
    return {"from": f"sales@{sid[4:]}", "status": status, "term_sheets": sheets, "attachments": [], "body": body}


def ours(*sheets, body="Our proposal.", refs=()):
    return {"from": "buyer", "subject": "Re", "body": body, "refs": list(refs),
            "term_sheets": [{"terms": dict(s)} for s in sheets]}  # fmt: skip


def state_for(case, threads, package=None, **config) -> F.CaseState:
    brain = Brain(DataTools(package), None, V2Config(log_dir="off", **config))
    st = brain.open_case(case, threads)
    return F.refreshed(st, threads)


def text_block(obj) -> dict:
    return {"type": "text", "text": json.dumps(obj)}


class ScriptedModel:
    """A model transport with the Anthropic block shape: per role a scripted answer, an optional first tool turn,
    an optional delay. Records every request."""

    ROLE_MARKERS = {
        "reader": "Your job: the message reader",
        "counterpart": "Your job: the counterpart analyst",
        "market": "Your job: the market and cost analyst",
        "deal": "Your job: the deal economist",
        "negotiator": "Your job: the lead negotiator",
    }

    def __init__(self, answers: dict, tool_calls: dict | None = None, delay: dict | None = None):
        self.answers, self.tool_calls, self.delay = answers, tool_calls or {}, delay or {}
        self.requests: list[dict] = []

    def role_of(self, system: str) -> str:
        return next(r for r, marker in self.ROLE_MARKERS.items() if marker in system)

    def __call__(self, *, model, system, messages, tools, tool_choice, schema, effort, max_tokens, timeout_s):
        role = self.role_of(system)
        self.requests.append({"role": role, "messages": list(messages), "tools": [t["name"] for t in tools or []],
                              "tool_choice": tool_choice, "schema": schema, "timeout_s": timeout_s})  # fmt: skip
        time.sleep(self.delay.get(role, 0.0))
        first_turn = len(messages) == 1
        if first_turn and role in self.tool_calls and tool_choice and tool_choice.get("type") == "auto":
            uses = [
                {"type": "tool_use", "id": f"t{i}", "name": n, "input": a}
                for i, (n, a) in enumerate(self.tool_calls[role])
            ]
            return uses, "tool_use", {"input_tokens": 10}
        answer = self.answers.get(role)
        answer = answer(messages) if callable(answer) else answer
        return [text_block(answer)], "end_turn", {"input_tokens": 100, "output_tokens": 50}


def frame_value(user_text: str, pattern: str, cast=float):
    m = re.search(pattern, user_text)
    return cast(m.group(1)) if m else None


READER = {
    "accepted_our_offer": False, "acceptance_evidence": "", "offer_summary": "It holds its price.", "movement": "none",
    "wants": ["faster payment"], "values_terms": ["payment_days"], "refused_terms": [], "questions_to_answer": [],
    "its_arguments": ["energy"], "information_offered": [], "tone": "neutral", "relationship_signal": "calm",
    "walk_risk": 0.2, "room_to_move": 0.6, "language": "en", "red_flags": [], "key_quotes": [],
}  # fmt: skip
COUNTERPART = {
    "situation": "stable", "relationship": "long", "negotiation_behaviour": "gives a little", "profile": "unknown",
    "secondary_traits": [], "what_moves_it": ["payment"], "values_terms": ["payment_days"], "avoid_terms": [],
    "style_advice": "factual", "risks": [], "evidence": [], "confidence": 0.5,
}  # fmt: skip
MARKET = {
    "price_view": "above fair", "justified_change_pct": 1.0, "fair_price": None, "floor_estimate": None,
    "credible_lowest_price": None, "ambitious_target_price": None, "floor_confidence": "rough",
    "strongest_arguments": [{"token": "A1", "why": "fits"}], "arguments_to_avoid": [], "volume_trend": "stable",
    "risks": [], "confidence": 0.5,
}  # fmt: skip
DEAL = {
    "standing_c": None, "standing_acceptable": True, "why_not_acceptable": "", "accepted_our_offer": False,
    "room_assessment": "some", "gap_to_target_pct": 3.0, "recommended_action": "counter", "action_reasoning": "room",
    "candidates": [], "levers_ranked": [], "walk_away_point": "alternative", "confidence": 0.5,
}  # fmt: skip


def negotiator_counter(price_factor=0.95, days="60", tokens=("A1",), body=None, decision="counter"):
    """A negotiator answer that counters at `price_factor` x the supplier's current price (read from the frame)."""

    def answer(messages):
        user = messages[0]["content"]
        cap = frame_value(user, r"unit_price <= ([\d.]+) \(its current offer\)") or 10.0
        text = body or ("Dear Sir or Madam,\n\nthank you for your offer and the step you made. We value the "
                        "cooperation and propose the packages attached, because a balanced deal serves both of us.\n"
                        + "\n".join(f"[[{t}]]" for t in tokens)
                        + "\nCould you send us a breakdown of your cost elements?\n\nKind regards\n"
                        "Hollerau Pumpenwerke, Purchasing")  # fmt: skip
        return {
            "decision": decision, "reasoning": "it still moves; trade payment for price",
            "counter_offers": [{"unit_price": round(cap * price_factor, 2),
                                "terms": [{"field": "payment_days", "value": days}], "label": "main"}],
            "arguments": list(tokens), "email_subject": "Re: your offer", "email_body": text,
        }  # fmt: skip

    return answer


def scripted(**overrides) -> ScriptedModel:
    answers = {"reader": READER, "counterpart": COUNTERPART, "market": MARKET, "deal": DEAL,
               "negotiator": negotiator_counter()}  # fmt: skip
    answers.update(overrides.pop("answers", {}))
    return ScriptedModel(answers, **overrides)
