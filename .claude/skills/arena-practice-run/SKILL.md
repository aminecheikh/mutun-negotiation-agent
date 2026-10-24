---
name: arena-practice-run
description: Run the buyer agent against the arena (sandbox, one practice case, or a full practice sweep), harvest the sessions and compare scores with earlier versions. Use whenever asked to test, measure, run or benchmark the agent on the arena or the simulator.
---

# Running the agent on the arena

All commands from `starter-kit/`. Quota is scarce (~189 practice sessions until the final, none free up
before it), so pick the cheapest run that answers the question.

## 1. Choose the run

| Question | Run | Quota |
|---|---|---|
| Does the code work end to end? | `uv run arena play case:D01 --agent buyer -v` (also D02 for multi-supplier RFQ) | 0 |
| Plumbing without network or LLM cost | `uv run arena-sim serve --no-clock --data data/package` + a sim key (see `starter-kit/README.md`) | 0 |
| Did a fix help one case type? | `uv run arena play case:<id> --agent buyer -v` | 1 |
| Is a version better overall? | `uv run arena run --set practice --agent buyer --parallel 10 > runs/<version>.log 2>&1` | 10 |

Before any run that uses quota: run `uv run arena me`, tell the user how many sessions it will use and
how many are left, and confirm unless they already asked for that exact run. Never loop runs.

Long runs go in the background (`run_in_background`), not in the foreground.

## 2. Harvest

```bash
uv run python scripts/harvest.py --set practice --digest   # or --set sandbox
```

Prints one row per ended session (case, score, rounds, end reason, c_open / c_deal / c_best) and writes
`runs/digest-<set>.md`: every move, its terms, claim results and the supplier's reasons in words.
Sessions are cached in `runs/sessions/`.

## 3. Compare

Report a table per case: previous best, this run, delta. Name the version (git short hash). Then use the
`replay-analysis` skill on the cases that dropped or stayed low. Record a new lesson in `CLAUDE.md`
("Lessons from replays") only when two or more replays support it.
