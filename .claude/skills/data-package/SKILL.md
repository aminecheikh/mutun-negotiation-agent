---
name: data-package
description: Query the Hollerau procurement data package (SQLite, facts.json, correspondence) correctly - tables, citable IDs, how the arena checks a claim, and the queries behind price bands, claims and supplier dossiers. Use whenever writing or debugging code in tools/ or answering a question from the data.
---

# The data package

Repo copy: `data/` (`world.sqlite`, `facts.json`, `correspondence/`, `contracts/`). Runtime copy:
`starter-kit/data/package` via `arena_kit.data.DataPackage` (`query`, `lookup`, `document`). From Sun 08:00
also `release-sunday` (merged by `DataPackage`). As of date: 2026-09-30.

## Tables that matter for the agent

| Need | Table / file | Notes |
|---|---|---|
| Supplier cost floor | `should_cost` | `unit_cost` = full cost, no margin. Rows with `supplier_id` model that supplier's plant (+-5 %); others are a DE cost base (+-5-15 %): scale AT/CH 1.0-1.1, CZ/PL/HU 0.8-0.85, TR 0.75, CN 0.7 |
| Fair price | `price_conditions` (last price) x `cost_drivers` (series, weight) x `index_values` | pass-through: `last * (1 + sum(w_i * change_i))` |
| History prior | `negotiation_history` | ~3 renewals per supplier; `asked_pct - settled_pct` ~2.5 pts on average; note "accepted below full cost" |
| Competition / BATNA | `sourcing` (qualified 2nd source), `quotes`, `rfqs`, `po_lines` | rival quotes are citable in RFQs |
| Persona | `supplier_intel` (owner, equity, credit index trend), `invoices` (how late we pay), `news`, `documents` + `correspondence/*.md`, `scorecards` | notes and letters hold the real motives; some are noise |
| Notice periods | `contract_clauses`, `contracts/*.md`, letter `doc:` with `notice_days` | |

Prices are per `price_unit` (1, 100, 1000, kg, m); cases negotiate in the same unit.

## How the arena checks a claim (brief/api.md "What counts")

- Cite the ID in the body (or `refs`) and write the figure **in the same sentence**. Max 3 checked claims
  per message; each ID counts once per case (`duplicate`).
- `idx`: two months of one series, <= 12 months apart, ending within the last 12 months of the data;
  figure = `(later / earlier - 1) * 100` as %, tolerance +-0.5 pt.
- `quote` / `offer`: its price, same part, EUR. `sc` (and `qn`): mean OTD % or mean ppm.
  `po`: 2-8 lines, same part. `fc` change %, `cost` EUR, `news` about this supplier,
  letter `doc` + `ctr`: notice days.
- Tolerance otherwise +-0.5 % (min 0.01). Unknown ID or wrong figure = `contradicted` (costs trust).
- Always compute the figure from `facts.json` (what the arena checks), not from a CSV you re-derived.

## Rules for tools code

- Read-only SQL via `DataPackage.query` (SELECT only); parameterised queries, never string-built SQL
  with case data.
- Pure functions: `(case, package) -> value`. Test them against real rows (e.g. part:310010 /
  sup:70110 from case P01).
- Nothing supplier-specific hardcoded: final cases use suppliers practice never shows.
