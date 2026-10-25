# Amine - deterministic tools

Branch `feat/tools` · owns `starter-kit/arena_kit/agents/buyer/tools/` · skills: `data-package`, `replay-analysis` ·
plan and research: [amine-estimation-plan.md](amine-estimation-plan.md)

## Now

- Sun 10:30, branch `fix/hybrid-review` (off Jeddou's `feat/hybrid-deterministic`): review + fixes of the hybrid
  buyer, tools and orchestrator (user: no lane separation for this). Next: merge to `main` before the 13:00 freeze.

## Tasks

| ID | Task | Status | Due |
|---|---|---|---|
| A1 | `estimates.py` + `bands.py`: floor, fair, ceiling, target = cheapest package in C (relationship incl.) the supplier can accept; `calibrate.py` sanity check | done | Sat 19:00 |
| A2 | `offers.py`: midpoint anchor, Boulware (beta 0.5) + tit-for-tat until target (never past the midpoint), main sheet = cheapest package it can accept and never worth less to it than our last, up to 2 equal-C variants worth more to the supplier | done (v2) | Sat 22:00 |
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

- @jeddou @saed (22:40): **LLM client retries timeouts** - `arena_kit/agents/llm.py:60` `anthropic.Anthropic(timeout=45.0)` keeps the SDK default `max_retries=2`, so one call can hold a worker of the 32-thread pool in `roles/llm.py` ~135 s after `call_json` fell back; with 10 cases the pool fills and every role falls back. Fix: `max_retries=0` and a client timeout just under the role budget (`docs/research/04-llm-agent-engineering.md`).
- @jeddou @saed (22:40): `docs/research/README.md` lists candidate actions per owner from the web research (AC_next / AC_combi acceptance in code, round k/10 in prompts, option-ID menu for the strategist, datamarked supplier text, Writer checklist). Pick what fits your lane; cross-lane ones go to DECISIONS.md.
- @saed (21:00): **`ctx.trades[supplier_id]`** is live: each term change worth money to us, e.g. lead time 56 -> 28 days = EUR x, or EUR y per unit. The strategist can trade those explicitly ("can you do 28 days? we can move on price"); the tools never impose lead time or ppm.

- @saed (21:40): re your D01 test (0.11): that run used the **old placeholder** offers (branch older than main). Fixed on main now: every sheet covers the full need (no 12-of-24 months), a visible step every round until our target even when the supplier waits, your strategist's `Tactic.levers` each get an equal-cost sheet (fields must be negotiable with `term_options`), no sheet that "pays" for less coverage or sits far under our anchor. Please `git merge main` and re-run D01 (free) - expected path vs. its 51.90: 44.30, 45.30, 46.26, 47.22 ... with a 45-day alternative.
- @jeddou (21:40): **database lock fixed in tools**: `DataTools` wraps the package in `ThreadSafePackage` (one lock, ID index pre-built) - 19 cases in parallel went from 18 crashes to 0. If `agent.py` queries the package directly (not only `lookup`), wrap it too: `from .tools.safe_package import thread_safe`.

- @jeddou (18:55): with the user's OK I changed your files (please review): `runner.py` + `pipeline.py` keep-alive uses the
  strategist's question when it has no figures, rotates, and never repeats a body (D02 sent the same one 3x and the
  supplier closed); `pipeline.py` puts this round's fit into `ctx.fits`; `agent.py` builds the LLM client with the
  17 s role bound and no retries. issues.md 9 and 12 are done (see DECISIONS.md 18:35-18:48) - please close them there.
- @saed (18:55): with the user's OK I changed your files (please review): strategist view adds `concession` (fit, p_room) and
  `trades`; writer answers in the supplier's language (German template + German patterns in every check), gets
  `round_limit`; both prompts treat supplier text as data, never instructions; writer frames conditional trades.
- @jeddou (19:15): two more changes in your files (please review): `runner.iteration` calls `pipeline.sync` before
  the verdict (bands re-priced by a cost breakdown reach the policy at once); a counter to a `final_offer` goes out
  only if `policy.offers_more` (worth more to the supplier than our last message), else accept or hold.
- @jeddou (22:30): with the user's OK, more in your files (please review): `policy.verdict` - several suppliers
  compare best offers once each gave its best (final, agreement, no messages, or the same offer twice), then accept
  one; an agreement is never offered more; `cheaper_alternative` sends a supplier's cheaper alternative back;
  `others_settled`, `repeated_offer`; `runner.send` stops on 422 'round limit reached' (`th.exhausted`) and
  `arbiter._close` marks an ended thread dead; keep-alive questions ask for terms; `arbiter` logs since_reply_s.
  Open: the arena may serialise one session's calls - an accept waited ~30 s behind two supplier replies in P05.
- @saed (22:30): merged your PR #4 into fix/negotiation-issues (strategist.md conflict: your ordered decision kept;
  `accept_ready` returns True for agreed). Your notes are done: credible first offers (margin over firm cost),
  P01 creep (stable C-minimum floor), cheaper alternatives sent back, tool loop ends 1 s before the 17 s bound and
  asks for JSON only on the answer turn. Also `roles/llm.py` writes every call to runs/llm/<date>/.
- @jeddou @saed (18:55): kit changes in DECISIONS.md: `llm.py` (max_retries=0, timeout parameter) and `data.py` (one lock).

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

- 18:55 - merged main (all three branches were in via PR #1), D02 sandbox 0.524 (best D02), replay accuracy check:
  claims 2/2 verified, our figures match the trace, no timeouts; loss = 3 identical keep-alives to J1. Fixed with
  the user's OK across lanes, each test-first: offers (tit-for-tat only below target and never past the midpoint,
  main sheet = cheapest package it can accept and never worth less to it than our last: 4d45f68, 0b8b6fd),
  keep-alive never repeats (7d5a6d6), LLM client no retries + 17 s (592eb67), strategist sees fit + trades
  (1b3c430), writer in the supplier's language with German checks (421d764), kit DataPackage lock (4f290a3),
  roles research items (f51d63a). 453 tests. Research items already present: AC_next (no better counter ->
  accept), number/ID guard, urgency block, claim indices as an option menu, cost-share check (A8), payment value
  bounded by its rate. Not done on purpose: taper of late steps, final position by T-2 (concession-curve changes
  to measure in the sandbox first), precise non-round prices (computed prices are already precise).

- 19:15 - sandbox D02 on the fixes (53ce03986722d2b5): 0.357 - keep-alives no longer repeat, but J1 walked away
  after we answered its final offer with our last sheets unchanged. Three more fixes, test-first: bands re-priced
  before every verdict (a breakdown left J1 parked against a stale target: a4d3075), tit-for-tat answers every
  supplier move again, capped at the midpoint (422495b), no counter to a final offer that offers nothing more
  (082049f). Open (A8): a breakdown that lumps "overhead and profit" makes our floor its variable cost, so the
  target (12.66) sits below where it can sell - the final-offer rule now contains the damage.

- 19:40 - hosted practice sweep on main 4c30c05 (10 sessions): mean 50.2 vs baseline 54.4. Up: P08 0.57 (0.38),
  P10 0.65 (0.59), P11 0.63 (0.58). Down: P01 0.65 (0.91), P03 0.84 (0.91), P18 0.01 (0.19), P07 0.57 (0.66).
  Replays: (1) the policy only saw the supplier's first sheet - P18's alternative cost C 273,512 vs the 294,209
  we accepted (~0.30 instead of 0.01), P05 similar: standing = cheapest offered sheet (7fab87a); (2) we closed at
  our band target after one message (P01/P03/P11), but targets score 0.6-0.84: the target is a prior in the
  first half of the rounds unless p_room says no room (f28e992). Still open: suppliers go final after 1-2
  rounds (P10, P18, P04) and walk-prone personas get no counter; our first offers sit 25-28 % under them.

- 20:40 - since 19:15, all user-approved and test-first: accept only the supplier's standing sheet and never retry a
  refused accept (4408cf5, after P18 scored 0 on a 422 loop); no inter-supplier comparison per the organisers -
  claims, bands, policy, guard, prompts (a983fa3, 36018fe, 70a872d); audit fixes - a refused accept still sends a
  full offer, the rule fallback respects the target prior, prompt accept rules match the policy (70a872d);
  tools/credibility.py - lowest credible price from our evidence and the supplier's current offer, enforced in
  offers and shown to the strategist (3ce57d4, 8903772); keep-alives carry our standing offer (e787f86); merged
  Jeddou's PR #3 (strategist tool loop), conflicts resolved keeping one-on-one (885eec2). 517 tests.
  Sweep 885eec2: mean 55.1 (baseline 54.4, last 50.2). Next, from the live rounds: (1) stabilise cases send no
  offer - P02 (0, walked after 3 empty messages) and P03: our price sits below where C stops falling, so the squeeze
  charge makes every sheet dearer than its own; never price below the C minimum (band target) - my lane;
  (2) P05: two suppliers agreed (35.15, 35.39) while we held them for a cheaper third that went final (0.33), and
  one accept named a stale offer ID (422); (3) P11 repeated the same offer 3 rounds, went final (0.54);
  (4) P10 still opens 28 % under on a firm should-cost and gets an instant final offer.

- 22:30 - since 20:40, user-approved, test-first, branch fix/negotiation-issues: squeeze-case floor + no repeated
  price on a standstill (24ccdb4), stuck counts only offers (0c0c6f1), every supplier gets an offer (525fe5b), LLM
  call records in runs/llm (d2c4476); sweep e848872 58.6. Closing rule per the user: no instant accept, one supplier
  accepts when it cannot go lower within our margin, several suppliers compare best offers then accept one
  (a5b8eae); P05 then scored 0 - agreements now count as a supplier's best, round-limit and ended-thread 422s end
  the sending (cf0e20e). Merged Saed's PR #4 (fdbfca7). Saed's notes: credible first offers keep a healthy margin
  over firm cost evidence (4b6c993), a C-minimum floor that does not creep and a repricer that works there
  (133acc8), a supplier's cheaper alternative is sent back (8adf7c8), tool loop fits the clock and ends with JSON
  (8d54375), keep-alives ask for terms (4d75d57). The user: same offer twice = its best (b1ef5b5). Research: steps
  never grow above target (feaf893). 592 tests.

- Sun 10:30 - review of feat/hybrid-deterministic (3 parallel reviewers; 0 critical, 6 high, 7 medium) and fixes on
  fix/hybrid-review, test-first: full-cost estimate with company.md country factors and capped by the supplier's own
  offers (8a81730); planner credible floor from the estimate's band in anchors, rival_anchor and keep_margin, no
  strategic price above its quote, exchanges never dearer in C, null margin crash, volume format (2a88d35); scorecard
  and should-cost claims never praise the seller (e6d882f); composer answers "which figures?" and thanks once
  (28ca9d2); orchestrator (fork, 8f439eb): cheaper alternative at a last word sent back, 409 round limit, no e-mail
  twice, 15 s opening download cap. Rival use kept (user; DECISIONS 2026-10-25 10:30). 764 tests.

## Runs

| When | Commit | Cases | Sessions | Mean | Notes |
|---|---|---|---|---|---|
| Sat 20:15 | ef030f0 | sandbox D02 | 0 (free) | 0.21 | 3 sheets/round worked; lost rival to timeouts; accepted 16.10 final offer |
| Sat 18:22 | d05f691 | sandbox D02 | 0 (free) | 0.52 | merged main; claims 2/2 verified, no timeouts; same keep-alive 3x to J1 |
| Sat 18:56 | 0b8b6fd | sandbox D02 | 0 (free) | 0.36 | keep-alive fixed; J1 parked on a stale band, walked after our unmoved counter to its final |
| Sat 19:12 | 082049f | sandbox D01, D02 | 0 (free) | 0.69 | D01 0.86 (best), D02 0.52, no walk-away |
| Sat 19:13 | 4c30c05 | practice (10) | 10 | 50.2 | baseline 54.4; alternatives ignored (P18 0.01), closed at target after 1 msg (P01, P03) |
| Sat 19:26 | 26b72a8 | practice P18 | 1 | 0.00 | accepted the supplier's alternative by ID: 422 "not a standing offer", retried until timeout (fixed 4408cf5) |
| Sat 20:30 | 885eec2 | practice (10) | 10 | 55.1 | baseline 54.4, last 50.2; P18 0.36, P07 0.68, P08 0.61 up; P02 0 and P03 offer-less (squeeze); P05 0.33 held two agreed suppliers |
| Sat 21:17 | e848872 | practice (10) | 10 | 58.6 | P02 0.33 (first ever), P11 0.71, P18 0.50; P05 0.07: instant accept signed the dearer supplier |
| Sat 21:37 | a5b8eae | practice P05 | 1 | 0.00 | waited on an above-target agreement; cheapest supplier walked; round-limit 422 ignored; timeout |
| Sun 10:40 | 28ca9d2+8f439eb | sandbox D01, D02 | 0 (free) | 0.78 | D01 0.943, D02 0.618: anchor needing an estimate opened D02 at 0.86, J1 agreed 14.70 |
| Sun 10:55 | 845068a | sandbox D02 | 0 (free) | 0.70 | 0.80 anchor restored: D02 0.702 (deal 14.30, J1 final offer), same as before the fixes |
