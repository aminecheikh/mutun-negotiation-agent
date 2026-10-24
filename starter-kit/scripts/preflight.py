"""J6: pre-flight for the Live Final (owner: Jeddou). Run on the final machine, from starter-kit/.

    uv run python scripts/preflight.py [--sunday]

Checks, without printing any secret: the environment variables exist, the arena answers with our key, the data
package loads, the Sunday release is present (required with --sunday), no practice session is left open (open
sessions eat the 10-slot limit of the final), and agent code does not reference the live feed. Exit code 1 if a
required check fails.
"""

import argparse
import os
import re
import sys
from pathlib import Path

from arena_kit import ArenaClient
from arena_kit.cli import load_env

REQUIRED_ENV = ("ARENA_URL", "ARENA_API_KEY", "ARENA_DATA")
LLM_ENV = ("LLM_MODEL", "ANTHROPIC_API_KEY")
FEED = re.compile("|".join(["leader" + "board", "/" + "events", "/" + "screen", "/" + "results"]))


def check(name: str, ok: bool, detail: str = "", required: bool = True) -> bool:
    mark = "OK  " if ok else ("FAIL" if required else "WARN")
    print(f"[{mark}] {name}{': ' + detail if detail else ''}")
    return ok or not required


def main() -> None:
    load_env()
    p = argparse.ArgumentParser()
    p.add_argument("--sunday", action="store_true", help="the Sunday release must be present")
    args = p.parse_args()
    good = True
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    good &= check("environment", not missing, f"missing {missing}" if missing else "names present")
    llm_missing = [k for k in LLM_ENV if not os.environ.get(k)]
    check("LLM configured", not llm_missing, f"missing {llm_missing}: roles run on rule twins" if llm_missing
          else os.environ.get("LLM_MODEL", ""), required=False)  # fmt: skip
    try:
        client = ArenaClient()
        me = client.me()
        good &= check("arena key", True, f"team {me.get('team_name') or me.get('team') or '?'}")
        open_practice = [s["session_id"] for s in client.sessions("practice") if s.get("status") == "open"]
        good &= check("no open practice sessions", not open_practice, ", ".join(open_practice))
    except Exception as e:  # noqa: BLE001 - report every failure the same way
        good &= check("arena key", False, str(e)[:200])
    data = Path(os.environ.get("ARENA_DATA", "data/package"))
    try:
        from arena_kit.data import DataPackage

        package = DataPackage(data)
        good &= check("data package", bool(package.facts), f"{len(package.facts)} facts at {data}")
        good &= check("Sunday release", package.release is not None, str(package.release or "not found"),
                      required=args.sunday)  # fmt: skip
    except Exception as e:  # noqa: BLE001
        good &= check("data package", False, str(e)[:200])
    roots = [Path("arena_kit/agents/buyer"), Path("arena_kit/agents/buyer_v2"), Path("scripts")]
    hits = [str(f) for root in roots for f in root.rglob("*.py") if FEED.search(f.read_text(encoding="utf-8"))]
    good &= check("agent never reads the live feed", not hits, ", ".join(hits))
    print("\npre-flight passed" if good else "\npre-flight FAILED")
    sys.exit(0 if good else 1)


if __name__ == "__main__":
    main()
