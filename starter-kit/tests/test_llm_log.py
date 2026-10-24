"""roles/llm.py: every LLM call leaves a JSON record of exactly what the model was given and what it answered."""

import json
import time

from arena_kit.agents.buyer.roles import llm


def records(root):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(root.glob("*/*.json"))]


def test_call_json_when_model_answers_records_the_payload_and_the_reply(tmp_path, monkeypatch):
    monkeypatch.setenv("BUYER_LLM_LOG_DIR", str(tmp_path))
    user = json.dumps({"case": {"title": "T"}, "threads": [{"supplier_id": "sup:1"}]})
    assert llm.call_json(lambda s, u: '{"tactic": {"action": "send"}}', "SYSTEM", user, 5.0, name="strategist")
    [rec] = records(tmp_path)
    assert rec["role"] == "strategist" and rec["outcome"] == "ok"
    assert rec["user"] == json.loads(user) and rec["reply"] == '{"tactic": {"action": "send"}}'
    assert (tmp_path / rec["system_prompt"]).read_text(encoding="utf-8") == "SYSTEM"


def test_call_json_when_model_is_late_records_the_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("BUYER_LLM_LOG_DIR", str(tmp_path))

    def slow(system, user):
        time.sleep(0.5)
        return "{}"

    assert llm.call_json(slow, "SYSTEM", "{}", 0.05, name="writer") is None
    [rec] = records(tmp_path)
    assert rec["role"] == "writer" and rec["outcome"] == "timeout" and rec["reply"] is None


def test_call_json_stores_each_system_prompt_once(tmp_path, monkeypatch):
    monkeypatch.setenv("BUYER_LLM_LOG_DIR", str(tmp_path))
    for _ in range(3):
        llm.call_json(lambda s, u: "{}", "SAME SYSTEM", "{}", 5.0, name="writer")
    assert len(records(tmp_path)) == 3 and len(list((tmp_path / "prompts").glob("*.md"))) == 1


def test_call_json_when_logging_is_off_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("BUYER_LLM_LOG_DIR", "off")
    monkeypatch.chdir(tmp_path)
    llm.call_json(lambda s, u: "{}", "SYSTEM", "{}", 5.0, name="writer")
    assert not list(tmp_path.rglob("*.json"))
