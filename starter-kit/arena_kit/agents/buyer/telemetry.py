"""Decision trail (owner: Jeddou): one JSON line per decision and per case, for debugging, timing reports and
the jury's "why did your agent write that?".

    runs/buyer-rounds.jsonl   every message, accept and walk: verdict, stages used, timings, sheets with C
    runs/buyer-cases.jsonl    every finished case: the arena's result (score, c_open, c_deal, c_best, ...)

The directory comes from BUYER_LOG_DIR (default `runs`, gitignored in starter-kit/); BUYER_LOG_DIR=off disables.
Never logs keys or the .env; e-mail bodies are truncated.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("arena")
BODY_CHARS = 1200


class Telemetry:
    def __init__(self, directory: str | os.PathLike | None = None):
        where = directory if directory is not None else os.environ.get("BUYER_LOG_DIR", "runs")
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
            log.warning("telemetry write failed: %s", e)

    def round(self, **fields) -> None:
        if isinstance(fields.get("body"), str):
            fields["body"] = fields["body"][:BODY_CHARS]
        self._write("buyer-rounds.jsonl", fields)

    def case(self, **fields) -> None:
        self._write("buyer-cases.jsonl", fields)
