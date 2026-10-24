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
