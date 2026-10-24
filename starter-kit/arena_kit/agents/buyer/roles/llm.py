"""One LLM call with a hard timeout and JSON parsing, shared by all roles. A late or broken call returns
None and the role falls back to its rule-based move: a slow model must never cost the 60-second limit."""

import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from ...llm import parse

log = logging.getLogger("arena")
PROMPTS = Path(__file__).parent / "prompts"
# Shared across agents: 10 parallel cases x a few roles. A timed-out call keeps its worker until the
# provider answers, so keep this above the number of calls that can be in flight at once.
_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix="llm")


def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def call_json(complete, system: str, user: str, timeout_s: float, salvage=None) -> dict | None:
    """The model's JSON answer, or None (the role falls back). `salvage(text)` may rescue a reply that is not
    JSON (the writer: a "Subject: ... / body" e-mail); whatever it returns still passes the role's own checks."""
    if complete is None:
        return None
    future = _POOL.submit(complete, system, user)
    try:
        text = future.result(timeout=timeout_s)
    except FutureTimeout:
        future.cancel()  # do not charge for stale queued work after returning a rule-based answer
        log.warning("LLM call over %.0fs - using the fallback", timeout_s)
        return None
    except Exception as e:  # provider error
        log.warning("LLM call failed (%s) - using the fallback", e)
        return None
    try:
        return parse(text)
    except Exception as e:  # unparsable reply
        rescued = salvage(text) if salvage and isinstance(text, str) else None
        if rescued:
            return rescued
        log.warning("LLM call failed (%s) - using the fallback", e)
    return None
