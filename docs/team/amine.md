# Amine - deterministic tools

Branch `feat/tools` · owns `starter-kit/arena_kit/agents/buyer/tools/` · skills: `data-package`, `replay-analysis`

## Now

- A1 done (v1); next: A2 `offers.py` - anchor from `band.target_terms`, Boulware beta ~0.5 + reciprocity, equal-C sheets.

## Tasks

| ID | Task | Status | Due |
|---|---|---|---|
| A1 | `bands.py`: floor (should-cost x country factor), fair (index pass-through), ceiling (alternative, fallback, rival quotes, C_open), target = min C incl. relationship term and index clause | todo | Sat 19:00 |
| A2 | `offers.py`: anchor near floor, Boulware concession, 2-3 equal-C sheets trading price vs payment days / effective date / volume / lead time / Incoterm (D02: price-only steps of 0.02-0.14 EUR stalled the supplier) | todo | v0 19:00, v1 22:00 |
| A3 | `claims.py`: claim sentences pre-checked against facts.json (supplier's own cost drivers first) | todo | Sat 22:00 |
| A4 | `concession.py`: reservation estimate from the price path + prior from floor / history | todo | Sun 02:00 |
| A5 | `dossier.py`: numbers (intel, invoices, scorecards, sourcing, history) + evidence lines with IDs for the Profiler | todo | Sun 02:00 |
| A6 | Sunday release: new IDs reach dossiers and claims | todo | Sun 08:30 |
| A7 | Edge cases: no should-cost row, price per 100/1000, no bid, must-haves on non-price fields | todo | Sun 10:30 |

## Asks to others

- @jeddou (18:40): `PriceBand.target_terms` (the whole target package) is new in contracts.py - the accept policy can compare a standing sheet's C against `band.c_target`.

## Log

- 16:30 - scaffold in place (placeholders in every tools module, tests in `tests/test_buyer.py`) - commit on `feat/negotiation-agent`
- 18:00 - A1 estimates.py: full cost (own plant / scaled / price fallback), cost of money (skonto, credit, equity), fair price, index change; 11 tests on real data - b281662
- 18:40 - A1 bands.py v1: target = cheapest package in C (relationship incl.) the supplier can accept, searched over all options; ceiling from opening/alternative; `python -m arena_kit.agents.buyer.tools.calibrate` compares with arena C_best - target/best 1.01-1.02 where should-cost models the supplier's plant (P01-P03), 0.75-0.97 on price-fallback estimates (P04, P08, P11, P18: still too aggressive)
- 18:00 - research + plan for the hidden quantities: docs/team/amine-estimation-plan.md

## Runs

| When | Commit | Cases | Sessions | Mean | Notes |
|---|---|---|---|---|---|
