"""One LLM call with a hard timeout and JSON parsing, shared by all roles. A late or broken call returns
None and the role falls back to its rule-based move: a slow model must never cost the 60-second limit.

`call_json_with_tools` is the same contract with tool calling: the model may call our deterministic tools
(roles/toolbox.py) for a bounded number of rounds before it answers, all inside one deadline. The transport is
`converse(system, messages, tools, final, timeout_s) -> (content_blocks, stop_reason)`; `converse_from_env` builds
it for the Anthropic API (no retries, per-call timeout = time left). Other providers keep the single call.

Every call leaves a JSON record of what the model was given and what it answered (`BUYER_LLM_LOG_DIR`, default
runs/llm, "off" to disable): runs/llm/<date>/<time>-<role>-<id>.json with the user payload, the raw reply, the
tools it called, seconds and outcome; each distinct system prompt once under runs/llm/prompts/. Logging never
breaks a call."""

import hashlib
import json
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime
from pathlib import Path

from ...llm import parse as greedy_parse
from ...llm import retry_connect

log = logging.getLogger("arena")


def parse(text: str) -> dict:
    """The first complete JSON object in the reply. The model sometimes adds prose after it, and the kit's greedy
    match then ran to the last brace of that prose ("Extra data": 3 strategist answers lost in one sweep)."""
    decoder = json.JSONDecoder()
    start = text.find("{") if isinstance(text, str) else -1
    while start >= 0:
        try:
            out, _ = decoder.raw_decode(text, start)
            if isinstance(out, dict):
                return out
        except ValueError:
            pass
        start = text.find("{", start + 1)
    return greedy_parse(text)


PROMPTS = Path(__file__).parent / "prompts"
# Shared across agents: 10 parallel cases x a few roles. A timed-out call keeps its worker until the
# provider answers, so keep this above the number of calls that can be in flight at once.
_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix="llm")
TOOL_ROUNDS = 1  # tool turns before the model must answer: each one is a full model call (~4-7 s)
MAX_CALLS_PER_TURN = 6  # tool calls executed per turn; more get an error result
# Saed's bench: a strategist answer takes up to ~6 s (p90 6.1 s). A tool turn is a full model call too, so it is
# only started when an answer still fits after it; with less time left the model answers at once, without tools
FINAL_ANSWER_S = 7.0
ANSWER_NOW = "Answer now with the one JSON object described in the instructions - no tool calls, no other text."


def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def record(role: str, system: str, user: str, reply: str | None, seconds: float, outcome: str, tools=()) -> None:
    """Write one call's record under BUYER_LLM_LOG_DIR (see the module docstring). Never raises."""
    root = os.environ.get("BUYER_LLM_LOG_DIR", "runs/llm")
    if root.lower() == "off":
        return
    try:
        base, now = Path(root), datetime.now()
        digest = hashlib.sha256(system.encode("utf-8")).hexdigest()[:12]
        prompt_file = base / "prompts" / f"{role}-{digest}.md"
        if not prompt_file.exists():
            prompt_file.parent.mkdir(parents=True, exist_ok=True)
            prompt_file.write_text(system, encoding="utf-8")
        try:
            payload = json.loads(user)
        except (TypeError, ValueError):
            payload = user
        entry = {
            "ts": now.isoformat(timespec="milliseconds"), "role": role, "outcome": outcome,
            "seconds": round(seconds, 2), "system_prompt": str(prompt_file.relative_to(base)),
            "user": payload, "reply": reply, "tools_called": list(tools),
        }  # fmt: skip
        day = base / now.strftime("%Y-%m-%d")
        day.mkdir(parents=True, exist_ok=True)
        name = f"{now.strftime('%H%M%S.%f')}-{role}-{uuid.uuid4().hex[:6]}.json"
        (day / name).write_text(json.dumps(entry, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    except Exception as e:  # a full disk or a read-only folder must never cost a move
        log.warning("LLM record failed: %s", e)


def call_json(complete, system: str, user: str, timeout_s: float, name: str = "llm", salvage=None) -> dict | None:
    """The model's JSON answer, or None (the role falls back). `salvage(text)` may rescue a reply that is not
    JSON (the writer: a "Subject: ... / body" e-mail); whatever it returns still passes the role's own checks."""
    if complete is None:
        return None
    t0, text, outcome, out = time.monotonic(), None, "ok", None
    future = _POOL.submit(complete, system, user)
    try:
        text = future.result(timeout=timeout_s)
        try:
            out = parse(text)
        except Exception:
            out = salvage(text) if salvage and isinstance(text, str) else None
            if out is None:
                raise
            outcome = "salvaged"
    except FutureTimeout:
        future.cancel()  # do not charge for stale queued work after returning a rule-based answer
        outcome = "timeout"
        log.warning("LLM call over %.0fs - using the fallback", timeout_s)
    except Exception as e:  # provider error or unparsable reply
        outcome = f"error: {e}"[:200]
        log.warning("LLM call failed (%s) - using the fallback", e)
    finally:
        record(name, system, user, text, time.monotonic() - t0, outcome)
    return out


def tools_enabled() -> bool:
    """BUYER_LLM_TOOLS=0 switches tool calling off (one plain call per role, as before)."""
    return os.environ.get("BUYER_LLM_TOOLS", "1") != "0"


def converse_from_env(timeout_s: float):
    """The tool-calling transport for LLM_PROVIDER=anthropic, or None (other providers, or tools switched off)."""
    if os.environ.get("LLM_PROVIDER") != "anthropic" or not os.environ.get("LLM_MODEL") or not tools_enabled():
        return None
    import anthropic

    model = os.environ["LLM_MODEL"]
    client = anthropic.Anthropic(timeout=timeout_s, max_retries=0)  # a retried call outlives our deadline
    extra = {"output_config": {"effort": e}} if (e := os.environ.get("LLM_EFFORT")) else {}

    def converse(system: str, messages: list, tools: list[dict], final: bool, timeout_s: float):
        # final: tools stay declared (the history holds tool_use blocks) but the model must answer now
        r = retry_connect(
            lambda: client.with_options(timeout=max(timeout_s, 1.0)).messages.create(
                model=model,
                max_tokens=16000,
                system=system,
                messages=messages,
                tools=tools,
                tool_choice={"type": "none" if final else "auto"},
                **extra,
            )
        )
        return r.content, r.stop_reason

    return converse


def _text(blocks) -> str:
    return "".join(getattr(b, "text", "") or "" for b in blocks if getattr(b, "type", None) == "text")


def _run_tools(toolbox, uses) -> list[dict]:
    results = []
    for n, use in enumerate(uses):
        if n >= MAX_CALLS_PER_TURN:
            content, error = f"skipped: at most {MAX_CALLS_PER_TURN} tool calls per turn", True
        else:
            try:
                result = toolbox.run(use.name, use.input or {})
                content, error = json.dumps(result, ensure_ascii=False, default=str), False
            except Exception as e:  # a bad argument is the model's mistake to read, never a crash of the round
                content, error = f"error: {e}", True
        results.append({"type": "tool_result", "tool_use_id": use.id, "content": content, "is_error": error})
    return results


def call_json_with_tools(
    converse, system: str, user: str, toolbox, timeout_s: float, rounds: int = TOOL_ROUNDS, name: str = "llm"
) -> dict | None:
    """Like `call_json`, but the model may call `toolbox` tools for up to `rounds` turns first. One deadline covers
    every model call and every tool run; a late answer returns None and the role uses its fallback."""
    if converse is None:
        return None
    deadline = time.monotonic() + timeout_s

    def loop() -> str:
        messages = [{"role": "user", "content": user}]
        for turn in range(rounds + 1):
            left = deadline - time.monotonic()
            if left <= (FINAL_ANSWER_S if turn else 0.0):
                raise TimeoutError(f"{left:.1f}s left for the answer")
            final = turn == rounds or left < 2 * FINAL_ANSWER_S  # no room for a tool turn and an answer: answer now
            if final and turn:  # the answer turn after tool results: ask for the JSON explicitly (3 prose replies)
                messages[-1] = {
                    **messages[-1],
                    "content": [*messages[-1]["content"], {"type": "text", "text": ANSWER_NOW}],
                }
            blocks, stop = converse(system, messages, toolbox.schemas, final, left)
            uses = [b for b in blocks if getattr(b, "type", None) == "tool_use"]
            if final or not uses or stop != "tool_use":
                return _text(blocks)
            messages.append({"role": "assistant", "content": blocks})
            messages.append({"role": "user", "content": _run_tools(toolbox, uses)})
        return ""

    t0, text, outcome = time.monotonic(), None, "ok"
    future = _POOL.submit(loop)
    try:
        text = future.result(timeout=timeout_s)
        return parse(text)
    except FutureTimeout:
        outcome = "timeout"
        log.warning("LLM tool loop over %.0fs - using the fallback", timeout_s)
    except Exception as e:  # provider error, late answer or unparsable reply
        outcome = f"error: {e}"[:200]
        log.warning("LLM tool loop failed (%s) - using the fallback", e)
    finally:
        record(name, system, user, text, time.monotonic() - t0, outcome, getattr(toolbox, "calls", ()))
    return None
