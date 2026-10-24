"""One LLM call with a hard timeout and JSON parsing, shared by all roles. A late or broken call returns
None and the role falls back to its rule-based move: a slow model must never cost the 60-second limit.

`call_json_with_tools` is the same contract with tool calling: the model may call our deterministic tools
(roles/toolbox.py) for a bounded number of rounds before it answers, all inside one deadline. The transport is
`converse(system, messages, tools, final, timeout_s) -> (content_blocks, stop_reason)`; `converse_from_env` builds
it for the Anthropic API (no retries, per-call timeout = time left). Other providers keep the single call."""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from ...llm import parse

log = logging.getLogger("arena")
PROMPTS = Path(__file__).parent / "prompts"
# Shared across agents: 10 parallel cases x a few roles. A timed-out call keeps its worker until the
# provider answers, so keep this above the number of calls that can be in flight at once.
_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix="llm")
TOOL_ROUNDS = 1  # tool turns before the model must answer: each one is a full model call (~4-7 s)
MAX_CALLS_PER_TURN = 6  # tool calls executed per turn; more get an error result
FINAL_ANSWER_S = 4.0  # less time than this left after a tool turn: no point in another call, fall back


def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def call_json(complete, system: str, user: str, timeout_s: float) -> dict | None:
    if complete is None:
        return None
    future = _POOL.submit(complete, system, user)
    try:
        return parse(future.result(timeout=timeout_s))
    except FutureTimeout:
        log.warning("LLM call over %.0fs - using the fallback", timeout_s)
    except Exception as e:  # provider error or unparsable reply
        log.warning("LLM call failed (%s) - using the fallback", e)
    return None


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
        r = client.with_options(timeout=max(timeout_s, 1.0)).messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            messages=messages,
            tools=tools,
            tool_choice={"type": "none" if final else "auto"},
            **extra,
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
    converse, system: str, user: str, toolbox, timeout_s: float, rounds: int = TOOL_ROUNDS
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
            final = turn == rounds
            blocks, stop = converse(system, messages, toolbox.schemas, final, left)
            uses = [b for b in blocks if getattr(b, "type", None) == "tool_use"]
            if final or not uses or stop != "tool_use":
                return _text(blocks)
            messages.append({"role": "assistant", "content": blocks})
            messages.append({"role": "user", "content": _run_tools(toolbox, uses)})
        return ""

    future = _POOL.submit(loop)
    try:
        return parse(future.result(timeout=timeout_s))
    except FutureTimeout:
        log.warning("LLM tool loop over %.0fs - using the fallback", timeout_s)
    except Exception as e:  # provider error, late answer or unparsable reply
        log.warning("LLM tool loop failed (%s) - using the fallback", e)
    return None
