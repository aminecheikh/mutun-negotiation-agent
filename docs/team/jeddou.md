# Jeddou - orchestrator and ops

Branch `feat/orchestrator` · owns `agents/buyer/agent.py`, `agents/buyer/guard.py`, `arena_kit/cli.py`, `scripts/`, the final runner · skills: `arena-practice-run`, `replay-analysis`, `final-runbook`

## Now

- Not started: J1 next.

## Tasks

| ID | Task | Status | Due |
|---|---|---|---|
| J1 | Review the scaffold: play D01 + D02 with `--agent buyer` (free), read the per-round timing log, fix what breaks | todo | Sat 17:30 |
| J2 | Accept / walk policy: no accept before round 3 unless C <= c_target, use `BehaviourRead` (p_more, walk_risk), walk when nothing beats the alternative | todo | Sat 22:00 |
| J3 | RFQ: **every live thread must get a message within 60 s** (D02: unanswered bidder timed out 3x and walked) - likely threads in parallel in the play loop; then focus, rival quotes, close the best | todo | **Sat 22:00** |
| J4 | Timing: run analyst calls of several threads in parallel, round < 30 s at 10 parallel cases, test on `arena-sim` | todo | Sat 22:00 |
| J5 | Sweeps: v1 at 22:00, v2 ~01:00, v3 Sun morning; harvest, compare per case, write the Runs table | todo | ongoing |
| J6 | Final: `arena-sim --final-rehearsal`, README (run command, env var names), pre-flight, freeze, runner | todo | Sun 10:30-13:15 |

## Asks to others

## Log

- 16:30 - scaffold in place (`BuyerAgent`, `guard.py`, `--agent buyer` in cli, naive fallback on any error) - commit on `feat/negotiation-agent`

## Runs

| When | Commit | Cases | Sessions | Mean | Notes |
|---|---|---|---|---|---|
| Sat 15:20 | 7f92ca7 | 10 hosted | 10 | 54.4 | kit `--agent llm` baseline |
