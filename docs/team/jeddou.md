# Jeddou - orchestrator and ops

Branch `feat/orchestrator` · owns `agents/buyer/agent.py`, `guard.py` and the orchestrator modules `runner.py`, `arbiter.py`, `policy.py`, `pipeline.py`, `observe.py`, `state.py`, `config.py`, `telemetry.py`; `arena_kit/cli.py`, `scripts/`, the final runner · skills: `arena-practice-run`, `replay-analysis`, `final-runbook`

How it works: the module docstrings (start with `runner.py`, then `policy.py`). Open questions: [../issues.md](../issues.md).

## Now

- PR #2 (`fix/orchestrator-review`) includes the earlier recovery/guard fixes and the negotiation review.
  Fetched all branches and merged Saed's `5e44a84`. Price estimates now respect units and recent evidence;
  concession inference separates package changes; roles and arbiter share relationship-cost inputs.
  The strategist prompt uses live evidence rather than practice percentages or expected closing rounds.
  Local validation and limitations are recorded in `docs/orchestrator-review.md`; hosted score and model
  latency validation still require credentials. No hosted quota consumed by this review.

## Tasks

| ID | Task | Status | Due |
|---|---|---|---|
| J0 | `scripts/probe_parallel.py` on D02: does the arena serialise calls per session? -> `BUYER_SERIALISED_SESSION` | script done, run pending (needs the arena key) | Sat 22:00 |
| J1 | Per-thread runner: one worker per supplier thread, case arbiter under a lock, `cli.py --agent buyer` routing, resume after a crash | done (arena-sim tests: RFQ bidders answered in time, resume) | Sat 18:45 |
| J2 | Accept / walk policy: acceptability invariant (must-haves, below the best opening, not above the alternative), never close above a cheaper sheet, rival may still beat (fit, `p_room`), one counter after a final offer, case-level walk | done (unit tests incl. the D02 16.10-vs-16.00 regression) | Sat 22:00 |
| J3 | RFQ: every live thread answered within 60 s; rival offers via tools' `rival_offer` claims, IDs reserved atomically across threads; keep-alive for held threads | done | Sat 22:00 |
| J4 | Timing: every role stage time-boxed with its rule twin; `scripts/timing_report.py`; stand-off and given-up-thread handling | done; live-model timing pending (issues.md 3) | Sun 00:30 |
| J5 | Sweeps v1/v2/v3 (10 sessions each), harvest, per-case deltas | sweep tool implemented and smoke-tested; hosted runs pending credentials | ongoing |
| J6 | Preflight, self-contained rehearsal, README run section, freeze, runner | local rehearsal and documentation done; final-machine preflight/freeze pending | Sun 10:30-13:15 |

## Asks to others

- @amine @saed - user-authorised integration review adds the optional/default-empty
  `CaseContext.supplier_estimates` field so standing C in the strategist includes the same relationship
  charge as tools and the arbiter. Tools prepare/refresh it and the live pipeline snapshots current estimates.
  No existing positional argument changes. See `DECISIONS.md` and the new review section.
- @amine - estimates now normalise per-part price units, ignore incomparable currency/invalid rows, and
  select the newest usable own cost evidence. Valid stated drivers refresh bands; bundle weights are not
  guessed. Fit and tit-for-tat compare unchanged non-price terms only. New synthetic regressions exercise
  unseen suppliers, row order, units, malformed shares and package changes; no practice-score tuning.
- @saed - merged `5e44a84`, retained the one-thread role view and clarified that the arbiter compares bidders.
  Removed practice percentages/fixed closing-round expectations; personas remain hypotheses to revise.
  Strategist standing-C, acceptance and ordering now include the supplied relationship-cost estimate.

- @saed - follow-up review reuses your writer text validator for email subjects in the orchestrator
  guard; the writer prompt now explicitly keeps claims/figures out of the subject. Keep-alive subjects
  receive the same check. No role or tool contract changed; see `docs/orchestrator-review.md`.

- @amine - SDK retry question resolved: the live buyer sets `max_retries=0` and a 12-second
  provider timeout; role deadlines and rule fallbacks remain in place. Offline precomputation keeps
  the kit's original defaults. Both provider paths have client-configuration regression tests.

- @amine @saed - review follow-up changes under Jeddou's user-authorised integration: Sunday facts
  now reach dossiers; new case/release evidence bypasses old profile fallbacks; timed-out queued model
  calls are cancelled. No contract change. Tests and rationale: `docs/orchestrator-review.md`.

- @saed - your `buyer_factory` wiring (98f9013) is kept in the merge: one shared `LLMProfiler`, `LLMStrategist` + `LLMWriter` per case, now driven by the per-thread runner. Please read issues.md 9-11 (language, one-thread view, accept/walk permissions).
- @amine - done as asked: accept checks price C with `tools.supplier_from_thread` (re-run on every reply, so a cost breakdown counts), `tools.refresh` every round (bands/trades synced back), `p_room` in the rival logic, one shared `ThreadSafePackage`. issues.md 12 (lock in the kit's `data.py`) is a team call.

## Log

- 16:30 - scaffold in place (`BuyerAgent`, `guard.py`, `--agent buyer` in cli, naive fallback on any error)
- 17:20-21:50 - orchestrator implemented on `feat/orchestrator`: runner (per-thread workers), arbiter, policy, guard, pipeline, observe/state/config/telemetry; cli routing; scripts probe_parallel / timing_report / preflight; harvest UTF-8 - 027e607
- contrast on arena-sim, same RFQ and a 1 s strategist: kit `play()` left the second bidder with 0 messages and a timeout; the runner answered both every round, 0 timeouts
- merged main (tools v1 + A8): adopted `thread_safe` and `supplier_from_thread`, kept `CaseContext.trades` - f3c24c7
- integration run with the real data package + Saed's roles (local merge): found two RFQ bidders sent the same index pair in the same second (`duplicate`); fixed by reserving IDs atomically before each POST - 0046f39; 3 reruns: 80 claims, all verified
- LLMProfiler wired, dossiers refined in parallel - ee09a3e; `p_room` in the rival logic - d5ed30a
- merged main with Saed's roles (98f9013): kept his factory wiring inside the orchestrator; full suite 413 passed, ruff clean; real-data sim run 27/27 claims verified
- 2026-10-24 review - waited for Claude to finish; reproduced and fixed state publication, recovery,
  cap handling and guard failures; bounded attachment work; added release evidence and cache fallback
  corrections. Pulled the team's merged PR (`d05f691`) without conflicts. Added sweep/rehearsal tools,
  tested local waves and documented remaining hosted/model checks in `docs/orchestrator-review.md`.
- 2026-10-24 follow-up - re-fetched every remote branch and confirmed PR #1 merged; checked Amine's tools
  and Saed's roles/prompts against the runner. Reproduced and fixed subject safety and premature citation
  burning; local rehearsal: 6/6 cases, zero timeouts, 48 verified claims, no duplicates/contradictions.
  Full suite: 443 passed, 2 optional skips; Ruff lint and format checks passed.
  Final-machine preflight still needs arena/model configuration and Sunday's release.

- 2026-10-24 negotiation review - merged Saed's `5e44a84` and extended PR #2 with unit-aware estimates,
  latest cost evidence, driver validation/refresh, comparable-package concession inference, consistent
  relationship C in the strategist, and a prompt free of practice-specific percentage/round expectations.
  35 new tests; full suite 478 passed, 2 optional skips, Ruff clean. Local rehearsal: 6/6, zero timeouts/caps,
  51 verified claims, no duplicates/contradictions. No hosted quota or model calls; credentials/release pending.

## Runs

| When | Commit | Cases | Sessions | Mean | Notes |
|---|---|---|---|---|---|
| Sat 15:20 | 7f92ca7 | 10 hosted | 10 | 54.4 | kit `--agent llm` baseline |
| Sat 21:40 | 0046f39 + roles (local) | arena-sim S01-S03, real data | 0 (local) | 73.1 | scripted sim supplier: not comparable with hosted scores; 27 claims, all verified |
| Sat review | working tree based on d05f691 | local 2-wave rehearsal, S01-S03 | 0 (local) | not a hosted benchmark | 6/6 closed before cap; 0 timeouts; 53 verified claims, 0 duplicates/contradictions |
| Sat follow-up | working tree based on ec25076 | local 2-wave rehearsal, S01-S03 | 0 (local) | not a hosted benchmark | 6/6 closed before cap; 0 timeouts; 48 verified claims, 0 duplicates/contradictions |
| Sat negotiation review | working tree based on 85de429 | local 2-wave rehearsal, S01-S03 | 0 (local) | not a hosted benchmark | 6/6 closed; 0 timeouts/caps; 51 verified claims, 0 duplicates/contradictions |
