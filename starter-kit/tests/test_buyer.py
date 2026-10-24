"""The buyer agent skeleton: tools and guard as pure functions, and whole games against arena-sim (no LLM)."""

import json
import secrets
from pathlib import Path

from arena_kit import ArenaClient, RemoteTable, play
from arena_kit.agents.buyer import BuyerAgent, buyer_factory
from arena_kit.agents.buyer.contracts import BehaviourRead, Tactic
from arena_kit.agents.buyer.guard import scrub_ids
from arena_kit.agents.buyer.roles import PassthroughProfiler, RuleAnalyst
from arena_kit.agents.buyer.tools import DataTools
from arena_kit.agents.buyer.tools.concession import fit

CASE = json.loads((Path(__file__).parents[1] / "arena_kit/sim_cases/S01.json").read_text())
SID = "sup:70270"


def opening(price: float = 17.4) -> dict[str, list[dict]]:
    sheet = {"term_sheet_id": "offer:70270-0", "terms": {"unit_price": price, "payment_days": 30}}
    return {SID: [{"from": "sales@70270", "status": "open", "term_sheets": [sheet], "attachments": []}]}


def test_prepare_when_opening_quote_sets_target_below_opening_price():
    ctx = DataTools().prepare(CASE, opening())
    band = ctx.bands[SID]
    assert band.target < 17.4 and band.ceiling <= 17.4 and band.c_target < band.c_ceiling


def test_plan_offers_when_first_round_returns_complete_sheet_at_anchor():
    tools, threads = DataTools(), opening()
    ctx = tools.prepare(CASE, threads)
    read = BehaviourRead(SID, 0.6, 0.1, (), ())
    plan = tools.plan_offers(ctx, SID, threads[SID], Tactic(SID, "send"), read)
    sheet = plan.term_sheets[0]
    anchor = round(min(2 * ctx.bands[SID].target - 17.4, ctx.bands[SID].target), 2)  # midpoint lands on target
    assert set(sheet) == set(CASE["negotiable"]) and sheet["unit_price"] == anchor


def test_concession_fit_when_steps_shrink_reports_shrinking():
    events = [
        {"from": "s", "term_sheets": [{"terms": {"unit_price": p}}]} if p else {"from": "buyer"}
        for p in (20.0, None, 18.0, None, 17.0, None, 16.5)
    ]
    result = fit(SID, events)
    assert result.prices == (20.0, 18.0, 17.0, 16.5) and result.shrinking and round(result.last_step_pct, 2) == 2.94


def test_scrub_ids_when_unknown_id_removes_it_and_keeps_known():
    text = "Prices fell 3.7 % (idx:PPI-D351@2025-08, idx:FAKE@2026-08)."
    assert scrub_ids(text, lambda ref: ref == "idx:PPI-D351@2025-08") == "Prices fell 3.7 % (idx:PPI-D351@2025-08, )."


def test_buyer_agent_when_played_against_sim_finishes_every_case(sim, monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)  # template writer: no LLM calls in tests
    url = sim[0]
    key = ArenaClient(url).register(f"test-{secrets.token_hex(3)}", "sim")["api_key"]
    client = ArenaClient(url, key, rate=1000)
    new_agent = buyer_factory(None)
    for case in client.cases("practice"):
        result = play(RemoteTable(client), case["case_id"], new_agent())
        assert result["ended"], case["case_id"]
        assert result["score"] is None or 0 <= result["score"] <= 1


def test_buyer_agent_when_a_role_raises_falls_back_to_naive_move():
    class Broken:
        def choose(self, *args):
            raise RuntimeError("boom")

    tools = DataTools()
    agent = BuyerAgent(tools, PassthroughProfiler(), RuleAnalyst(), Broken(), None)
    decision = agent.decide(CASE, opening())
    assert decision["supplier_id"] == SID and decision["message"].get("term_sheets")


# ---- LLM client: a call we stopped waiting for must not hold a pool worker -------------------------------------


class FakeAnthropic:
    """Stands in for the SDK at the system boundary: records how the client was built."""

    built: dict = {}

    def __init__(self, **kwargs):
        FakeAnthropic.built = kwargs


def test_llm_from_env_when_anthropic_never_retries_and_times_out_at_the_given_budget(monkeypatch):
    import anthropic

    from arena_kit.agents.llm import llm_from_env

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "claude-test")
    llm_from_env(timeout_s=17.0)
    assert FakeAnthropic.built == {"timeout": 17.0, "max_retries": 0}


def test_buyer_factory_when_model_set_bounds_the_client_by_the_role_budget(monkeypatch):
    import anthropic

    from arena_kit.agents.buyer.config import OrchestratorConfig

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "claude-test")
    buyer_factory()
    cfg = OrchestratorConfig.from_env()
    assert FakeAnthropic.built["max_retries"] == 0
    assert FakeAnthropic.built["timeout"] <= max(cfg.strategist_timeout_s, cfg.writer_timeout_s)
