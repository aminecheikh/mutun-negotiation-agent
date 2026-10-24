"""Buyer request limits must reach the provider, including its otherwise hidden retries."""

import sys
from types import SimpleNamespace

from arena_kit.agents.buyer.agent import buyer_factory
from arena_kit.agents.llm import llm_from_env


def test_buyer_disables_anthropic_retries_and_shortens_timeout(monkeypatch):
    clients = []

    def client(**kwargs):
        clients.append(kwargs)
        return SimpleNamespace(messages=None)

    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=client))
    factory = buyer_factory()
    assert callable(factory)
    assert clients == [{"timeout": 12.0, "max_retries": 0}]
    # Offline precomputation and the kit example retain their original request budget.
    llm_from_env()
    assert clients[-1] == {"timeout": 45.0, "max_retries": 2}


def test_compatible_provider_uses_requested_timeout(monkeypatch):
    requests = []

    def post(url, **kwargs):
        requests.append((url, kwargs))
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": {"content": "test reply"}}]},
        )

    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "https://test.invalid/v1")
    monkeypatch.setattr("arena_kit.agents.llm.httpx.post", post)
    complete = llm_from_env(timeout_s=12.0, max_retries=0)
    assert complete("system", "user") == "test reply"
    assert len(requests) == 1
    assert requests[0][1]["timeout"] == 12.0


def test_rule_only_buyer_does_not_construct_provider(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)

    def unexpected(**kwargs):
        raise AssertionError("Rule-only buyer must not construct a provider client")

    monkeypatch.setattr("arena_kit.agents.buyer.agent.llm_from_env", unexpected)
    assert callable(buyer_factory())
