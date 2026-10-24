# Team tracking

One file per person, written by that person's Claude session (and by you). Nobody edits another
person's file: that keeps the three branches free of merge conflicts.

| File | Who | Branch | Owns |
|---|---|---|---|
| [amine.md](amine.md) | Amine | `feat/tools` | `starter-kit/arena_kit/agents/buyer/tools/` |
| [saed.md](saed.md) | Saed | `feat/roles-saeb` | `starter-kit/arena_kit/agents/buyer/roles/` |
| [jeddou.md](jeddou.md) | Jeddou | `feat/orchestrator` | `agents/buyer/agent.py`, `guard.py` + orchestrator modules (`runner`, `arbiter`, `policy`, `pipeline`, `observe`, `state`, `config`, `telemetry`), `cli.py`, `scripts/` |
| [DECISIONS.md](DECISIONS.md) | anyone, append only | any | decisions that bind more than one person |

The branch tells Claude who you are (`.claude/hooks/team.py`); override with `MUTUN_MEMBER=amine|saed|jeddou`.
At session start a hook shows Claude your **Now** and **Tasks** and the other two's **Asks to others**.

## Sections in each file

- **Now** - one or two lines: what is being worked on right now. Replace, don't append.
- **Tasks** - the task table from `docs/ROADMAP.md`; status `todo | doing | done | blocked`.
- **Asks to others** - what you need from someone else (`@who`, time, what). The others read it at
  session start. Delete the line once answered.
- **Log** - append only: `HH:MM - what changed - evidence` (commit, test, score, replay link).
- **Runs** - one row per arena run you started: time, commit, cases, sessions used, mean score, notes.

## Practice runs

All three share one arena key and one quota. A hook logs every `arena play` / `arena run` to
`starter-kit/runs/ledger.jsonl` (local) and harvests the sessions right after a foreground run.
`scripts/harvest.py` sees the whole team's sessions, not only yours.

| Who | Hosted practice budget |
|---|---|
| Amine | 30 |
| Saed | 30 |
| Jeddou (sweeps, rehearsal) | 70 |
| Reserve (team decision) | ~59 |
