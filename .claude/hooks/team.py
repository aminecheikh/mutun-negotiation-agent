"""Shared by the team hooks: who is working (from the git branch) and where their files are."""

import os
import subprocess
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
KIT = ROOT / "starter-kit"
TEAM = ROOT / "docs" / "team"
BRANCH_PREFIX = {"feat/tools": "amine", "feat/roles": "saed", "feat/orchestrator": "jeddou"}
MEMBERS = ("amine", "saed", "jeddou")


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def member() -> str:
    """MUTUN_MEMBER wins; else the branch prefix; else 'unknown'."""
    forced = os.environ.get("MUTUN_MEMBER", "").lower()
    if forced in MEMBERS:
        return forced
    branch = git("branch", "--show-current")
    return next((name for prefix, name in BRANCH_PREFIX.items() if branch.startswith(prefix)), "unknown")


def section(name: str, heading: str) -> str:
    """The text under `## heading` in docs/team/<name>.md, without blank lines."""
    path = TEAM / f"{name}.md"
    if not path.is_file():
        return ""
    out, inside = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            inside = line[3:].strip().lower() == heading.lower()
            continue
        if inside and line.strip():
            out.append(line)
    return "\n".join(out)
