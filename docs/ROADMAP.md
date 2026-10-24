# Team Mutun - buyer agent: architecture and roadmap

Deadline: **Sun 13:00 code freeze**, Live Final 13:30 (5 waves x 8 min, up to 10 cases in parallel).
Baseline: naive agent scored 0.597 on `case:P01`.

## What the score actually rewards

- Score is measured in **total cost C**, not unit price: `(C_open - C_deal) / (C_open - C_best)`.
  Payment days, `effective_date` (delaying an increase), contract length (uncovered need is costly),
  Incoterm, PPM/chargeback and index clauses all count.
- `stabilise` / `secure_supply` cases **charge for squeezing the supplier** below its sustainable margin.
  The cheapest price is not the best deal there; the optimum is `argmin(price + relationship cost)`.
- Suppliers have a **hard cost floor**, a boss approval ladder, and give nothing without a move from us.
- **Contradicted claims cost trust.** Only send claims already checked against `facts.json`.
- Hidden cases use **suppliers and parts that practice never shows**: nothing supplier-specific may be
  hardcoded. Everything must come from the data package (+ the Sunday release).

## Data reality check (why not a classic time-series model)

| Data | Size | Usable for |
|---|---|---|
| `negotiation_history` | 225 renewals, ~3 per supplier, annual | a **prior** on how far a supplier gives (asked - settled averages ~2.5 pts); far too short to forecast per supplier |
| `index_values` | 33 series, 2021-01 to 2026-09 monthly | **cost pass-through**: fair price = last price x weighted index change (`cost_drivers`); verified claims |
| `should_cost` | 28 parts, 22 modelled on the supplier's own plant (+-5 %) | the supplier's **cost floor** (and its margin, for the relationship term) |
| `price_conditions`, `quotes`, `po_lines` | price history, rival quotes | ceiling, anchors, citable competition |
| `supplier_intel`, `invoices`, `news`, `documents` | 46 suppliers | supplier **persona**: cash-short, full order book, monopolist, reference-customer |

The case file already gives the expected index move (`buyer.index_paths`), so forecasting indices adds
little. The time series that matters is **the supplier's concession sequence inside a negotiation**:
fit it live to estimate where it will stop.

## Architecture

```
          OFFLINE (before any session, precomputed + cached)
 data package + Sunday release
   -> dossier.py   supplier persona, levers, history prior, BATNA, cash position, notes
   -> bands.py     per part/supplier: floor, fair (index pass-through), ceiling - in price AND C
   -> claims.py    ready-made sentences "figure + ID", each pre-verified against facts.json

          ONLINE (per round, < 60 s budget, target < 25 s)
 thread events ─> opponent.py   concession-curve fit -> reservation estimate R, P(more), signals
              ─> strategist.py  LLM (JSON): tactic, levers to trade, which claims, questions
              ─> offers.py      code: 1-3 term sheets on the iso-C frontier, concession schedule
              ─> writer.py      LLM: the e-mail around the chosen claims + term sheets
              ─> guard          code: snap terms, drop unknown IDs, no leaks, accept/walk rules
```

`agent.py` implements `decide(case, threads)` and is registered as `--agent buyer` in `cli.py`, so
`arena run --set final --agent buyer --parallel 10` works unchanged with the kit's wave runner.

### Components

1. **Dossier (offline, LLM allowed to be slow).** For every one of the 46 detailed suppliers:
   owner type, equity ratio, credit-index trend, how late we pay them (`invoices`), scorecard trend,
   open quality issues, second source available (`sourcing`), contract notice clauses, history prior
   (mean asked - settled), and an LLM summary of its notes/letters/news: what it wants (cash, volume,
   long contract, price list untouchable, reference customer). Cached as JSON; rebuilt Sun 08:00.
2. **Price bands (deterministic).**
   - `floor` = should-cost full cost (x country factor from `company.md` if the row is DE-based), else
     last price rolled forward by index pass-through minus a typical margin. Never offer below
     `floor` x ~1.0: it wastes rounds and trust.
   - `fair` = last agreed price x (1 + sum(weight_i x 12-month change of series_i)), the rest of the
     cost held constant. This is the anchor we argue for.
   - `ceiling` (our walk-away) = min(C of the alternative, C at fallback price, best rival quote, C_open).
   - In `stabilise`/`secure_supply`: `target` = price minimising C including `relationship_cost`
     with `supplier = {"full_cost": floor, ...}`.
3. **Claims (deterministic).** Generate candidate claims per case: 12-month index changes for each cost
   driver (both directions - only use those that help us), rival quotes, PO price history, scorecard
   OTD/PPM, should-cost, notice-period vs contract. Compute the figure the same way the arena checks it;
   max 3 checked claims per message, one per sentence, never reuse an ID (duplicate).
4. **Opponent model (live).** Per thread, from the supplier's price/C sequence:
   - fit `p_t = R + (p_0 - R) * exp(-k t)` with a prior on `R` from `floor`/history; shrinking
     concession ratios -> near its limit.
   - signals: `final_offer`, "needs approval", "that remains", reply delay, validity days, claim
     reactions, which of our 2-3 term sheets it answered (reveals what it values).
   - output: `R_hat`, `p_more` (probability next round improves), `walk_risk`.
5. **Offer engine (deterministic).** Time-dependent (Boulware) concession from `target` toward
   `ceiling` over the round limit; generate 2-3 sheets with **equal C for us but different value for
   the supplier** (price vs payment days vs effective date vs volume/contract length) - its answer tells
   us its preferences (cash-short supplier -> trade short payment for price).
6. **Strategist + writer (LLM).** Strategist returns structured JSON (tactic, lever, claims to use,
   question to ask, e.g. cost breakdown). Writer produces <= 120 words, polite, factual. Neither does
   arithmetic. Prompts include the dossier, bands, opponent state and allowed claim sentences.
7. **Closer / guard.** Accept when: must-haves hold, C <= ceiling, and (`agreed`, or `final_offer`
   with C within band, or `p_more` x expected gain < walk_risk x surplus, or out of rounds / time).
   Walk away when nothing beats the alternative. Read the quote PDF standard terms before accepting.
8. **Learning loop.** `scripts/harvest.py` pulls `/v1/sessions` + traces after every practice run into
   `runs/` (gitignored); an LLM pass distils the supplier's stated reasons into a playbook per persona
   (what moved it, what backfired). The playbook feeds the strategist prompt.

### Model choice and latency

10 parallel cases x 1-2 LLM calls per round inside 60 s: per-round calls on **claude-sonnet-5-5** (or
`claude-opus-5-5` at low effort if measured latency allows), hard timeout 40 s, deterministic
fallback move from `offers.py` if the LLM is late. Offline dossier/playbook work on
`claude-opus-5-5`. Watch the account's rate limit at 10x concurrency.

## Roadmap (Sat ~15:00 -> Sun 13:00)

**Practice budget: 199 sessions until the final.** Sessions age out after 24 h, so nothing used today
frees up before the freeze. Use the **sandbox (`--set sandbox`, D01/D02, free)** and `arena-sim` for
plumbing and style; spend hosted practice only on agent versions worth measuring.

| When | Goal | Deliverable | Quota |
|---|---|---|---|
| Sat 15-16 | Baseline + plumbing | `ANTHROPIC_API_KEY` set; `--agent llm` on all 10 hosted cases; harvester saves results and traces; score table per case | 10 |
| Sat 16-19 | Offline intelligence | `dossier.py`, `bands.py`, `claims.py` + tests (pure functions over SQLite); dossiers cached for all 46 suppliers | 0 |
| Sat 19-22 | Agent v1 | `offers.py`, `agent.py` (strategist + writer + guard), registered as `--agent buyer`; sandbox runs until clean | ~20 |
| Sat 22-02 | Opponent model + iterate | `opponent.py`; full sweeps, read every replay, fix the weakest case type each loop | ~60 |
| Overnight | Unattended sweep | one `arena run --set practice --agent buyer --parallel 10` per agent version, logs to file | ~20 |
| Sun 08:00 | Sunday release | `uv run arena data`; rebuild dossiers; check new IDs are picked up as claims | 0 |
| Sun 08-10:30 | Harden | timeouts, fallbacks, 10x parallel on `arena-sim --final-rehearsal`; latency report; RFQ multi-thread play | ~40 |
| Sun 10:30-11:30 | Pre-flight | final machine + network, one full practice sweep, power on, no sleep (`caffeinate`) | ~10 |
| Sun 11:30-13:00 | Freeze | README (run command, env vars without values), approach description, commit hash; slides skeleton | reserve ~30 |
| Sun 13:15 | Start runner | `uv run arena run --set final --agent buyer --parallel 10 2>&1 \| tee final.log` | - |

### Parallel workstreams (for 3-4 people)

- **A - Data/intel:** dossier, bands, claims, Sunday-release ingestion.
- **B - Agent core:** offers, opponent model, agent orchestration, guard, closing rules.
- **C - LLM + learning:** strategist/writer prompts, replay harvester, persona playbook.
- **D - Ops + pitch:** runs, quota accounting, latency, final rehearsal, README, slides (pick one
  replay to walk the jury through).

## Priorities if time runs short

1. C-aware term sheets (payment days, effective date, full-horizon coverage) + safe closer.
2. Pre-verified claims (never contradicted) + floor/ceiling bands.
3. Dossier-driven tactic choice (what this supplier wants).
4. Live concession-curve opponent model.
5. Playbook learning from replays.
