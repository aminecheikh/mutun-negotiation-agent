---
name: replay-analysis
description: Diagnose why a negotiation scored low from its harvested session (trace, supplier reasons, cost breakdown, back office) and turn it into concrete fixes for tools, roles or orchestrator. Use after a practice run, when a score drops, or when asked why a case went badly.
---

# Reading replays

Input: `starter-kit/runs/sessions/<case>_<sid>.json` (keys `session`, `backoffice`) or
`runs/digest-<set>.md`. Run `scripts/harvest.py --digest` first if they are missing.

## Checklist per case

1. **Score arithmetic.** `c_open`, `c_deal`, `c_best`, `c_alt`. Where is the gap? Compare
   `result.cost_breakdown` items: `relationship`, `index_clause` + `index_risk`, `uncovered_need` +
   `fallback_risk`, `payment_terms`, `freight`, `late_delivery` often matter more than price.
2. **Anchor.** Our first term sheets vs the supplier's opening. An `agreed` in round 1-2 means our offer
   was above its limit: money left on the table.
3. **Concession path.** The supplier's prices per round: still moving at the end (we closed too early),
   or flat with `final_offer` (we hit its limit)? Note how many of the round limit we used.
4. **Claims.** Each `claim_results` entry: `contradicted` is a bug in claims; `irrelevant` means wrong
   supplier/series/window; `verified` + "does not change our calculation" means the argument was not
   about its real cost drivers.
5. **Reasons and back office.** The supplier's `reasons` and the `backoffice` log say what moved it
   (approval needed, cash, capacity, relationship). Hidden figures show as `…`.
6. **Which terms it accepted vs refused** across our 2-3 term sheets: that reveals what it values.

## Output

Per case: one line of verdict, the evidence (round, quote), and the fix assigned to its layer:
- `tools/` - wrong band, anchor, offer engine, claim or C evaluation
- `roles/` - wrong tactic, tone, leaked urgency, missed signal, bad question
- `agent.py` - closed too early/late, wrong supplier focus in an RFQ, timeout

End with patterns seen in two or more cases; propose adding them to `CLAUDE.md` lessons.
