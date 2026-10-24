"""The model layer: one agent call = a bounded tool loop that ends in a JSON answer, all inside one deadline.

    LLM.run(call)          one agent: tool turns (read-only tools, see toolbox.py), then a schema-checked answer
    LLM.run_many(calls)    several agents in parallel; each returns by its own deadline or is dropped

Never raises: a refusal, a timeout, a provider error or an unparsable answer gives `CallResult(data=None)` and the
caller decides without that agent. The transport is pluggable (`Transport`); `anthropic_transport` speaks the
Anthropic Messages API with prompt caching on the system prompt, structured outputs (`output_config.format`) for
the answer and no SDK retries - a retried request would outlive the deadline it was started for.
"""

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field

from .config import V2Config

log = logging.getLogger("arena")
MIN_CALL_S = 2.0  # never start a model call with less time than this before the deadline
GRACE_S = 0.3  # an answer this late past its deadline still counts (HTTP timeouts end slightly after)
# separate executors: a burst of specialist calls (a wave's first round) can never queue a negotiator behind it
_POOLS = {
    "analyst": ThreadPoolExecutor(max_workers=96, thread_name_prefix="v2-analyst"),
    "negotiator": ThreadPoolExecutor(max_workers=32, thread_name_prefix="v2-negotiator"),
}

# transport(model=, system=, messages=, tools=, tool_choice=, schema=, effort=, max_tokens=, timeout_s=)
#   -> (content_blocks, stop_reason, usage_dict)
Transport = Callable[..., tuple[list, str | None, dict]]


@dataclass
class AgentCall:
    role: str  # analyst name or "negotiator": picks the model, the effort and the slot pool
    system: str
    user: str
    deadline: float  # time.monotonic() by which the answer must be back
    schema: dict | None = None  # JSON schema of the final answer (structured outputs)
    tools: object | None = None  # a toolbox.Toolset: .schemas (list of tool definitions) and .run(name, args)
    tool_turns: int = 0
    context: dict = field(default_factory=dict)  # case, supplier, round: for the in/out log only


@dataclass
class CallResult:
    role: str
    data: dict | None = None
    error: str | None = None
    tool_calls: list[str] = field(default_factory=list)
    seconds: float = 0.0
    stop_reason: str | None = None
    usage: dict = field(default_factory=dict)
    turns: list[dict] = field(default_factory=list)  # per model call: its text, tool calls with inputs and results
    reply: str | None = None  # the final answer's raw text

    @property
    def ok(self) -> bool:
        return isinstance(self.data, dict)


class LateError(TimeoutError):
    pass


def parse_json(text: str) -> dict:
    """The answer as a dict: plain JSON (structured outputs), else the first {...} block in the text."""
    text = (text or "").strip()
    try:
        out = json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ValueError(f"no JSON object in the answer: {text[:160]!r}") from None
        out = json.loads(match.group(0))
    if not isinstance(out, dict):
        raise ValueError("the answer is not a JSON object")
    return out


def _block_type(block) -> str | None:
    return block.get("type") if isinstance(block, dict) else getattr(block, "type", None)


def _attr(block, name):
    return block.get(name) if isinstance(block, dict) else getattr(block, name, None)


class LLM:
    def __init__(self, transport: Transport | None, config: V2Config | None = None, recorder=None):
        self.transport, self.config, self.recorder = transport, config or V2Config(), recorder
        self._slots = {
            "negotiator": threading.BoundedSemaphore(self.config.negotiator_slots),
            "analyst": threading.BoundedSemaphore(self.config.analyst_slots),
        }

    @property
    def available(self) -> bool:
        return self.transport is not None and bool(self.config.model_for("negotiator"))

    # ---- one agent ---------------------------------------------------------------------------------

    def run(self, call: AgentCall) -> CallResult:
        t0 = time.monotonic()
        result = CallResult(call.role)
        kind = "negotiator" if call.role == "negotiator" else "analyst"
        slot = self._slots[kind]
        if self.transport is None:
            result.error = "no model configured"
            return result
        if not slot.acquire(timeout=max(0.0, call.deadline - time.monotonic() - MIN_CALL_S)):
            result.error = "no free model slot before the deadline"
            return result
        try:
            result.data = self._loop(call, result, kind)
        except LateError as e:
            result.error = f"late: {e}"
        except Exception as e:  # provider error, refusal, bad JSON: the caller decides without this agent
            result.error = f"{type(e).__name__}: {e}"[:300]
        finally:
            slot.release()
            result.seconds = round(time.monotonic() - t0, 2)
            if result.error:
                log.warning("v2 %s: %s (%.1fs)", call.role, result.error, result.seconds)
            if self.recorder is not None:
                self.recorder(call, result)
        return result

    def _loop(self, call: AgentCall, result: CallResult, kind: str) -> dict:
        cfg = self.config
        model = cfg.model_for(kind)
        effort = cfg.negotiator_effort if kind == "negotiator" else cfg.analyst_effort
        max_tokens = cfg.negotiator_max_tokens if kind == "negotiator" else cfg.analyst_max_tokens
        schemas = list(getattr(call.tools, "schemas", None) or []) if call.tools is not None else []
        messages: list[dict] = [{"role": "user", "content": call.user}]
        for turn in range(call.tool_turns + 1):
            left = call.deadline - time.monotonic()
            if left < MIN_CALL_S:
                raise LateError(f"{left:.1f}s left before turn {turn + 1}")
            final = turn >= call.tool_turns or not schemas
            blocks, stop, usage = self.transport(
                model=model,
                system=call.system,
                messages=messages,
                tools=schemas or None,
                tool_choice=({"type": "none"} if final else {"type": "auto"}) if schemas else None,
                schema=call.schema,
                effort=effort or None,
                max_tokens=max_tokens,
                timeout_s=left,
            )
            result.stop_reason = stop
            for key, value in (usage or {}).items():
                if isinstance(value, int | float):
                    result.usage[key] = result.usage.get(key, 0) + value
            if stop == "refusal":
                raise RuntimeError("the model refused")
            uses = [b for b in blocks if _block_type(b) == "tool_use"]
            turn_text = "".join(_attr(b, "text") or "" for b in blocks if _block_type(b) == "text")
            result.turns.append({"turn": turn + 1, "stop": stop, "text": turn_text, "tool_calls": []})
            if uses and not final and stop == "tool_use":
                messages.append({"role": "assistant", "content": blocks})
                messages.append({"role": "user", "content": self._run_tools(call, uses, result)})
                continue
            text = turn_text
            result.reply = text
            if stop == "max_tokens" and not text.strip():
                raise RuntimeError("answer cut off at max_tokens")
            return parse_json(text)
        raise RuntimeError("no answer after the tool turns")

    def _run_tools(self, call: AgentCall, uses: list, result: CallResult) -> list[dict]:
        out = []
        limit = self.config.max_tool_calls_per_turn
        for n, use in enumerate(uses):
            name, args, use_id = _attr(use, "name"), _attr(use, "input") or {}, _attr(use, "id")
            result.tool_calls.append(str(name))
            if n >= limit:
                content, error = f"skipped: at most {limit} tool calls per turn", True
            else:
                try:
                    payload = call.tools.run(str(name), args if isinstance(args, dict) else {})
                    content = (
                        payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
                    )
                    content, error = content[: self.config.tool_result_chars], False
                except Exception as e:  # a wrong argument is information for the model, never a crash
                    content, error = f"error: {e}"[:500], True
            out.append({"type": "tool_result", "tool_use_id": use_id, "content": content, "is_error": error})
            if result.turns:  # the in/out log keeps what each tool was asked and what it answered
                result.turns[-1]["tool_calls"].append(
                    {"name": str(name), "input": args, "result": content[:4000], "is_error": error}
                )
        return out

    # ---- several agents at once ----------------------------------------------------------------------

    def run_many(self, calls: list[AgentCall]) -> dict[str, CallResult]:
        """Every call in parallel; a call that is not back by its deadline is reported late and abandoned (its
        HTTP request carries the same deadline as its timeout, so it ends on its own shortly after)."""
        futures = {
            call.role: (_POOLS["negotiator" if call.role == "negotiator" else "analyst"].submit(self.run, call), call)
            for call in calls
        }
        out = {}
        for role, (future, call) in futures.items():
            try:
                out[role] = future.result(timeout=max(0.0, call.deadline - time.monotonic()) + GRACE_S)
            except FutureTimeout:
                out[role] = CallResult(role, error="late: not back by its deadline")
        return out


# ---- the Anthropic transport ---------------------------------------------------------------------------


def anthropic_transport(max_timeout_s: float = 60.0) -> Transport | None:
    """The Messages API transport, or None when the model is not configured (LLM_PROVIDER=anthropic, LLM_MODEL
    and ANTHROPIC_API_KEY). One client for all cases: it is thread-safe and keeps its connection pool."""
    if os.environ.get("LLM_PROVIDER", "anthropic") != "anthropic" or not os.environ.get("LLM_MODEL"):
        return None
    import anthropic

    client = anthropic.Anthropic(timeout=max_timeout_s, max_retries=0)

    def call(*, model, system, messages, tools, tool_choice, schema, effort, max_tokens, timeout_s):
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            # the system prompt (house rules, playbook, role) is identical for every call of a role: cache it
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
        }
        if tools:
            kwargs["tools"], kwargs["tool_choice"] = tools, tool_choice
        output_config = {}
        if effort:
            output_config["effort"] = effort
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        if output_config:
            kwargs["output_config"] = output_config
        r = client.with_options(timeout=max(timeout_s, 1.0)).messages.create(**kwargs)
        usage = {}
        if r.usage is not None:
            for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                value = getattr(r.usage, key, None)
                if isinstance(value, int):
                    usage[key] = value
        return list(r.content), r.stop_reason, usage

    return call
