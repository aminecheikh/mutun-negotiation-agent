# Open issues: orchestrator (Jeddou)

What is not verified yet, what is an assumption, and what needs a decision from someone else. Written with the
orchestrator PR (`feat/orchestrator`). Each item says how to close it. Newest decisions go to
`docs/team/DECISIONS.md`, not here.

## Not verified yet (needs the hosted arena or a live model)

1. **Does the arena serialise calls on one session?** Unknown. The runner sends to every thread of a case in
   parallel; if the server queues calls per session, a third RFQ thread could wait behind two others. The runner
   has an ordered send gate behind `BUYER_SERIALISED_SESSION=1` (oldest supplier reply first), off by default.
   arena-sim says "not serialised", but it answers instantly, so that proves nothing.
   *Close it:* `uv run python scripts/probe_parallel.py case:D02` (sandbox, no quota); it writes
   `runs/probe-parallel.json` and prints the value to set.

2. **No run against the hosted arena yet.** This machine has no `.env` (no arena key). Everything was tested on
   arena-sim (real clock, shortened 60 s rule) and with the real data package. The sim's supplier is a fixed
   script, so its scores (0.64-0.87 with the real tools) say nothing about the real suppliers.
   *Close it:* `uv run arena play case:D01 --agent buyer -v` and `case:D02` (free), then
   `uv run python scripts/timing_report.py`.

3. **No run with a live Claude model yet.** No `ANTHROPIC_API_KEY` here; every role ran on its rule twin. The
   orchestrator bounds the strategist at 17 s and the writer at 16 s (Saed's roles use 15 s inside), inside a
   48 s move budget. Saed's bench measured strategist p90 7.3 s and writer p90 7.6 s at 10 in parallel, which
   fits, but the full round (both calls plus the supplier's reply) has not been timed end to end.
   *Close it:* one sandbox case with `LLM_MODEL=claude-opus-5-5`, then `timing_report.py`: the `choose` and
   `write` rows show how often the model answered in time.

4. **A question without a term sheet after a `final_offer`.** A parked thread (its sheet is held while a rival
   may still go lower) gets at most two keep-alive questions so its 60 s clock does not run out. The brief says a
   question is answered and the quote stands; whether a bot counts it as "pushing after a final offer" is
   unverified. The alternative (letting it time out) repeats our last offer, which certainly counts as pushing.
   *Close it:* read the replay of the first RFQ where a final offer was parked.

## Assumptions in the code (documented in `config.py`, not tuned on practice scores)

5. Time constants come from the brief and the kit: 48 s move budget (12 s cushion under 60 s), close 90 s before
   the cap or slowest round + 15 s (kit `play.py`), freeze 150 s, last call 30 s.
6. Policy thresholds are on the cost function's own scale: accept within 0.5 % of the band's target C; one counter
   after a final offer only if it is more than 2 % above target; a rival "could beat" a sheet if it can plausibly
   go 1 % lower; a rival with `p_room` < 0.2 counts as done. These are judgement calls, documented, few.
7. **`moves_left` counts our messages only.** On the arena a timeout also uses a round. The kit has the same
   approximation; the runner is built not to time out, so the effect should be small.
8. **Our `C_open` uses our supplier estimate** for the relationship charge; the arena uses the true values. A deal
   priced just under our `C_open` could sit just above the arena's in a stabilise / secure_supply case.

## Needs a decision or work from a teammate

9. **@Saed - language.** Supplier e-mails can be German, English or mixed. The orchestrator's own keep-alive
   questions are in German or English by the supplier's last e-mail; the writer and its template are English only.
   Mirroring the supplier's language is the writer's job. The guard does not enforce a language.
10. **@Saed - the strategist sees one thread at a time.** Each worker asks the strategist about its own thread (the
    claims list is filtered for that supplier, so claim indices stay valid and each ID is cited once per case).
    The strategist therefore cannot compare bidders itself; it learns about rivals through the `rival_offer`
    claims, and the orchestrator does the cross-thread decisions (who to accept, who to hold). If the strategist
    should see a summary of the other threads, that is a small addition to its input, to agree together.
11. **@Saed - the strategist's `accept` and `walk_away`.** It may choose `accept` only where the policy permits it
    (acceptable sheet, no cheaper or likely-cheaper rival); otherwise it becomes `send`. `walk_away` is a hint to
    the arbiter, which walks only when no acceptable sheet exists and nothing can still produce one.
12. **@Amine @Saed - the kit's `DataPackage` lock.** The agent is safe: `DataTools` wraps the package in
    `ThreadSafePackage` and the orchestrator shares that one object. Saed's bench uses the raw package, so a lock
    inside the kit's `arena_kit/data.py` would also protect other callers; it is a kit change, so a team decision.
13. **@team - roles not on `main` yet.** `buyer_factory` uses `LLMStrategist` and `LLMProfiler` when the roles package
    exports them; on today's `main` it falls back to the rule strategist and the passthrough profiler. Verified on a
    local merge of `feat/roles-saeb` (not pushed): 411 tests pass, the factory builds `LLMProfiler`,
    `LLMStrategist` and `LLMWriter`, and the three sim cases with the real data sent 27 claims, all `verified`.
    Until roles merge, the rule strategist on `main` never cites claims.

## Not done from the orchestrator plan

14. `scripts/sweep.py` (run + harvest + per-case delta table) and `scripts/rehearse.py`. Meanwhile: the kit's
    `arena run` + `scripts/harvest.py --digest`, and the rehearsal commands in skill `final-runbook`. A two-wave
    rehearsal on arena-sim is covered by `tests/test_orchestrator_runner.py`.
15. The `RoundFrame` contract additions in the plan were not proposed: the roles do not accept a frame argument,
    so the frame stays internal to the orchestrator (verdict, permissions, filtered claims). No `contracts.py`
    change in this PR.
16. A `contradicted` claim is logged loudly with its sentence, but the orchestrator does not stop using that claim
    kind automatically (claims are pre-verified by the tools; one would be a tools bug to fix at the source).
