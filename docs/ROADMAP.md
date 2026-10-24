# Team Mutun - buyer agent: architecture and roadmap

Deadline: **Sun 13:00 code freeze**, Live Final 13:30 (5 waves x 8 min, up to 10 cases in parallel).
Baseline (kit LLM agent, Sat 15:20): **mean 54.4** over the 10 hosted cases; 189 practice sessions left.

## What the score rewards

- Score is measured in **total cost C**, not unit price: `(C_open - C_deal) / (C_open - C_best)`.
  Payment days, `effective_date`, contract length (uncovered need is costly), Incoterm, PPM/chargeback
  and index clauses all count.
- `stabilise` / `secure_supply` cases **charge for squeezing the supplier** below its sustainable margin
  (P02 baseline: lower price, EUR 139k relationship charge, score 0).
- Suppliers have a hard cost floor and an approval ladder, and give nothing without a move from us.
  Verified data alone rarely moves them ("it holds, but it does not change our calculation").
- Hidden cases use **suppliers and parts practice never shows**: nothing supplier-specific in code.

## Baseline diagnosis

| Problem | Cases | Fix (layer) |
|---|---|---|
| First offer too generous, supplier agreed at once | P02, P18, P08 | anchor near floor, slow concession (tools: bands, offers) |
| Relationship charge not seen | P02 | C with relationship term in every evaluation (tools) |
| Index clause accepted on rising path | P02, P08, P18 | evaluate clauses in C, propose only when they help (tools) |
| Closed after 2-3 of 10 rounds | P08, P10, P11, P18 | accept policy uses bands + behaviour read (orchestrator, roles) |
| RFQ: 10 rounds with one supplier | P05 | multi-thread play, cite rival quotes (orchestrator, tools) |
| Arguments not about the supplier's cost drivers | P02 | claims from its own drivers (tools), chosen per persona (roles) |

## Architecture

```
          PREPARE (once per case, before the first move)            owner
 case + data package -> dossier, bands, claims  -> CaseContext         Amine (tools)

          EACH ROUND (budget < 30 s, 60 s hard limit)
 threads -> tools.fit_concessions        numbers: where its price converges      Amine
         -> analyst.read                 words: is it still open, what it wants   Saed (roles)
         -> strategist.choose            Tactic: send/accept/walk, levers, claims Saed
         -> tools.plan_offers            1-3 term sheets + C, anchor/concession   Amine
         -> writer.write                 the e-mail around claims + term sheets   Saed
         -> guard                        must-haves, C <= ceiling, IDs, no leaks  Jeddou
         -> arena message
 orchestrator = agent.py: runs the steps, timeouts, fallbacks, RFQ focus, closing  Jeddou
```

The interfaces are in `starter-kit/arena_kit/agents/buyer/contracts.py` (frozen dataclasses +
Protocols). **Each owner codes against them, with stubs for the others, so nobody waits.**

The orchestrator is **plain code, not an LLM agent**: a fixed pipeline with LLM roles at fixed points.
With 60 s per move and 10 cases in parallel, a free-form agent conversation is too slow and too hard to
debug, and a deterministic pipeline is easier to explain to the jury.

## Team

### Jeddou - orchestrator (`agents/buyer/agent.py`, `cli.py`, `scripts/`)
1. Walking skeleton: `BuyerAgent.decide()` wired to stub tools/roles, `--agent buyer` in `cli.py`,
   plays D01 end to end. **First, so the others can plug in.**
2. Timing: per-call timeouts (e.g. analyst 12 s, strategist 12 s, writer 12 s; run analyst calls of
   several threads in parallel), deterministic fallback (tools offer + template e-mail).
3. Guard and closer: must-haves, `C <= ceiling`, beats the alternative, no accept before round 3 unless
   `C <= c_target`, walk when nothing beats the alternative; reuse `play.closing_move` near the cap.
4. RFQ policy: which thread gets this round's message, keep all bidders alive, close the best.
5. Ops: practice sweeps + `harvest.py`, quota ledger, final rehearsal, README, final runner.

### Saed - LLM roles (`agents/buyer/roles/`)
1. Writer: polite, <= 120 words, uses only the given claim sentences and term sheets, never leaks
   urgency, budget or missing alternative.
2. Strategist: returns `Tactic` JSON; picks levers and claims from the dossier persona and behaviour read.
3. Analyst: reads the supplier's e-mail + `ConcessionFit` -> `BehaviourRead` (`p_more`, `walk_risk`,
   what it wants). Numbers come from the fit; the analyst interprets words.
4. Playbook: mine `runs/digest-practice.md` per persona (what moved it, what backfired) into prompts.
5. Offline prompt tests: replay recorded threads from `runs/sessions/` into the roles (no quota):
   valid JSON, latency, no invented numbers or IDs.
6. Sunday: pitch slides (one replay walked through).

### Amine - deterministic tools (`agents/buyer/tools/`), in this order
1. `bands.py`: floor (should-cost, country factor), fair (index pass-through), ceiling (alternative,
   fallback, rival quotes, C_open), target (min C incl. relationship term in stabilise/secure_supply).
2. `offers.py`: opening anchor near floor, Boulware concession toward ceiling over the round limit,
   2-3 sheets of equal C for us that differ in what the supplier gets (price vs payment days vs
   effective date vs volume/length); index clause only when it lowers C.
3. `claims.py`: claim sentences pre-checked against `facts.json` (index changes of the part's own cost
   drivers, rival quotes, PO history, scorecards, notice periods).
4. `concession.py`: fit of the supplier's price path -> `ConcessionFit`.
5. `dossier.py`: persona, levers, history prior, second source, how late we pay, evidence lines.
6. `tools/__init__.py`: `DataTools(package)` implementing the `Tools` protocol.

## Timeline

| When | Everyone | Jeddou | Saed | Amine |
|---|---|---|---|---|
| Sat 16:00-16:45 | agree `contracts.py`, create branches, read `CLAUDE.md` | | | |
| -> **19:00 checkpoint 1** | skeleton plays D01 with `--agent buyer` | skeleton, cli, fallback | writer + strategist v0 | bands + offers v0 |
| -> **22:00 checkpoint 2** | merge, sweep v1 (10 sessions) vs 54.4 | guard, accept policy | analyst v0, prompt tests | claims, relationship-aware target |
| 22:00-02:00 | fix the 3 weakest cases from replays | RFQ policy, timing | playbook from replays | concession fit, dossier |
| ~01:00 | sweep v2 (10 sessions) | | | |
| Night | sleep in shifts; at most one unattended sweep | | | |
| Sun 08:00 | Sunday release: `uv run arena data`, rebuild dossiers | | | release ingestion |
| 08:00-10:30 | hardening, sweep v3 | rehearsal on `arena-sim --final-rehearsal` | slides | edge cases (no should-cost, prices per 100/1000) |
| 10:30-11:30 | pre-flight on the final machine (skill `final-runbook`) | | | |
| 12:00-13:00 | freeze: merge, README, commit hash, submit | | | |
| 13:15 | start `arena run --set final --agent buyer --parallel 10` | | | |

## Practice budget (189 sessions)

| Use | Sessions |
|---|---|
| Full sweeps (v1, v2, v3, pre-final), 10 each | 40 |
| Targeted single cases: tools / roles / orchestrator, ~25 each | 75 |
| Sunday pre-flight + spare sweep | 20 |
| Reserve | ~54 |

Sandbox (D01, D02) and `arena-sim` are free: use them for anything that is not a measurement.

## If time runs short, keep in this order

1. C-aware anchor and concession (offers) + safe accept policy.
2. Relationship-aware target in stabilise/secure_supply + index clause evaluation.
3. Pre-verified claims on the supplier's own cost drivers.
4. Behaviour read + concession fit for "is it still open".
5. Dossier personas and playbook.
