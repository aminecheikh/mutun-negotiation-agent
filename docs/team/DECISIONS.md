# Decisions

Append only, newest last. One entry per decision that binds more than one person: `date time - who -
decision - why`. Changing `contracts.py` always needs an entry here.

- 2026-10-24 16:30 - team - The orchestrator is plain code, not an LLM agent; LLM roles run at fixed points
  of each round. - 60 s per move with 10 parallel cases; debuggable; explainable to the jury.
- 2026-10-24 16:30 - team - Numbers only from `tools/`; roles never compute or invent a price, figure or ID. -
  Contradicted claims cost trust; the baseline's LLM-chosen anchors were too generous.
- 2026-10-24 16:30 - team - Roles: Profiler (once per case), Analyst -> Strategist -> Writer (per round). No LLM
  critic (the guard is deterministic), no separate RFQ or closer agent (orchestrator code). - Every extra LLM
  call per round costs ~10 s of the 60 s budget.
- 2026-10-24 16:30 - team - Analyst and Strategist stay separate types but may run as one LLM call; decided by
  measured latency (S6 / J4).
- 2026-10-24 16:30 - team - `Tools.prepare(case, threads)` gets the opening threads: bands need the opening
  quotes and the quote PDFs' standard terms.
- 2026-10-24 18:40 - Amine - `PriceBand` gains `target_terms: dict` (default empty): the whole target package, not
  only its price - the offer engine and the accept policy need payment days, dates and clauses, not just a number.
- 2026-10-24 19:10 - team (Amine) - Generalise, don't overfit: logic and constants come from mechanisms and
  documents, never from practice outcomes; estimates are priors the live negotiation corrects; every module is
  tested for properties on all 19 case files and mutated copies. Rules in CLAUDE.md. - The final uses suppliers,
  parts and situations practice never shows; 10 scored practice cases are too few to fit anything.
- 2026-10-24 20:10 - Amine - `ConcessionFit` gains `p_room: float | None = None`: probability the supplier can still go
  >= 1 % below its last price (Bayesian fit of its own price path, `tools/concession.py`). For the analyst (p_more)
  and the accept/walk policy; None until it has moved once.
- 2026-10-24 21:00 - Amine - `CaseContext.trades: dict[str, tuple[Trade, ...]]` (default empty) and the `Trade` type: per
  supplier, each term change against its quote that saves us money, in EUR and as a price per unit (lead time,
  ppm/chargeback, payment, Incoterm, volume, contract length, dates, clauses). For the strategist's explicit trades -
  the tools no longer impose lead time or ppm, they price them.
- 2026-10-24 19:00 - team (Amine, user-approved) - Kit `agents/llm.py`: `llm_from_env(timeout_s=45.0)` builds the
  Anthropic client with `max_retries=0`; `buyer_factory` passes the larger role bound (17 s). - The SDK retried
  timeouts twice, so one late call held a worker of the 32-thread LLM pool ~135 s after the role fell back; with
  10 cases the pool could fill and every role would fall back. The kit's own `LLMAgent` keeps its 45 s.
- 2026-10-24 19:15 - team (Amine, user-approved) - `CaseContext.fits: dict[str, ConcessionFit]` (default empty): the
  pipeline puts this round's fit in before the strategist runs; the strategist's thread view shows it (`concession`)
  and `ctx.trades` (`trades`, biggest 6). - Its prompt already promised "the concession fit of its prices", but the
  view never had it, and no role read the priced trades (asked by Amine 21:00).
