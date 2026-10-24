---
name: final-runbook
description: Sunday checklist for the Sunday data release, pre-flight, code freeze, submission and starting the Live Final runner. Use on Sunday 2026-10-25 or whenever asked about the final, the freeze, submission or rehearsal.
---

# Final runbook (Sun 2026-10-25, CET - clocks went back at 03:00)

## 08:00 Sunday release
1. `cd starter-kit && uv run arena data` - adds `data/release-sunday`.
2. Rebuild cached dossiers/claims if tools cache them; check a new `news:` / `doc:` ID from the release
   resolves via `DataPackage.lookup`.

## Rehearsal (no quota)
```bash
uv run arena-sim serve --final-rehearsal --data data/package
ARENA_URL=http://127.0.0.1:8000 uv run arena register "Rehearsal" sim
ARENA_URL=http://127.0.0.1:8000 ARENA_API_KEY=<sim key> uv run arena run --set final --waves 2 --agent buyer --parallel 10
```
Check: every case ends before its cap, no move over 45 s, no crash, logs readable.

## Pre-flight (by 11:30, on the final machine and network)
- [ ] `uv run arena me` answers with team Mutun.
- [ ] One hosted practice case with `--agent buyer` (1 session) ends with a deal or a deliberate walk.
- [ ] LLM key has credit and rate limit for ~10 parallel cases for an hour.
- [ ] Laptop on power; `caffeinate -dims` running; stable network (phone hotspot as backup).
- [ ] `uv run pytest` and `uv run ruff check .` green.

## 13:00 code freeze
- [ ] Merge to the final branch, push, note the commit hash: `git rev-parse HEAD`.
- [ ] README: setup, env var names (no values), the run command, approach summary.
- [ ] Submit repo URL, commit hash, short description on the organisers' platform.
- [ ] No `.env` or key in git: `git log -p | grep -E "sk-ant|ARENA_API_KEY=."` returns nothing.

## 13:15 start the runner (then hands off - no human in the loop)
```bash
cd starter-kit && uv run arena run --set final --agent buyer --parallel 10 2>&1 | tee runs/final.log
```
It waits through the 403 until wave 1, plays each wave in parallel and closes 90 s before each cap.
If it crashes: start the same command again; it resumes running sessions. The agent must never read
`/v1/events`, `/screen`, `/leaderboard`, `/results`.

## After the final
`scripts/harvest.py` only knows `practice` and `sandbox`; final replays appear on
https://arena.tacto.ai/results once the final has closed. Pick one replay for the pitch (15:10, 5 min + 2 min
questions).
