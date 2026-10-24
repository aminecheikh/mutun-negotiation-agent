# Amine - deterministic tools

Branch `feat/tools` · owns `starter-kit/arena_kit/agents/buyer/tools/` · skills: `data-package`, `replay-analysis`

## Now

- Not started: A1 next.

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

## Log

- 16:30 - scaffold in place (placeholders in every tools module, tests in `tests/test_buyer.py`) - commit on `feat/negotiation-agent`

## Runs

| When | Commit | Cases | Sessions | Mean | Notes |
|---|---|---|---|---|---|
