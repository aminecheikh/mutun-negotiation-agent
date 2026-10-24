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

- 2026-10-24 - Jeddou / Codex review (user-authorised cross-layer improvements) - Keep the existing
  deterministic orchestrator and shared contracts. Reconcile uncertain moves from our own session/thread
  history; only authoritative ended state terminates a case. Publish replies before clearing in-flight.
  At the last-call cap only, prefer the best idle acceptable offer over a cheaper unavailable one.
  Feed Sunday release evidence into dossiers and prevent old cached profiles overriding it; cancel
  expired queued role calls. Small changes in kit data/resume and tools/roles are covered by regression
  tests; implementation and limits are recorded in `docs/orchestrator-review.md`.
- 2026-10-24 - Jeddou / Codex integration - Answer Amine's SDK retry request by configuring the live
  buyer with zero SDK retries and a 12-second provider timeout (below the 15-second role fallback).
  Preserve generic/offline defaults; retain outer role deadlines and cancellation of queued work.
  Research proposals that alter negotiation policy remain experiments pending controlled hosted evidence.
- 2026-10-24 - Jeddou / Codex follow-up (user-authorised integration) - Apply the existing writer text
  checks to outgoing subjects as well as body checks, including keep-alives; keep all claim IDs in the
  checked body. Align the writer prompt with that boundary. Reserve citations only after the final body
  fallback so removed evidence remains usable. No shared contracts changed.

- 2026-10-24 - Jeddou / Codex negotiation review (user-authorised cross-layer improvements) - Merge Saed's
  `5e44a84`; preserve one thread per role call and case-level arbitration. Normalise cost evidence by price
  unit, validate cost shares, refresh stated drivers, and compare concessions only on unchanged terms.
  Add `CaseContext.supplier_estimates: dict[str, dict | None]` with an empty default, after existing fields.
  Tools populate it; the live pipeline snapshots the arbiter's estimates; the strategist includes relationship
  cost in acceptance/ranking and model input. Existing constructors remain compatible. Remove practice-fitted
  numerical expectations from the strategist prompt. Evidence and limits: `docs/orchestrator-review.md`.
