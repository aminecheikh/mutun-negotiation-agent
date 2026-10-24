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

## Follow-up review - 24 October 2026

Recovered the earlier review conversation and checked the latest remote branches, team asks, deterministic
tools, role implementations and all three active prompts. PR #1 is merged; `origin/main` and `origin/feat/tools`
remain at `1dde1b2`, with Saed's `98f9013` already integrated. The recovery and client-timeout fixes above were
still local, so the follow-up PR includes them as well as these additional corrections:

- A safe body could carry an unsafe subject: budgets, private supplier information, invented figures and
  untracked data IDs bypassed validation. Subjects now reuse Saed's existing text checks plus the guard's
  German leak checks. Invalid subjects become a neutral heading. The last send guard and keep-alive path
  apply this too, including when the supplier supplied the subject. The writer prompt states the same rule.
- Citation reservation happened before the final short-body fallback. Evidence removed by that fallback
  was incorrectly marked used for the rest of the case. Only IDs in the final outgoing body are now reserved;
  the lock still prevents parallel bidders from citing the same evidence.

Eight new regression cases failed before these fixes; the ninth confirms safe subjects remain unchanged.
The targeted orchestrator/recovery/writer check passed with 66 tests and one optional fixture skip.
The full suite passed with **443 tests and 2 skipped**; Ruff lint and formatting checks passed.
The local two-wave rehearsal completed 6/6 cases with zero timeouts or cap closures, and 48 verified claims
with zero duplicate or contradicted claims. Evidence: `starter-kit/runs/codex-followup-review/report.json`
and `sessions.json`; the report correctly identifies an uncommitted working tree based on `ec25076`.

Preflight again loaded 10,356 facts and passed the forbidden-endpoint scan. It failed the required arena
and LLM environment checks; the Sunday release is absent. No hosted quota or model API calls were used.
Hosted-model latency, supplier behavior and negotiation-score improvements remain unverified.


## Negotiation and estimation generalisation review - 24 October 2026

Recovered the original orchestration conversation and its latest follow-up. Fetched every remote branch,
fast-forward checked the open PR branch, and merged Saed's `5e44a84` prompt update before editing. The
one-thread role view is preserved: the deterministic arbiter still compares bidders. All changes in this
review extend PR #2; they do not create a separate PR or merge into main.

### Corrections grounded in economics and evidence

- **Comparable price units.** Should-cost and market-price estimates now convert each row to the part's
  `price_unit`, as required by `data/README.md` and `brief/api.md`. EUR prices are compared only with EUR;
  absent, non-positive and non-finite unit/price inputs are ignored. Without a known part unit, the tools
  fall back to case-level anchors rather than invent a conversion. This normalises arithmetic only;
  original citable facts and claim figures are unchanged.
- **Evidence order.** The latest usable supplier-specific should-cost wins, followed by the latest generic
  estimate, then another plant's estimate. Source row IDs, country-scaling assumptions and uncertainty
  remain explicit. A newer generic row does not displace a supplier-specific model.
- **Cost drivers.** Negative/non-finite shares, shares above one and aggregate shares above 100% cannot
  move the fair-price estimate. A bundle does not inherit the first component's weights. Valid newly stated
  cost drivers refresh bands/trades even when no spreadsheet breakdown arrived.
- **Trades versus concessions.** Reservation-price inference uses the latest uninterrupted series on
  unchanged non-price terms. Changed or missing terms start a fresh comparison. A price reduction paid
  for by different payment, freight, volume or contract terms no longer earns an automatic matching
  price concession. Same-package concessions retain the existing schedule; no new strategy constant is fitted.
- **One cost basis for roles and policy.** The strategist's total C previously omitted relationship cost
  while its target and the arbiter included it. `CaseContext.supplier_estimates` is an additive field with
  an empty default; tools prepare/refresh it, and the live pipeline passes a private snapshot of the
  arbiter's estimates. Role acceptance, bidder ordering and the model's standing-C input now include the
  same supplier relationship economics. Existing positional constructors remain valid. No new estimate
  is generated by an LLM, and private estimator inputs are not added to outgoing messages.
- **Prompt generalisation.** Removed practice-derived concession percentages, expected final-offer rounds,
  guaranteed persona responses and the instruction to always move a non-price term. The prompt now asks
  for evidence-based hypotheses, complete-package C, available terms and revised beliefs when live
  evidence contradicts a prior. It preserves Saed's per-thread interface and the orchestrator's authority.

### Research and limits

Revisited primary sources on [negotiation decision functions](https://www.sciencedirect.com/science/article/pii/S0921889098000293),
[multi-issue trade-offs](https://www.sciencedirect.com/science/article/pii/S0004370202002904), and
[acceptance conditions](https://homepages.cwi.nl/~baarslag/pub/Acceptance_conditions_in_automated_negotiation.pdf).
Our engineering inference is that a price path should compare the same package, and acceptance should
use consistent utility/cost. These papers do not establish an optimal configuration for this arena.
A wholesale concession-curve change, relative tit-for-tat, and a new AC_next/AC_combi rule remain candidates
for controlled evaluation rather than being installed from published benchmark percentages.

The existing margin, country and uncertainty priors are still assumptions; market prices do not reveal a
supplier's actual reservation price. Full overfitting immunity cannot be proved from local tests. This review
adds no case/supplier exceptions and tunes no parameters on practice scores. Tests check equivalent economic
representations and perturbed evidence, plus the existing 19-case mutation matrix. They establish the stated
properties, not future negotiation scores. Restarting a fit on changed terms intentionally sacrifices some
history rather than assume the value of an unmodelled trade.

### Validation of this revision

- Added 35 tests (synthetic unit/evidence invariants, changed-term concession paths, relationship-cost
  consistency). The initial estimation and concession regressions reproduced 26 failures before fixes.
- Full suite: **478 passed, 2 optional skips**. Ruff lint and formatting passed; the strategist tests also
  passed independently after aligning its arithmetic-error fallback with the arbiter.
- Local two-wave rehearsal: **6/6 cases**, zero timeouts, zero wave-cap closures, **51 verified claims**,
  no duplicate or contradicted claims. Report and sessions: `starter-kit/runs/codex-negotiation-generalization/`.
  The report truthfully records the uncommitted revision based on merge commit `85de429`.
- Preflight with `ARENA_DATA=../data`: 10,356 facts loaded and forbidden-endpoint scan passed. Arena/model
  settings are absent and the Sunday release is not present, so preflight is not ready for the final.
  No hosted quota or model API calls were consumed. The deterministic rehearsal cannot validate the
  revised prompt's model behaviour or demonstrate a hosted-score improvement.
- Re-fetched remote branches after implementation: main, tools and Saed's roles were already integrated.
  PR #2 has no substantive review comments; the Copilot review reports insufficient review quota.
