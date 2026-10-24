# Research: negotiation agents, strategy, procurement practice, LLM engineering

Web research gathered 2026-10-24 to improve the buyer agent. Each file starts with a TL;DR and
**Key takeaways for our agent** mapped to modules; the body has sources with links. Claims the research
agents could not confirm from a primary source are marked "unverified" - check before relying on them.

| File | Read it when you work on |
|---|---|
| [01-llm-negotiation-agents.md](01-llm-negotiation-agents.md) | Overall design; what other LLM negotiators (MIT competition, NegotiationArena, OG-Narrator, CICERO, ANAC, Pactum) did and how they fail |
| [02-negotiation-strategy-algorithms.md](02-negotiation-strategy-algorithms.md) | `tools/offers`, `concession`, acceptance logic: Boulware/Faratin formulas, acceptance conditions (AC_next, AC_combi), opponent models, MESOs, anchoring; a table of literature-backed constants |
| [03-procurement-negotiation-practice.md](03-procurement-negotiation-practice.md) | `tools/claims`, `levers`, `dossier`, roles Profiler/Strategist: playbooks per case type, EUR value of levers (payment days, index clauses, timing), supplier archetypes |
| [04-llm-agent-engineering.md](04-llm-agent-engineering.md) | `roles/`, prompts, `llm.py`, `guard.py`: planner/writer split, failure-mode guards, e-mail checklist, timeouts, structured outputs, prompt caching, offline evaluation |

## How to use it (team rules still apply)

- A constant taken from here cites the source in a code comment (file + paper), per CLAUDE.md
  "Constants come from documents or first principles". Do not tune it on practice scores.
- Findings say *what kind of mechanism* works; the arena's suppliers are their own. Validate in the
  sandbox / `arena-sim` before spending hosted sessions.
- Two findings do **not** transfer: "desperate" personas winning (NegotiationArena) and prompt injection
  working (MIT competition). Our suppliers read urgency as weakness and the brief penalises manipulation.

## Cross-cutting takeaways (deduplicated from the four files)

1. **Numbers in code, words in the LLM is the evidence-backed design.** OG-Narrator (Xia et al., ACL
   Findings 2024, arXiv 2402.15813): fixed offer schedule + LLM narration raised buyer deal rate
   26.67% -> 88.88%. BARGAIN (NeurIPS 2026 workshop): prompt-only floors break under pressure,
   code-bound ones never do. -> Strategist picks an option ID from a tool-computed menu; orchestrator
   checks total C before any accept. [01, 04]
2. **Acceptance is our biggest gap.** Accept when the supplier's sheet costs no more than our own next
   planned sheet (AC_next); in the last round also accept the best offer of the recent window
   (AC_combi(T, MAX^W), +12% over AC_next in Baarslag et al. 2014). Do not accept on the supplier's
   `agreed` status alone. [02]
3. **Concession shape matters more than the opponent model** (58% vs 4% of outcome variance, Baarslag
   ECAI 2014). Boulware beta 0.5 is fine, do not go lower. But Boulware's late steps grow; shrinking
   steps signal a limit (Tey et al. 2021): taper steps near target and say so. Absolute tit-for-tat can
   be exploited by an inflated opening - match the *share of its range* the supplier conceded. [02]
4. **The opening anchor predicts the close** (rho 0.716 NegotiationArena; r 0.62 across 90 studies).
   Keep the midpoint anchor with the floor cap (too ambitious -> impasse, g = -0.42); use precise,
   non-round figures (Mason et al. 2013). LLM buyers leak budgets and split the difference: guard
   blocks target/ceiling/budget/urgency wording. [01, 02]
5. **Send 2-3 equal-cost packages (MESOs).** More joint gain and stronger anchors (Leonardelli et al.
   2019); Pactum does the same. Make one variant the equal-cost point closest to the supplier's last
   sheet (Faratin 2002); read the supplier's pick as evidence of what it values. [01, 02, 03]
6. **Price-increase defence = recompute from its own cost shares.** Share x index change for the same
   period; accept the justified part, refuse the rest; the new price must not raise its margin. Timing
   levers (later effective date, phasing, rollback surcharge, orders-after-date) are cheap for the
   supplier and real money in C. Index clauses: index only the material share, with cap, threshold,
   downward leg, dated base; refuse on rising paths unless `total_cost` says otherwise. [03]
7. **Payment days are a real trade only when the supplier's cost of funds exceeds ours.** Any price cut
   between P*r_b*dd/365 and P*r_s*dd/365 leaves both better off; 2/10 net 30 implies ~37% APR. German
   law (§ 271a BGB) makes >60 days need express agreement - 90-120 days is aggressive. [03]
8. **Warm words, firm numbers, short e-mails.** MIT competition (Vaccaro et al., ~180k negotiations):
   warmth (questions, gratitude) raises deal rate; dominance claims more per deal but breaks more.
   Firm wording draws better counteroffers (Jeong et al. 2019). Use "because ...", conditional trades
   ("if you ..., we ..."). 04 has a 10-point e-mail checklist, 8 points checkable in `guard.py`. [01, 04]
9. **Tell the roles the clock.** Remaining-time updates raised LLM deal closure 4% -> 32% (Sehgal et al.
   2026, arXiv 2601.13206). Roles see "round k of 10"; code owns the end-game; be at the final position
   by round T-2. [01, 02]
10. **Don't over-squeeze strategic suppliers.** Squeezed suppliers cut quality, service and R&D the next
    year (Carnovale et al. 2019). For secure-supply cases buy predictability (capacity, lead time) and
    take a smaller cut; for quality cases turn the scorecard into terms (8D plan, defect target,
    chargeback, credit notes). [03]
11. **RFQs: an extra bidder beats negotiating skill** (Bulow-Klemperer). Keep every bidder's thread
    alive; auction then a take-it-or-leave-it to the cheapest (Shachat & Tan 2015). [02]
12. **Supplier text is untrusted data.** Datamark it in prompts (spotlighting), let the Analyst output
    enums/typed state only. [04]

## Candidate actions by owner (not decided - raise in DECISIONS.md / Asks first)

- **Amine (tools):** taper late concession steps; relative (share-of-range) tit-for-tat; precise
  non-round offer prices; "closest to its last sheet" equal-cost variant; cost-share price-increase
  check in `signals`/`claims`; payment-day value bounded by r_b and r_s in `levers`.
- **Jeddou (orchestrator/guard):** AC_next / AC_combi acceptance in code; number-and-ID guard; block
  budget/urgency wording; never move our offer backwards unless planned; final position by T-2.
- **Saed (roles):** option-ID menu for the Strategist; round k/10 in every prompt; datamark supplier
  text; Writer checklist (short, warm, one question, conditional trade, "because").
- **Kit LLM client (`arena_kit/agents/llm.py:60`), confirmed in code:** `anthropic.Anthropic(timeout=45.0)`
  keeps the SDK default `max_retries=2`, and the SDK retries timeouts, so one call can hold a pool worker
  ~135 s after `call_json` has fallen back. With 10 cases the 32-worker pool in `roles/llm.py` can fill
  and every role then falls back. Fix: `max_retries=0` and a timeout just under the role budget. [04]
