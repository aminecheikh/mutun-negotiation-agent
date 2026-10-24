# Orchestrator review and integration - 24 October 2026

Reviewed after Claude's implementation completed. Baseline: `1fc2d52`, 413 passing tests and
2 skipped. The team subsequently merged PR #1 as `d05f691`; that merge was fetched and integrated
without conflicts. A local backup branch, `backup/orchestrator-before-codex-review-20261024`,
preserves the original implementation.

## Architecture decision

Keep the deterministic orchestrator, one worker per supplier and one case arbiter. This directly
addresses independent 60-second supplier clocks and gives one place to enforce total-cost and
must-have constraints. Adding another model to schedule or approve actions would add latency and
make failures harder to recover from. The current strategist and writer remain separate, with
their established fallbacks; there is no measured end-to-end hosted evidence here to justify
replacing the team's working interfaces with the larger plan-v2 framework.

Inputs reviewed: the challenge presentation (including the score and cost-component slides),
`brief/brief.md`, `brief/api.md`, submission rules, data documentation and source schema,
team decisions/tracking, Amine's tools, Saed's roles, the local orchestrator plan/spec and plan-v2,
and the implemented runner, state, policy, guard and tests. The private plan documents remain local.

## Corrected failures

| Failure | Resulting behavior |
|---|---|
| A send cleared `in_flight` before publishing the reply | History, standing terms and cost are published before the thread becomes idle |
| Attachment errors discarded an already acknowledged exchange | The reply and consumed round survive; enrichment has an 8-second budget and late work cannot mutate published state |
| Every closing HTTP 409 meant "case ended" | Recoverable 409 and stale-offer 422 refresh server history and continue; only authoritative ended state closes locally |
| Lost POST responses could trigger a new move from stale history | The worker reconciles its own session/thread before another move; uncertainty blocks accepting that offer |
| Resume reset the clock and ignored timeout rounds | Recovery uses the server's remaining clock, counts timeout rounds, and starts with deterministic roles |
| Fixed numeric deadlines were silently ignored | Numeric caps and callable/extended caps both work |
| A cheaper in-flight offer blocked the final cutoff accept | The last-call window can close the best idle acceptable offer; earlier windows continue comparing pending rivals |
| A stale walk decision could race with a new acceptable offer | Walk checks the latest acceptable offer again under the lock |
| Reservation could restore a completely scrubbed body | A neutral fallback replaces it; removed claims and leaks stay removed |
| Failed quote downloads could leave missing terms valued as buyer defaults | An accept is rejected while explicitly missing terms are unknown |
| Refreshed supplier estimates changed current cost but left opening cost stale | Both are valued with the same updated estimate |
| Template fallback refreshed its claim menu after choosing indices | The exact context that produced the tactic travels with the result |
| Timed-out queued model jobs could execute after fallback | Queued futures are cancelled; already-running jobs remain bounded by provider timeouts |
| Temporary final-wave polling failures terminated the runner | Transport errors and transient server/rate-limit errors retry polling |
| Sunday release facts were loaded but missing from dossiers | Relevant new facts and document text enter dossiers ahead of old evidence and influence rule signals |
| Cached profiles could override newer case/release evidence | New evidence triggers refinement; failure retains current rules instead of restoring an old persona |

The cross-layer changes are small: expose the loaded release facts, include them in dossiers,
invalidate old profiles when new evidence exists, cancel expired role jobs, and mark resumed tables.
No shared contract signature changed.

The opening/resume attachment budget is shared across the entire history, so resuming a long
thread cannot accumulate one full timeout for each older document.

Amine's follow-up retry question is resolved: the live buyer uses a 12-second provider timeout
and zero Anthropic SDK retries, leaving room before the roles' 15-second fallback. The compatible
HTTP provider receives the same timeout and already has no retries. Offline profile precomputation
and the kit example retain their original defaults. This matters because the SDK otherwise retries
timeouts twice ([official Python SDK documentation](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python)).
Outer role deadlines still bound waiting: a network timeout is not a guaranteed wall-clock cancellation
of an already-running request, and the live profiler has its own shorter six-second fallback.

## Operations completed

- `scripts/rehearse.py`: self-contained two-wave simulator run with real data, ordinary API pacing,
  deterministic roles, saved sessions, claim counts, closure checks and an exit status.
- `scripts/sweep.py`: labelled sandbox/practice runs, quota count before execution, explicit practice
  execution, saved session IDs and per-case comparisons. Each report records the commit and whether
  the checkout had uncommitted work. Existing labels are preserved.
- Timing reports now distinguish buyer decision time from supplier response time, which the rules
  exclude from the 60-second buyer clock. Round logs include session IDs.
- Preflight checks both practice and sandbox sessions and supports `--require-llm` for the selected
  provider. The README documents the team's buyer rather than defaulting to the organiser's naive agent.

## Validation

The original suite passed before edits. Regression tests first reproduced the concurrency, deadline,
guard and conflict-handling failures. Targeted tests cover uncertain POSTs against the real-clock HTTP
simulator, timeouts, restart, transient polling errors, stale profiles, release ingestion, and the sweep CLI.
The final integrated suite passed with **434 tests and 2 skipped**. The skips are the optional hosted
arena check (no test credentials) and the harvested-session writer check (no local harvested sessions).
Ruff lint and formatting checks pass. The recovery regressions also pass independently.

The real-data local rehearsal completed **6/6 cases across two waves, zero timeouts, zero wave-cap
closures, and 51 verified claims with zero duplicates or contradictions** on clean commit `13d712f`.
Evidence is in `starter-kit/runs/codex-final-rehearsal/report.json` and `sessions.json` (local, gitignored).
An earlier rehearsal also passed with 53 verified claims; concurrent claim reservations can change the count.
The subsequent provider-configuration change is covered by stubbed client tests; this deterministic
rehearsal does not call an LLM.
These are scripted-supplier reliability results, not evidence of an improved hosted negotiation score.

## Remaining live validation

This checkout has no `.env` and no arena/model credentials in its process environment. No hosted
practice quota was consumed and no model API calls were made during this review. The actual Sunday
release was not yet available; ingestion was tested with a synthetic release in the documented format.
Preflight confirmed 10,356 loaded facts and passed the forbidden-endpoint scan; it correctly failed
the arena/model environment checks. The existing Python 3.12 virtual environment works, but `uv`
is not on the current shell's PATH.

Before using the final runner:

1. Configure credentials on the chosen machine; run preflight and sandbox D01/D02 with the selected model.
2. Measure end-to-end buyer latency at the expected concurrency. Role timeouts bound waiting, but cannot
   guarantee network delivery; deterministic preparation and request retries also consume time.
3. Run the parallel probe and inspect its raw timings. A single pair of unequal reply times cannot prove
   server serialization. Do not enable the optional serialized gate from that heuristic alone; it can
   consume another supplier's response window while waiting for the first reply.
4. Evaluate one controlled practice sweep, then inspect weak replays and per-case deltas. Keep strategy
   changes grounded in mechanisms and repeated evidence rather than fitting practice scores.
5. After Sunday's download, confirm the real release reaches dossiers, redo sandbox/preflight, and
   record the final commit hash before the code freeze.

Open strategic questions remain documented in `docs/issues.md`: whether suppliers tolerate keep-alive
questions after final offers, the writer's English-only fallback, and the estimated supplier relationship
cost. This review preserves those explicit assumptions; it does not claim an optimal negotiation policy.
