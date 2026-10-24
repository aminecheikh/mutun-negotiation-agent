"""The whole decision chain on cases we have never seen (owner: Saed): every practice case file (9 never played)
and mutated copies - unknown supplier and part, bundles, other price levels, many options - in four thread
states. Rule path only (no model, no API credit), the path every role falls back to in the final. Each move
must not raise, and every sheet must pass the arena's own checks (api.md 422: every negotiable field, values
within the allowed options, at most 3 sheets) - a refused sheet costs a round."""

import pytest
from test_tools_robustness import CASES, IDS, MUTATIONS, opening_threads

from arena_kit.agents.buyer.roles import LLMWriter, RuleAnalyst, RuleStrategist
from arena_kit.agents.buyer.roles.strategist import safe
from arena_kit.agents.buyer.tools import DataTools
from arena_kit.cost import complete_offer

GERMAN = (
    "Guten Tag, anbei unser Angebot. Ohne Bewegung Ihrerseits koennen wir uns nicht bewegen. Mit freundlichen Gruessen"
)


def states(case: dict, sid: str, opening: list[dict]) -> dict[str, list[dict]]:
    """Opening; mid-negotiation with a cheaper alternative; final offer; a German supplier."""
    terms = opening[0]["term_sheets"][0]["terms"]
    lower = {**terms, "unit_price": round(terms["unit_price"] * 0.98, 2)}
    alt = complete_offer(case, {**lower, "unit_price": round(terms["unit_price"] * 0.95, 2)})
    ours = {"from": "buyer", "body": "Our proposal.", "refs": [],
            "term_sheets": [{"terms": {**terms, "unit_price": round(terms["unit_price"] * 0.9, 2)}}]}  # fmt: skip

    def reply(status, sheets, body="Our revised offer."):
        sheets = [{"term_sheet_id": f"offer:{sid}-{i}", "terms": t} for i, t in enumerate(sheets)]
        return {"from": f"sales@{sid}", "status": status, "body": body, "attachments": [], "term_sheets": sheets}

    return {
        "opening": opening,
        "alternative": [*opening, ours, reply("open", [lower, alt])],
        "final": [*opening, ours, reply("final_offer", [lower])],
        "german": [{**opening[0], "body": GERMAN}],
    }


def sheet_problems(case: dict, sheet: dict) -> list[str]:
    out = [f"missing {f}" for f in case["negotiable"] if f not in sheet]
    for field, options in (case.get("term_options") or {}).items():
        if field in sheet and options and sheet[field] not in options:
            out.append(f"{field}={sheet[field]!r} not an option")
    return out


@pytest.fixture(scope="module")
def tools():
    from test_tools_robustness import DATA

    from arena_kit.data import DataPackage

    return DataTools(DataPackage(DATA))


@pytest.mark.parametrize("mutate", MUTATIONS, ids=IDS)
@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_rule_path_when_any_case_is_mutated_sends_valid_sheets_and_never_raises(case, mutate, tools):
    case = mutate(case)
    threads = opening_threads(case)
    ctx = tools.prepare(case, threads)
    for sid, opening in threads.items():
        for name, events in states(case, sid, opening).items():
            view = {sid: events}
            ctx_r = tools.refresh(ctx, view)
            fit = tools.fit_concessions(ctx_r, sid, events)
            reads = {sid: RuleAnalyst().read(ctx_r, sid, events, fit)}
            rule = RuleStrategist().choose(ctx_r, view, reads)
            tactic = safe(ctx_r, view, {"action": "send", "levers": ["made_up"], "claims": [99]}, rule)
            assert tactic.supplier_id == sid and tactic.action in ("send", "accept", "walk_away"), name
            if tactic.action != "send":
                continue
            plan = tools.plan_offers(ctx_r, sid, events, tactic, reads[sid])
            assert 1 <= len(plan.term_sheets) <= 3, (name, plan)
            for sheet in plan.term_sheets:
                assert not sheet_problems(case, sheet), (name, sheet_problems(case, sheet))
            email = LLMWriter(None).write(ctx_r, tactic, plan, events)
            assert email.body.strip() and len(email.subject) <= 300, name
