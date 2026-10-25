"""New cases: the whole deterministic path - arguments, planner, e-mail, guard - on every practice case file under
mutations the hidden cases may bring (a supplier or part the data has never seen, other price levels, a larger
option menu, an RFQ of unknown bidders). Every round must give a message the guard accepts or, where nothing beats
the supplier's standing sheet, a valid fallback (accept it, or repeat our offer). Nothing here is tuned to a case:
it checks that the rules make valid choices where the data says nothing."""

import copy
import json
from pathlib import Path

import pytest

from arena_kit.agents.buyer import guard, observe, policy
from arena_kit.agents.buyer.agent import BuyerAgent
from arena_kit.agents.buyer.config import OrchestratorConfig
from arena_kit.agents.buyer.pipeline import repeat_message
from arena_kit.agents.buyer.roles import LLMWriter, RuleStrategist
from arena_kit.agents.buyer.telemetry import Telemetry
from arena_kit.agents.buyer.tools import DataTools
from arena_kit.cost import complete_offer
from arena_kit.data import DataPackage

DATA = Path(__file__).parents[2] / "data"
CASES = json.loads((Path(__file__).parent / "fixtures/practice_cases.json").read_text())
ROUNDS = 3


def unknown_supplier(case: dict) -> dict:
    out = copy.deepcopy(case)
    out["suppliers"] = ["sup:99999"]
    return out


def unknown_part(case: dict) -> dict:
    out = copy.deepcopy(case)
    out["parts"] = ["part:999999"]
    return out


def prices_x100(case: dict) -> dict:
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
    out.setdefault("term_options", {}).update(
        {"payment_days": [14, 30, 45, 60, 90, 120], "lead_time_days": [7, 21, 35, 56], "contract_months": [6, 12, 36]}
    )
    return out


def unknown_rfq(case: dict) -> dict:
    out = copy.deepcopy(case)
    out["suppliers"] = ["sup:90001", "sup:90002", "sup:90003"]
    return out


MUTATIONS = {"as_is": lambda c: c, "unknown_supplier": unknown_supplier, "unknown_part": unknown_part,
             "prices_x100": prices_x100, "many_options": many_options, "unknown_rfq": unknown_rfq}  # fmt: skip


@pytest.fixture(scope="module")
def tools():
    return DataTools(DataPackage(DATA))


def opening(case: dict) -> dict[str, list[dict]]:
    base = case["buyer"].get("current_unit_price") or case["buyer"]["fallback_unit_price"]
    return {
        sid: [{"from": f"sales@{sid[4:]}", "status": "open", "body": "Our offer.", "attachments": [],
               "term_sheets": [{"term_sheet_id": f"offer:{sid[4:]}-0",
                                "terms": complete_offer(case, {"unit_price": round(base * (1.04 + 0.02 * i), 2)})}]}]
        for i, sid in enumerate(case["suppliers"])
    }  # fmt: skip


def reply_to(sid: str, rnd: int, theirs: dict, ours: float) -> dict:
    price = round(max(ours, theirs["unit_price"] * 0.99), 2)  # it moves 1 % and never under our offer
    sheet = {"term_sheet_id": f"offer:{sid[4:]}-{rnd}", "terms": {**theirs, "unit_price": price}}
    return {
        "from": f"sales@{sid[4:]}",
        "status": "open",
        "body": "Revised offer.",
        "attachments": [],
        "term_sheets": [sheet],
    }


@pytest.mark.parametrize("mutation", list(MUTATIONS))
def test_new_cases_every_case_file_gets_guard_clean_messages_round_after_round(mutation, tools):
    for raw in CASES:
        case = MUTATIONS[mutation](raw)
        agent = BuyerAgent(tools, None, None, RuleStrategist(), LLMWriter(None), tools.package, OrchestratorConfig(),
                           Telemetry("off"))  # fmt: skip
        session = agent.open_case(case, opening(case))
        for rnd in range(1, ROUNDS + 1):
            for sid, th in session.threads.items():
                if not th.alive:
                    continue
                ctx = agent.pipeline.round_context(session, th)
                tactic = RuleStrategist().choose(ctx, {sid: th.events}, {})
                plan = tools.plan_offers(ctx, sid, agent.pipeline.threads(session), tactic)
                email = LLMWriter(None).write(ctx, tactic, plan, th.events)
                checked = guard.build_message(session, th, plan.term_sheets, email)
                if checked is None or not checked.message["term_sheets"]:
                    # nothing beats its standing sheet: the runner takes it if acceptable, else repeats our offer
                    assert policy.acceptable(session, th) or repeat_message(session, th), (
                        case["case_id"],
                        mutation,
                        rnd,
                    )
                    th.alive = False
                    continue
                sent = guard.reserve(session, th, checked.message)
                theirs = th.events[-1]["term_sheets"][0]["terms"]
                th.events = [
                    *th.events,
                    {"from": "buyer", **sent},
                    reply_to(sid, rnd, theirs, sent["term_sheets"][0]["terms"]["unit_price"]),
                ]
            observe.rebuild_case(session)
