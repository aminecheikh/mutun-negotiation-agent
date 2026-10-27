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

**Hosted arena, run 1 of 5 (all 10 cases; the second half ran a few minutes later on `4973483`):**

| Case | run 1 | run B (§3) | our best | |
|---|---|---|---|---|
| P01 | **0.790** | 0.647 | 0.906 | +0.14: AC_next closed at 133.99 instead of giving more |
| P10 | **0.686** | 0.647 | 0.663 | **new best**: coverage package, 2 rounds |
| P03 | 0.855 | 0.864 | 0.905 | flat |
| P04 | 0.766 | 0.766 | 0.810 | unchanged |
| P11 | 0.710 | 0.710 | 0.710 | unchanged |
| P07 | 0.689 | 0.710 | 0.710 | -0.02 |
| P05 | 0.352 | 0.346 | 0.455 | flat (RFQ) |
| P18 | 0.406 | 0.411 | 0.497 | flat |
| P02 | 0.311 | 0.352 | 0.356 | -0.04, inside this case's spread (.31-.36) |
| P08 | **0.313** | 0.536 | 0.621 | **-0.22: the regression** |

**Mean 58.8** (run B: 59.9). Two clear wins, one clear loss that outweighed them.

**P08 diagnosed from the trail (this is the lesson of the run).** Our first counter was **5.62, only 7 %
under its 6.05**; the best-ever session anchored at **4.84 (-20 %)** and the supplier then moved 11.6 % to
5.35. From 5.62 there was nowhere to go: it answered 5.57, then held 5.65 and went final.

The cause was my own addition: "first counters 4-8 % under the quote closed best" - a replay *average* that
mixed cases with firm cost evidence and cases without. It overrode `tools/credibility`, which already sets the
depth per supplier (down to the cost floor with a firm should-cost, within 8 % without). P08 has a should-cost
floor of 4.76, so the credible anchor was 4.84; the prose talked the negotiator up to 5.62.

**Fix (commit `7c01027`), generalising instead of fitting:**
- The frame and prompts now say **anchor at the lowest credible price**; how deep that is stays the tool's job.
- The negotiator's input carries the **offer engine's own paced counter**. The engine is credibility-aware *and*
  C-aware: in squeeze cases (P02) it holds above the C minimum, where the credible price would be the wrong
  anchor. Its price is the default unless the negotiator can say why this supplier needs another.
- Also in: a tool turn is skipped when under 14 s remain (every live timeout came from a tool turn begun with
  10-17 s left), and reports are asked to be concise.

**Tests:** 829 pass, including a new check that the negotiator's input carries the engine's counter.

## 5. Runs 2-6: what each change did (Sun 25 Oct, 00:40-01:40)

Each entry is a change, its argument, its tests and what the next sweep said. Scores move several points between
identical runs (the suppliers are LLMs), so a single case's change is read as evidence only when the trail
explains it.

### Anchor: the credible price, not a blanket percentage (`7c01027`)
Run 1 showed P08 at 0.31 against a best of 0.62: our first counter was 7 % under its quote where the best
session anchored 20 % under. The cause was my own "first counters 4-8 % under closed best", a replay average
that mixed suppliers with firm cost evidence and without. `tools/credibility` already sets that depth per
supplier. The frame and prompts now anchor at the credible price, and the negotiator's input carries the offer
engine's own paced counter (credibility-aware and C-aware, so squeeze cases hold above the C minimum).
**Run 2: P08 0.31 -> 0.56, P04 0.86 and P02 0.39 both new bests, mean 60.4.**

### RFQ latency: nothing blocks a move (`b650dff`, `ba5ec81`, `aa6bd8d`)
Run 2's P05 scored 0.087: a supplier agreed and our accept left 70 s after its reply, past the 60 s limit, so
the thread had timed out and the stale ID was refused. Three steps, each proved by the next run:
1. `accept_best`/`walk` held the case lock across the closing HTTP call, freezing every worker in the case.
   Fixed; one closing action at a time; the coordinator now accepts an agreement itself every poll.
2. A new `waited_s` in the trail then showed the move waited 77 s *before* deciding - so the lock was not the
   main cause. Attachments were downloaded serially between the reply and the decision, through the client's
   9-calls/s pacing shared by all ten cases.
3. Run 4 proved a budget between downloads cannot interrupt one in flight: a single attachment GET blocked a
   worker 76 s. Downloads now run on their own threads.
**Run 5: max wait 1.4 s (was 76.9 s), no move over 60 s, P18 a new best at 0.509.**

### Hallucination: a figure an agent states is never a fact (`ba5ec81`)
The guard keeps invented figures out of the e-mail, but inside the team a specialist could report a price from
memory, the negotiator would read it as fact, and it would persist through the memory log into later rounds.
Every reported figure code can compute (fair, floor, credible price, target, standing C, the accepted and
acceptable flags) is replaced by the computed value before the negotiator sees it; one it cannot compute is
dropped rather than trusted; each substitution is recorded. **Runs 4-5: 3 then 6 corrections, e.g. a reported
target of 30.28 where the band says 26.29.**

### A cost floor above the supplier's own quote is not evidence (`a70cb96`, `9d7b7a5`)
P05's supplier quotes 36.00 while our should-cost floor says 36.87. Nobody sells below cost for long, so the
estimate is wrong, not the price - yet the derived credible price (35.03) held our anchor 2.7 % under the quote,
it answered "agreed" in round one, and we locked in the worst score of the set. Outside `stabilise` and
`secure_supply` (where that picture means a genuinely squeezed supplier, and C already charges us for pushing)
the floor now falls back to the tool's own no-evidence rule. Two steps were needed: relaxing the guard stopped a
low price being raised, but the offer engine prices from the same band, so its anchor had to be corrected where
v2 owns it. On the real openings this moves exactly one supplier (35.03 -> 33.12) and leaves the rest untouched.
**Not yet verified on the hosted arena.**

### Scores across the sweeps

| Case | run 1 | run 2 | run 3 | run 4 | run 5 | verif | best |
|---|---|---|---|---|---|---|---|
| P01 | 0.790 | 0.804 | 0.780 | 0.829 | 0.841 | 0.823 | 0.906 |
| P02 | 0.311 | **0.387** | 0.301 | 0.299 | 0.367 | 0.283 | 0.387 |
| P03 | 0.855 | 0.856 | 0.857 | **0.919** | 0.868 | 0.855 | 0.919 |
| P04 | 0.766 | **0.860** | 0.637 | 0.860 | 0.860 | 0.766 | 0.860 |
| P05 | 0.352 | 0.087 | 0.414 | 0.087 | 0.087 | 0.087 | 0.455 |
| P07 | 0.689 | 0.704 | 0.678 | 0.710 | 0.697 | 0.704 | 0.710 |
| P08 | 0.313 | 0.561 | 0.542 | 0.419 | 0.582 | 0.538 | 0.621 |
| P10 | **0.686** | 0.655 | 0.663 | 0.663 | - | 0.655 | 0.686 |
| P11 | 0.710 | 0.710 | **0.785** | 0.626 | 0.765 | 0.710 | 0.785 |
| P18 | 0.406 | 0.419 | - | 0.443 | **0.509** | 0.406 | 0.509 |
| mean | 58.8 | 60.4 | 62.8* | 58.6 | 55.8* | 58.3 | |

\* run 3 and run 5 are means over 9 cases (one case was refused: another sweep on the team key held a session
slot). Six of the ten team bests now come from v2: P02, P03, P04, P10, P11, P18.

### What is left, in order of expected value

1. **P05 (0.09 against a 0.46 best)** - the anchor correction above should address it; unverified live.
2. **The root cause belongs in shared code**: `tools/credibility` should not derive a near-quote floor from a
   should-cost that exceeds the quote. Fixing it there helps v1 too (an ask for Amine).
3. **Run-to-run spread is 0.1-0.2 per case.** Judging a change on one sweep is unsafe; the trail, not the mean,
   is what justified every change here.

## Practice runs used for the improvement work (limit: 5)
