"""PostToolUse hook on Bash: after `arena play` / `arena run`, log the run to starter-kit/runs/ledger.jsonl
and, for a finished foreground run on the hosted arena, harvest the sessions and hand Claude the score
table. Anything else passes through silently."""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from team import KIT, git, member  # noqa: E402

# `uv run arena play|run ...` or `arena play|run ...` at the start of a (sub)command, not mere mentions
RUN = re.compile(r"(?:^|[;&|(]\s*|\buv run\s+)arena\s+(play|run)\b")
HARVEST_TIMEOUT_S = 100
TABLE_LINES = 25


def arena_set(command: str) -> str:
    if "--set sandbox" in command or re.search(r"case:D\d", command):
        return "sandbox"
    return "final" if "--set final" in command else "practice"


def harvest(set_: str) -> str:
    cmd = ["uv", "run", "python", "scripts/harvest.py", "--set", set_, "--digest"]
    try:
        r = subprocess.run(cmd, cwd=KIT, capture_output=True, text=True, timeout=HARVEST_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        return f"harvest failed to run: {e}"
    out = (r.stdout or r.stderr).strip().splitlines()
    return "\n".join(out[-TABLE_LINES:])


def main() -> None:
    payload = json.load(sys.stdin)
    tool_input = payload.get("tool_input") or {}
    command = str(tool_input.get("command", ""))
    if not RUN.search(command):
        return
    who, set_ = member(), arena_set(command)
    local = "127.0.0.1" in command or "localhost" in command
    background = bool(tool_input.get("run_in_background"))
    entry = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "member": who,
        "branch": git("branch", "--show-current"),
        "commit": git("rev-parse", "--short", "HEAD"),
        "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        "set": set_,
        "local_sim": local,
        "background": background,
        "command": command[:300],
    }
    (KIT / "runs").mkdir(exist_ok=True)
    with (KIT / "runs" / "ledger.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    if local:
        context = "Local arena-sim run logged to runs/ledger.jsonl (sim sessions are not harvested)."
    elif set_ == "final":
        context = "Final run logged. Do not read /v1/events, /screen, /leaderboard or /results from agent code."
    elif background:
        context = (
            f"Arena run logged (running in background). When it finishes: `uv run python scripts/harvest.py "
            f"--set {set_} --digest` in starter-kit/, then add a row to Runs in docs/team/{who}.md."
        )
    else:
        context = (
            f"Arena run logged and harvested ({set_}):\n{harvest(set_)}\n\n"
            f"Add a row to Runs in docs/team/{who}.md (commit {entry['commit']}"
            f"{', uncommitted changes' if entry['dirty'] else ''}); use skill replay-analysis on weak cases."
        )
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": context}}))


if __name__ == "__main__":
    main()
