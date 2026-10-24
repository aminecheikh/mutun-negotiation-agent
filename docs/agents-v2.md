# Buyer agent v2: a team of agents

Owner: Jeddou. Code: `starter-kit/arena_kit/agents/buyer_v2/`. Tests: `starter-kit/tests/test_v2_*.py`.

## Switching between v1 and v2

| Setting | Agent |
|---|---|
| `AGENTIC_STRUCTURE=1` or unset (**default**) | v2: specialists in parallel, a negotiator, hard rules in code |
| `AGENTIC_STRUCTURE=0` | v1: roles + orchestrator (`agents/buyer`), unchanged |

`--agent buyer` follows the switch. `--agent buyer_v2` and `--agent buyer_v1` pick one explicitly, for
side-by-side runs.

```
uv run arena play case:P01 --agent buyer -v                   # v2 (default)
AGENTIC_STRUCTURE=0 uv run arena play case:P01 --agent buyer  # v1
uv run arena run --set final --agent buyer --parallel 10      # the Live Final, v2
```

The model comes from the same variables as v1: `LLM_PROVIDER=anthropic`, `LLM_MODEL`, `ANTHROPIC_API_KEY`.
Without `LLM_MODEL`, v2 runs on its deterministic rules. The fast paths, the offer engine, the guard and the
templates all still apply, so the agent always negotiates.

## How one move is decided

```
supplier reply ──> facts (code) ──> fast path? ── yes ──> accept / confirm / stop
                                        │ no
                                        v
         ┌──────────── specialists in parallel (each with its own tools) ────────────┐
         │ reader       every round   the latest e-mail as written, against the thread │
         │ deal         every round   C of its offers, room, trades, counter candidates │
         │ counterpart  round 1 + new documents   profile, history, documents, news     │
         │ market       round 1 + new documents   cost drivers, should-cost, arguments  │
         └──────────────────────────────────┬────────────────────────────────────────────┘
                                            v
                 negotiator: accept | counter | walk_away, and the e-mail
                                            v
                 guard (code): hard limits on every sheet and every sentence
                                            v
                 runner: send, or the coordinator accepts / walks
```

- **The e-mails are given as e-mails.** Every agent reads the thread as plain text: the supplier's words as
  written (German, English or mixed), its term sheets, the attachment text, and how our cited data checked out.
- **All data is reachable through tools.**
  - Curated views: supplier overview, documents and news, negotiation history, price history, payment and
    delivery, market indices, cost drivers, should-cost, internal quotes, forecast, contracts.
  - Generic access: `list_tables`, `sql_query` (read-only), `lookup_id`, `list_ids`, `read_document`.
  - The shared deterministic tools: price band, concession fit, lever trades, verified arguments, `price_package`
    (C of any package), `supplier_offers`, and `propose_counter` (the offer engine).
  - Each agent gets the subset its job needs. Every tool is read-only, and none can reach the arena or the live
    feed.
- **Numbers come from code.** Agents ask tools for C and never compute it. The negotiator chooses the packages,
  and the guard corrects or drops any sheet outside the hard limits.
- **Prompts** (`prompts/*.md`, all new):
  - `house_rules.md`: who we are, the arena's rules, the score, our never-broken rules, the data.
  - `playbook.md`: negotiation principles. BATNA and the zone of agreement; integrative trades and multiple
    equivalent offers; anchoring; decreasing concessions; reciprocity; reading a counterpart; data use; closing;
    category goals; pitfalls.
  - One prompt per agent.
  - They name no practice supplier, part or case. A test checks this, so the agents are not fitted to the
    practice set.

## The hard rules (code, never the model)

| Rule | Where |
|---|---|
| The supplier accepted our offer (`agreed`, its standing sheet equals one of ours, or costs us no more) → accept at once, no further e-mail | `facts.thread_facts` (`accepted_ours`) + `brain._fast_path` |
| Our package appears only as its alternative sheet → send exactly that package back, so it becomes its standing sheet | `brain._confirm` |
| Never a unit price above the supplier's current offer | `guard.check_counter` (capped) |
| Every counter costs us less than its standing sheet | `guard.check_counter` (else dropped) |
| Never below the lowest credible price (`tools.credibility`) | `guard.check_counter` (raised) |
| Never take back our current offer | `guard.check_counter` (raised to our latest offer's C) |
| Every sheet signable: must-haves kept, not dearer than the best opening or our alternative | `guard.check_counter` |
| Accept only a signable standing sheet, never the opening quote before our first answer | `guard.accept_ok`, `facts.sheet_acceptable` |
| Walk away only when its sheet cannot be signed and it will not move (final offer or last message) | `agents.walk_allowed` |
| A counter always carries term sheets; the rules' offer engine fills in when the negotiator's packages fail | `brain._apply` → `brain._fallback` |
| E-mails: verified arguments verbatim (from tokens), each ID once per case across threads, no other data ID, no figure outside our sheets, arguments or the supplier's words, no leaks, no other supplier | `guard.clean_email`, `guard.reserve` |

## Several suppliers (RFQ)

- Each thread has its own worker and its own team of agents, and is negotiated on its own (the organisers' rule:
  no inter-supplier comparison).
- The coordinator owns the irreversible acts, under the case lock:
  - **Accept:** it accepts the cheapest acceptable standing sheet among the idle threads. It never signs a dearer
    sheet over a cheaper one and never holds a thread back for a rival.
  - **Refused accept:** a refused accept (a stale ID after a timeout repeat) re-reads the thread from the arena
    and retries once with the current sheet.
  - **Walk away:** only when no thread is negotiating any more and nothing on the table can be signed.
  - **Wave cap:** before a Live Final wave's cap, it accepts the best acceptable sheet, else walks away (no deal
    scores our alternative).

## Timing (60 s per message, counted from the supplier's reply)

| Stage | Bound (from the reply) | Variable |
|---|---|---|
| Specialists, in parallel, ≤ 2 tool turns each | 20 s | `V2_ANALYSTS_BY_S` |
| Negotiator, ≤ 1 tool turn | 42 s | `V2_DECIDE_BY_S` |
| Message leaves | 48 s | `V2_MOVE_BUDGET_S` |

- Every model call carries its deadline as its HTTP timeout, has no SDK retries, and runs through a bounded pool.
  A late or hung call is abandoned and the rules decide.
- Concurrency slots stop specialists from starving negotiators across 10 parallel cases (`V2_ANALYST_SLOTS`,
  `V2_NEGOTIATOR_SLOTS`).
- System prompts are cached, and answers use structured outputs (`output_config.format`).
- Defaults: specialists at effort `low`, the negotiator at `medium`. Change with `V2_ANALYST_EFFORT` /
  `V2_NEGOTIATOR_EFFORT`, and pick models per role with `V2_ANALYST_MODEL` / `V2_NEGOTIATOR_MODEL`.

## Decision trail

Every move, case and model call is logged as JSON lines:
- `runs/v2-rounds.jsonl`: the path taken (fast, agents or rules), each agent's status and time, the negotiator's
  reasoning, the guard's corrections, sheets with C, and the e-mail.
- `runs/v2-cases.jsonl`: each finished case.
- `runs/v2-llm.jsonl`: role, seconds, tools, tokens, errors and the answer.

The directory comes from `V2_LOG_DIR` or `BUYER_LOG_DIR` (`off` disables it).

## Independent review (before the PR)

A separate reviewer, who had not seen the design reasoning, checked the code against the requirements and the
arena contract and reproduced 8 bugs. All of them are fixed and pinned by `tests/test_v2_review.py`:
1. **A repeated 409 looped decide → send.** A 409 now re-reads the thread from the arena: closed flag, status,
   standing offer ID, and timeouts (each one counts as a round). If nothing changed, the thread is not written to
   again.
2. **The confirm path ignored the round limit.** With no messages left, v2 now accepts a signable sheet or stops.
3. **The confirm path skipped the counter rules.** A confirmation now passes `check_counter` unchanged, or it is
   not sent (no price above the supplier's standing offer).
4. **A 409 on an accept ended the case locally while the session was still open.** The thread is now marked
   closed and the other threads go on.
5. **Argument tokens were positions that shifted when another thread cited an ID.** Tokens are now fixed per case.
6. **The rules fallback could send a counter without a sheet.** A "bridge" sheet is now offered (its own terms,
   priced between our offer and its offer).
7. **The caps vanished when its latest e-mail carried no sheet.** The standing offer is now its latest message
   that has a sheet.
8. **Other suppliers' short names and dependence wording passed the e-mail check.** Both are now removed, except
   words the addressee's own name shares.

Hardening that came with the fixes:
- a transient accept error no longer marks a sheet refused;
- a transport error re-reads the thread before deciding again (no double send);
- the coordinator survives an error;
- the 60 s clock starts when the reply arrives (before downloads);
- the rules fallback has a last resort of its own;
- specialists and negotiators use separate executors;
- C_open is recomputed with the current supplier estimates;
- tools serve only the thread's own supplier;
- argument tokens are normalised ("[[a1]]" is A1);
- no "none" index clause is offered where the arena would refuse it.

Remaining, by design:
- When no sheet can satisfy all hard rules at once, the rules fallback still sends a question without a sheet.
  This happens when our earlier offer has become unsignable, or when the credible floor sits above our
  alternative.
- At the wave cap, the coordinator signs the best idle sheet rather than wait for an in-flight thread.

## What is verified, and what is not

- **Verified offline:**
  - The full test suite (670 passed), including 126 v2 tests: rules and guard properties on all 19 practice case files;
    every tool on every practice case with the real data package; the tool loop and its deadlines with a scripted
    model; fast paths; the negotiator kept inside the limits; fallbacks; prompts and schemas.
  - arena-sim on a real clock: every case to a deal; no timeouts, no offer above the supplier's price, nothing
    sent after an agreement, even with a model slower than the budget. A two-wave final rehearsal ends every case
    before its cap.
- **Not verified yet:**
  - a live model run (this machine has no key);
  - a hosted practice sweep.
- **What to measure first:**
  - specialist and negotiator latency at p90 with 10 cases in parallel: `runs/v2-llm.jsonl`, seconds per role;
  - the share of moves on each path, from `runs/v2-rounds.jsonl` (`detail.path`);
  - rate limits under a full wave.

  If the negotiator often misses 42 s, lower `V2_ANALYST_EFFORT`, use a faster `V2_ANALYST_MODEL`, or reduce
  `V2_ANALYST_TOOL_TURNS` to 1.
