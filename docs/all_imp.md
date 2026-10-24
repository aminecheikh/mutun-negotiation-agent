# Agent v2: every improvement, with the evidence behind it

Written for: the Mutun team. Kept up to date after every change and every run. Branch `feat/Agents-v2` (PR #5).

How to read it: each entry says **what** changed, **why** (the data or research it rests on), **how it was tested**
offline (unit tests, the local arena simulator), and **what the hosted arena said** when a practice run followed.
Practice runs cost quota and are limited to 5 for the improvement work (count at the bottom), so every change is
studied offline first. Nothing is tuned on practice scores: constants come from the research or from how the
supplier bots demonstrably behave across all suppliers.

## 1. The v2 buyer: a team of agents (Sat 24 Oct, night)

**What.** A second, complete buyer next to v1, selected with `AGENTIC_STRUCTURE=1` (default; `0` = v1). Four
specialists in parallel, each with read-only tools over the whole data package (reader, deal economist,
counterpart analyst, market analyst), then a negotiator that decides accept / counter / walk_away and writes the
e-mail. Hard rules in code: accept the moment the supplier accepts our offer, never a price above its offer, every
counter cheaper for us than its sheet, never take back an offer, signable sheets only, verified arguments
verbatim, each data ID once per case, no other supplier named. Own runner (a worker per thread, a coordinator for
accept / walk / the wave cap). New prompts (house rules, a negotiation playbook from the research, one per agent).
Design: `docs/agents-v2.md`.

**Tests.** 126 v2 tests: rules and guard properties on all 19 practice case files, every tool on every case with
the real data, the tool loop with a scripted model, fast paths, fallbacks, prompts and schemas. Simulator on a real
clock: every case ends in a deal, no timeouts, nothing sent after an agreement, a two-wave final rehearsal ends
before its cap.

**Review.** An independent reviewer reproduced 8 bugs (a decide/send loop on a repeated 409, the confirm path
ignoring the round limit and the counter rules, a refused accept ending the case locally, shifting argument tokens,
a counter without a sheet, caps vanishing when the latest e-mail had no sheet, gaps in the e-mail checks). All
fixed and pinned in `tests/test_v2_review.py`.

## 2. Every tool checked against the truth (Sat, late)

**What.** `tests/test_v2_tools_truth.py`: 90 checks compare each tool's answer with an independent source (a
separate SQLite connection, `facts.json` - the values the arena checks claims against -, the published cost
function, the documents' own text) on all 19 cases and their suppliers.

**Fixed on the way.** The 12-month index change was "the 13th row back" (right only because the data has no
gaps) - now calendar months. Price arguments said "per unit" for parts priced per 100 or 1,000 pieces - now the
data's real unit (the arena's check is unchanged; the sentence is now true for the reader). Unknown values stay
explicit nulls.

## 3. Memory of the whole negotiation, and the model in/out log (Sat, late)

**What.** Every agent reads every e-mail of every round, plus a negotiation log: each earlier round's decision
and reasoning, the packages with their C, the data cited, how we read the supplier, both price paths with step
sizes. Prompts: keep one coherent strategy, build on what worked, close open questions, never repeat an offer or
an argument. Every model call leaves a full JSON record (`BUYER_LLM_LOG_DIR`, default `runs/llm`): input, every
tool call with arguments and result, raw reply, parsed answer, tokens, outcome.

**Tests.** `tests/test_v2_memory.py`, `tests/test_v2_llm_log.py` (drive three rounds, inspect what each agent
received; a broken log folder never costs a move).

**Hosted arena, run A (Sat 23:07, 3 cases - a teammate's sweep held the other 7 session slots):** P01 0.706,
P02 0.345, P03 0.868. Found: the counterpart agent took ~19 s against a 20 s window in round 1 (all three timed
out); fixed by giving the profile and market agents 25 s.

**Hosted arena, run B (Sat 23:26, all 10 cases): mean 59.9.** P01 .647, P02 .352, P03 .864, P04 .766, P05 .346,
P07 .710, P08 .536, P10 .647, P11 .710, P18 .411. Decisions took 36 s median, 42 s max (limit 60); the gatherer
(`scripts/harvest.py`) and the in/out log worked (182 records). Problems: the deal agent timed out in 18 of 42
calls and the counterpart in 14 of 30; P01 closed in round 3 at 134.85 while the supplier was still conceding
(best ever on P01: 0.906 at 133.5 after 5 rounds).

## 4. The data study and the improvements from it (Sun 25 Oct, early)

Studied: 94 harvested practice sessions (every supplier's moves and its reasons in words), v2's own in/out
records, `docs/research/02` (negotiation strategy as algorithms) and `03` (procurement practice), v1's latest
changes (merged into v2: credible first offers, no repeated price on a standstill, steps never grow).

**Findings (all suppliers, all sessions - not one case):**
- First counters 4-8 % under the quote closed best (mean 0.68); counters more than 15 % under were answered with
  a final offer in 30 of 30 sessions (mean 0.58); counters less than 4 % under did worst (0.51).
- Which verified arguments changed a supplier's calculation: index changes 15/15, should-cost 14/14, notice
  periods 9/10, our volume changes 22/29, delivery records 20/61, **our own order history 0/79** ("it holds, but it
  does not change our calculation"). A cost driver that rose drew "your figures confirm our case" 17 times.
- Suppliers asked "which volume can you commit to?" 87 times and offered "a binding higher volume ... can improve
  the price" 77 times; v2 could not state our volume (the e-mail guard removed figures not in a sheet).
- Deals closed on "agreed" scored lowest on average (0.51 vs 0.62): an agreement means our last package was inside
  the supplier's acceptance zone - we gave more than needed. In P01 v2 closed the gap itself (0.25 on 135).
- In P03 v2 paid early (14 days, +13k in C) for a 0.7 % price cut, while the best session took 90 days (-26k).

**Changes (commit `3ec8249`):**
1. **Concession discipline in code** (`guard.step_limit`): our step never exceeds the supplier's last step in C
   (relative tit-for-tat, research 02 §2 and §7), never more than half the remaining gap, a small visible step
   (0.3 % of its C) when it stood still. **AC_next** (research 02 §3): if what the negotiator wants to offer next is
   no better for us than its standing sheet, we accept that sheet now.
2. **Arguments ranked by effect** (`facts.KIND_EFFECT`): the replay frequencies above as a prior on each kind; a
   rising driver ranks below a falling one. The market agent is told what disarms and what does not.
3. **Precomputed specialist inputs** (`agents.precomputed`): the deal, counterpart and market agents get their
   first tool results from code before they start (milliseconds), one tool turn left for follow-ups.
4. **Guidance in the decision frame**: round 1 - fair price, target, floor confidence and the replay finding on
   first counters; later rounds - the allowed step and "do not close the gap yourself"; every round - the levers
   worth the most with their direction, and that early payment is a cost the price cut must pay for.
5. **E-mails may state our enquiry's figures** (fixed terms, need per year, horizon), so volume questions get an
   answer.

**Tests.** `tests/test_v2_improve.py` (step cap, visible step, AC_next, ranking incl. rising drivers, precomputed
blocks, frame guidance, enquiry figures); full suite 826 passed; simulator runner tests pass with the step cap.

**Hosted arena, run 1 of 5:** running (Sun 25 Oct, ~00:30). Results below when done.

## Practice runs used for the improvement work (limit: 5)

| # | When | Build | Mean | Notes |
|---|---|---|---|---|
| 1 | Sun 00:30 | 3ec8249 | pending | first run with concession discipline, ranked arguments, precomputed inputs |
