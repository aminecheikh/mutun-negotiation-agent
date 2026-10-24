# Amine - deterministic tools

Branch `feat/tools` · owns `starter-kit/arena_kit/agents/buyer/tools/` · skills: `data-package`, `replay-analysis` ·
plan and research: [amine-estimation-plan.md](amine-estimation-plan.md)

## Now

- Tools v1 complete and on `main`. Next: A8 (read the supplier's own statements: driver shares, cost breakdown XLSX),
  then one hosted sweep together with Jeddou's J2/J3 to measure.

## Tasks

| ID | Task | Status | Due |
|---|---|---|---|
| A1 | `estimates.py` + `bands.py`: floor, fair, ceiling, target = cheapest package in C (relationship incl.) the supplier can accept; `calibrate.py` sanity check | done | Sat 19:00 |
| A2 | `offers.py`: midpoint anchor, Boulware (beta 0.5) + absolute tit-for-tat, up to 2 equal-C variants worth more to the supplier | done (v1) | Sat 22:00 |
| A3 | `claims.py`: pre-verified claim sentences (index, rival quotes/offers, scorecard, quality, PO, forecast, should-cost, notice) | done (agent-built, reviewed) | Sat 22:00 |
| A4 | `concession.py`: Bayesian reservation estimate + `p_room` | done | Sun 02:00 |
| A5 | `dossier.py`: persona, levers, history, second source, evidence lines (intel, spend share, invoices, scorecards, contracts, forecasts, notes) | done (agent-built, reviewed) | Sun 02:00 |
| A6 | Sunday release: new IDs reach dossiers and claims (DataPackage merges facts; check documents/news) | todo | Sun 08:30 |
| A7 | Edge cases | done: robustness suite (19 case files x 6 mutations) | Sun 10:30 |
| A8 | `signals.py`: supplier-stated cost drivers and shares -> its own fair price; cost breakdown XLSX -> exact cost; case `facts` letters (asked price) | todo | Sun 02:00 |
| A9 | Anchors without cost data (fair, today's price, cheapest rival, fallback), honest labels for unknown suppliers | done | Sat 20:00 |

## Asks to others

- @jeddou (20:20): **J3 first** - tools-v1 sandbox D02 again lost the second bidder to 3 timeouts; every live thread needs a message within 60 s.
- @jeddou (20:20): **J2 accept policy** - D02 accepted a final offer at 16.10, worse than that supplier's own 16.00 opening, with a rival still open. Use `band.c_target` / `band.c_ceiling`, `ConcessionFit.p_room` (new) and compare against every live thread before accepting.
- @jeddou (20:20): call `tools.refresh(ctx, threads)` each round - claims follow the threads (series the supplier names, rival offers). Bands stay as prepared.
- @saed (20:20): **writer** - never state data that is not in `claim_sentences`: D02's writer mentioned "comparable machined shafts" and "the full cost picture" with no data and the supplier asked for it (costs trust). Claims are now real: max 3 per message, and each ID only once per case (track what was cited).
- @saed (20:20): **strategist/analyst/profiler inputs are live now**: `Dossier.persona/levers/evidence` (A5), `ctx.claims` (A3), `ConcessionFit.reservation_estimate` + `p_room` (A4). Lead time and ppm are kept at the supplier's standard by the tools: asking for them is an explicit trade for the strategist ("what price at 35 days?").

## Log

- 16:30 - scaffold in place (placeholders in every tools module, tests in `tests/test_buyer.py`)
- 18:00 - research + plan for the hidden quantities: amine-estimation-plan.md
- 18:00 - A1 estimates.py (full cost, cost of money, fair price, index change) - b281662
- 18:40 - A1 bands.py v1 + calibrate.py; target/best 1.01-1.02 where should-cost models the supplier's plant - a40c076
- 19:10 - robustness suite on all 19 case files x 6 mutations; fixed negative target on unit mismatch and biased option search; generalisation rules in CLAUDE.md - 4c29077
- 19:40 - A9 anchors without cost data, own-price fallback, honest labels - 0fc7190
- 19:50 - A2 offer engine - 918a602
- 20:00 - A3 claims + A5 dossier (two background agents in worktrees, merged after scope and overfitting review); claims wired with threads + `refresh` - 8b4db2b
- 20:10 - A4 concession posterior (`p_room` added to contracts, DECISIONS.md) - ef030f0
- 20:15 - sandbox D02 with tools v1 (free): 0.21; supplier raised its price because our sheets imposed a 35-day lead time it never offered -> fields it left out now keep its standard terms; unmodelled-cost terms held - b455de8. Rest of the loss: J2/J3, writer (see asks)

## Runs

| When | Commit | Cases | Sessions | Mean | Notes |
|---|---|---|---|---|---|
| Sat 20:15 | ef030f0 | sandbox D02 | 0 (free) | 0.21 | 3 sheets/round worked; lost rival to timeouts; accepted 16.10 final offer |
