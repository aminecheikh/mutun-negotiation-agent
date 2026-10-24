"""The v2 decision trail: one JSON line per move, per case and per model call - for debugging, timing and the jury's
"why did your agent write that?".

    runs/v2-rounds.jsonl   every move: path (fast / agents / rules), the agents' status and timing, the negotiator's
                           reasoning, the guard's corrections, sheets with C, the e-mail (truncated)
    runs/v2-cases.jsonl    every finished case: the arena's result
    runs/v2-llm.jsonl      every model call: role, seconds, tools called, tokens, error, the answer (truncated)

Directory: V2_LOG_DIR, else BUYER_LOG_DIR, else `runs` (gitignored); "off" disables. Never logs keys.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("arena")
TEXT_CHARS = 1500


class Trail:
    def __init__(self, directory: str | os.PathLike | None = None):
        where = (
            directory
            if directory not in (None, "")
            else os.environ.get("V2_LOG_DIR") or os.environ.get("BUYER_LOG_DIR", "runs")
        )
        self.dir = None if str(where).lower() == "off" else Path(where)
        self._lock = threading.Lock()

    def _write(self, name: str, record: dict) -> None:
        if self.dir is None:
            return
        line = json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **record}, ensure_ascii=False, default=str)
        try:
            with self._lock:
                self.dir.mkdir(parents=True, exist_ok=True)
                with (self.dir / name).open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except OSError as e:  # logging must never cost a move
            log.warning("v2 trail write failed: %s", e)

    def round(self, **fields) -> None:
        if isinstance(fields.get("body"), str):
            fields["body"] = fields["body"][:TEXT_CHARS]
        self._write("v2-rounds.jsonl", fields)

    def case(self, **fields) -> None:
        self._write("v2-cases.jsonl", fields)

    def llm_call(self, call, result) -> None:
        self._write(
            "v2-llm.jsonl",
            {
                "role": call.role,
                "seconds": result.seconds,
                "ok": result.ok,
                "error": result.error,
                "tools": result.tool_calls,
                "stop": result.stop_reason,
                "usage": result.usage,
                "prompt_chars": len(call.user),
                "answer": json.dumps(result.data, ensure_ascii=False)[:TEXT_CHARS] if result.ok else None,
            },
        )
