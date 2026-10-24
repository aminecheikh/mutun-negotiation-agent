# Jeddou - orchestrator and ops

Branch `feat/orchestrator` · owns `agents/buyer/agent.py`, `guard.py` and the orchestrator modules `runner.py`, `arbiter.py`, `policy.py`, `pipeline.py`, `observe.py`, `state.py`, `config.py`, `telemetry.py`; `arena_kit/cli.py`, `scripts/`, the final runner · skills: `arena-practice-run`, `replay-analysis`, `final-runbook`

How it works: the module docstrings (start with `runner.py`, then `policy.py`). Open questions: [../issues.md](../issues.md).

## Now

- J1-J4 implemented and tested (PR from `feat/orchestrator`). Next: J0 probe on D02 and the first sandbox runs with the live model (needs `.env` on the machine that runs it), then sweep v1 after roles merge.

## Tasks

| ID | Task | Status | Due |
|---|---|---|---|
| J0 | `scripts/probe_parallel.py` on D02: does the arena serialise calls per session? -> `BUYER_SERIALISED_SESSION` | script done, run pending (needs the arena key) | Sat 22:00 |
| J1 | Per-thread runner: one worker per supplier thread, case arbiter under a lock, `cli.py --agent buyer` routing, resume after a crash | done (arena-sim tests: RFQ bidders answered in time, resume) | Sat 18:45 |
| J2 | Accept / walk policy: acceptability invariant (must-haves, below the best opening, not above the alternative), never close above a cheaper sheet, rival may still beat (fit, `p_room`), one counter after a final offer, case-level walk | done (unit tests incl. the D02 16.10-vs-16.00 regression) | Sat 22:00 |
| J3 | RFQ: every live thread answered within 60 s; rival offers via tools' `rival_offer` claims, IDs reserved atomically across threads; keep-alive for held threads | done | Sat 22:00 |
| J4 | Timing: every role stage time-boxed with its rule twin; `scripts/timing_report.py`; stand-off and given-up-thread handling | done; live-model timing pending (issues.md 3) | Sun 00:30 |
| J5 | Sweeps v1/v2/v3 (10 sessions each), harvest, per-case deltas | todo (after roles merge) | ongoing |
| J6 | `scripts/preflight.py` done; rehearsal on arena-sim, README run section, freeze, runner | preflight done, rest todo | Sun 10:30-13:15 |

## Asks to others

- @saed - roles merge: `buyer_factory` picks up `LLMStrategist` and `LLMProfiler` automatically once they are on `main` (verified on a local merge of `feat/roles-saeb`: 411 tests, 27/27 claims verified on arena-sim with the real data). Please read issues.md 9-11 (language, one-thread view, accept/walk permissions).
- @amine - done as asked: accept checks price C with `tools.supplier_from_thread` (re-run on every reply, so a cost breakdown counts), `tools.refresh` every round (bands/trades synced back), `p_room` in the rival logic, one shared `ThreadSafePackage`. issues.md 12 (lock in the kit's `data.py`) is a team call.

## Log

- 16:30 - scaffold in place (`BuyerAgent`, `guard.py`, `--agent buyer` in cli, naive fallback on any error)
- 17:20-21:50 - orchestrator implemented on `feat/orchestrator`: runner (per-thread workers), arbiter, policy, guard, pipeline, observe/state/config/telemetry; cli routing; scripts probe_parallel / timing_report / preflight; harvest UTF-8 - 027e607
- contrast on arena-sim, same RFQ and a 1 s strategist: kit `play()` left the second bidder with 0 messages and a timeout; the runner answered both every round, 0 timeouts
- merged main (tools v1 + A8): adopted `thread_safe` and `supplier_from_thread`, kept `CaseContext.trades` - f3c24c7
- integration run with the real data package + Saed's roles (local merge): found two RFQ bidders sent the same index pair in the same second (`duplicate`); fixed by reserving IDs atomically before each POST - 0046f39; 3 reruns: 80 claims, all verified
- LLMProfiler wired, dossiers refined in parallel - ee09a3e; `p_room` in the rival logic - d5ed30a
- full suite: 367 passed on the branch, 411 passed with roles merged locally; ruff clean

## Runs

| When | Commit | Cases | Sessions | Mean | Notes |
|---|---|---|---|---|---|
| Sat 15:20 | 7f92ca7 | 10 hosted | 10 | 54.4 | kit `--agent llm` baseline |
| Sat 21:40 | 0046f39 + roles (local) | arena-sim S01-S03, real data | 0 (local) | 73.1 | scripted sim supplier: not comparable with hosted scores; 27 claims, all verified |
