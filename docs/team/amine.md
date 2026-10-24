# Amine - deterministic tools

Branch `feat/tools` · owns `starter-kit/arena_kit/agents/buyer/tools/` · skills: `data-package`, `replay-analysis` ·
plan and research: [amine-estimation-plan.md](amine-estimation-plan.md)

## Now

- Tools complete (A1-A5, A7-A9) on `main`. Next: A6 Sunday release; one hosted sweep (10 sessions) once Jeddou's
  J2/J3 and Saed's writer fixes are merged, then fix what the replays show.

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
| A8 | `signals.py` (its stated cost shares -> its own fair price; cost breakdown -> exact floor, re-priced on `refresh`) + `levers.py` (EUR value of every term change -> `ctx.trades`) | done | Sun 02:00 |
| A9 | Anchors without cost data (fair, today's price, cheapest rival, fallback), honest labels for unknown suppliers | done | Sat 20:00 |

## Asks to others

- @jeddou (20:20): **J3 first** - tools-v1 sandbox D02 again lost the second bidder to 3 timeouts; every live thread needs a message within 60 s.
- @jeddou (20:20): **J2 accept policy** - D02 accepted a final offer at 16.10, worse than that supplier's own 16.00 opening, with a rival still open. Use `band.c_target` / `band.c_ceiling`, `ConcessionFit.p_room` (new) and compare against every live thread before accepting.
- @jeddou (20:20): call `tools.refresh(ctx, threads)` each round - claims follow the threads (series the supplier names, rival offers). Bands stay as prepared.
- @saed (20:20): **writer** - never state data that is not in `claim_sentences`: D02's writer mentioned "comparable machined shafts" and "the full cost picture" with no data and the supplier asked for it (costs trust). Claims are now real: max 3 per message, and each ID only once per case (track what was cited).
- @saed (20:20): **strategist/analyst/profiler inputs are live now**: `Dossier.persona/levers/evidence` (A5), `ctx.claims` (A3), `ConcessionFit.reservation_estimate` + `p_room` (A4). Lead time and ppm are kept at the supplier's standard by the tools: asking for them is an explicit trade for the strategist ("what price at 35 days?").

- @saed (20:30): `roles/playbook.md` on feat/roles-saeb has a table of practice suppliers (Brandlhuber 70110, Nordmark 70510 ...). Keep the general rules ("over-ask vs fair price -> ...", "already below should-cost -> don't squeeze") but don't feed practice names or numbers to the prompts: the final's suppliers are new (CLAUDE.md "Generalise, don't overfit"). The tools now compute those numbers for any supplier (`band.fair`, `band.floor`, `dossier`).

- @jeddou (21:00): **guard fix (J2)** - `guard.accept_ok` computes C without our supplier estimate, so in stabilise / secure_supply cases it ignores the squeeze penalty (the slide's "supplier's health"). Use `tools.supplier_from_thread(case, sid, events)` as the 4th argument of `total_cost` there, like the offer engine does.
- @saed (21:00): **`ctx.trades[supplier_id]`** is live: each term change worth money to us, e.g. lead time 56 -> 28 days = EUR x, or EUR y per unit. The strategist can trade those explicitly ("can you do 28 days? we can move on price"); the tools never impose lead time or ppm.

- @saed (21:40): re your D01 test (0.11): that run used the **old placeholder** offers (branch older than main). Fixed on main now: every sheet covers the full need (no 12-of-24 months), a visible step every round until our target even when the supplier waits, your strategist's `Tactic.levers` each get an equal-cost sheet (fields must be negotiable with `term_options`), no sheet that "pays" for less coverage or sits far under our anchor. Please `git merge main` and re-run D01 (free) - expected path vs. its 51.90: 44.30, 45.30, 46.26, 47.22 ... with a 45-day alternative.
- @jeddou (21:40): **database lock fixed in tools**: `DataTools` wraps the package in `ThreadSafePackage` (one lock, ID index pre-built) - 19 cases in parallel went from 18 crashes to 0. If `agent.py` queries the package directly (not only `lookup`), wrap it too: `from .tools.safe_package import thread_safe`.

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

- 21:00 - A8 signals.py (supplier-stated cost shares, cost breakdown -> floor, `refresh` re-prices) + levers.py (`ctx.trades`): every term on Tacto's "Why every term costs money" slide is now priced; 332 tests

- 21:40 - from Saed's D01 replay: thread-safe package (18/19 parallel crashes -> 0), coverage search without a cost estimate, visible step per round to target, strategist levers in sheets, no less-coverage / non-credible variants; sandbox fixtures saved; 337 tests

- 22:30 - web research by 4 agents in `docs/research/` (LLM negotiators, strategy algorithms, procurement
  practice, LLM engineering) + README index with candidate actions per owner; 2 citations spot-checked
  (arXiv 2402.15813, 2601.13206); kit `llm.py` retry/timeout issue confirmed in code

## Runs

| When | Commit | Cases | Sessions | Mean | Notes |
|---|---|---|---|---|---|
| Sat 20:15 | ef030f0 | sandbox D02 | 0 (free) | 0.21 | 3 sheets/round worked; lost rival to timeouts; accepted 16.10 final offer |
