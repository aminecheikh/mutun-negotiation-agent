"""SessionStart hook: tell Claude who it works with, their current task list, and what the others ask.
Plain text on stdout becomes session context."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from team import MEMBERS, git, member, section  # noqa: E402


def main() -> None:
    me = member()
    branch = git("branch", "--show-current")
    lines = [f"Team Mutun session on branch `{branch}`."]
    if me == "unknown":
        lines.append(
            "Member unknown (branch is not feat/tools|feat/roles|feat/orchestrator and MUTUN_MEMBER is not set): "
            "ask the user whether they are amine, saed or jeddou before editing docs/team/."
        )
    else:
        lines.append(f"You work with **{me}**. Their tracking file: docs/team/{me}.md (follow docs/team/README.md).")
        lines.append(f"Now:\n{section(me, 'Now') or '-'}")
        lines.append(f"Tasks:\n{section(me, 'Tasks') or '-'}")
    for other in MEMBERS:
        if other != me and (asks := section(other, "Asks to others")):
            lines.append(f"Asks from {other}:\n{asks}")
    print("\n\n".join(lines))


if __name__ == "__main__":
    main()
