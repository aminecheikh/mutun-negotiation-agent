# Team Mutun - Tacto Negotiation Arena

An AI buyer agent for Hollerau Pumpenwerke that negotiates by e-mail with the arena's supplier bots.
Score per case = `(C_open - C_deal) / (C_open - C_best)` on total cost C in EUR, not unit price.
Read `brief/brief.md` and `brief/api.md` for the rules; `docs/ROADMAP.md` for plan and owners.

**Deadline: Sun 2026-10-25 13:00 code freeze. Live Final 13:30** (5 waves x 8 min, 10 cases in parallel).

## Layout

- `starter-kit/arena_kit/` - the organisers' kit (client, play loop, cost function, simulator). Change
  it only where needed (e.g. registering `--agent buyer` in `cli.py`); keep their tests passing.
- `starter-kit/arena_kit/agents/buyer/` - **our agent** (`--agent buyer`). `contracts.py` holds the shared
  types; every module starts as a working placeholder that its owner replaces:
  - `tools/` - deterministic: `bands`, `offers`, `claims`, `concession`, `dossier` (owner: Amine)
  - `roles/` - LLM roles + `prompts/`: Profiler (once per case), Analyst -> Strategist -> Writer (each
    round); `llm.py` gives every call a hard timeout and a rule-based fallback (owner: Saed)
  - `agent.py` + `guard.py` - the orchestrator: one round = fit -> read -> choose -> plan -> write ->
    guard; any exception falls back to the kit's naive move (owner: Jeddou)
- `starter-kit/scripts/` - ops: `harvest.py` saves ended sessions and writes a readable digest.
- `starter-kit/runs/` - session dumps, logs and `ledger.jsonl` of every arena run (gitignored).
- `docs/team/` - per-person tracking files and `DECISIONS.md` (see Team protocol below).
- `docs/research/` - web research (LLM negotiators, strategy algorithms, procurement practice, LLM engineering)
  with sourced takeaways per module; start at its `README.md` before changing strategy, prompts or constants.
- `.claude/hooks/` - `session_start.py` (who you work with, their tasks, others' asks) and
  `after_arena_run.py` (logs every `arena play|run`, harvests foreground runs).
- `data/` (repo) and `starter-kit/data/package` (from `uv run arena data`) - the same data package.

## Commands (run from `starter-kit/`)

```bash
uv sync --all-extras                            # a bare `uv sync` drops anthropic (LLM roles fall back) and the sim
uv run pytest                                   # kit + our tests
uv run ruff check . && uv run ruff format --check .
uv run arena play case:D01 --agent buyer -v     # sandbox: free, no quota
uv run arena play case:P01 --agent buyer -v     # one hosted practice case: 1 session of quota
uv run arena run --set practice --agent buyer --parallel 10 > runs/<name>.log 2>&1   # 10 sessions
uv run python scripts/harvest.py --set practice --digest                             # then read runs/digest-practice.md
uv run arena me                                 # quota left
```

## Hard rules

- **Practice quota is ~189 sessions until the final** (they free up 24 h after use, i.e. after the
  final). Use the sandbox (`--set sandbox`, D01/D02) and `arena-sim` for plumbing; spend hosted
  sessions only on versions worth measuring, and say how many a command will use before running it.
- **Numbers in code, words in the LLM.** Prices, C, bands, claim figures come from `tools/`. Roles
  choose and write; they never invent a number or a data ID.
- **Never send an unverified claim.** A `contradicted` claim costs trust. Claims come pre-checked
  against `facts.json` with the arena's rule (`brief/api.md` "What counts"). Max 3 per message.
- **60 s per move**, from the supplier's reply. Budget per round < 30 s with 10 cases in parallel;
  every LLM call has a timeout and a deterministic fallback.
- **No supplier-specific hardcoding.** Final cases use suppliers practice never shows.
- **Never read `/v1/events`, `/screen`, `/leaderboard`, `/results`** from agent code (disqualifying
  during the final).
- **Never commit keys** (`.env` is gitignored). Never print `.env` values.
- `stabilise` / `secure_supply` cases charge for squeezing the supplier below its sustainable margin:
  always evaluate C with the relationship term (`cost.relationship_cost` with an estimated `full_cost`).

## Generalise, don't overfit (the final is cases we have never seen)

- **Decisions come from mechanisms, not from practice outcomes**: the cost formula, the supplier rules in
  the brief, the structure of the data. A practice case shows *whether* something works, never *which number*
  to pick. Never write logic or a prompt line that names a practice case, supplier, part or price.
- **Constants come from documents or first principles** (`company.md`, the brief, the literature), each with
  a comment saying where. Do not tune a constant until the practice scores look right: with 10 cases that
  is fitting noise. If a value must be tuned, tune it on behaviour seen across many runs (sandbox included),
  keep it to few parameters, and prefer the choice that is least bad across case types.
- **Priors from data, corrections from the live negotiation.** Every estimate is a starting point that the
  supplier's own moves can overrule (concession fit, its answers to our term sheets, documents it sends).
- **Degrade gracefully.** Every tool and role must work when data is missing: unknown supplier or part,
  no should-cost, bundles, other price units, other negotiable fields, case-only `facts`, the Sunday release.
- **Test properties, not practice numbers.** Besides unit tests on known rows, run the code on all 19
  practice case files (9 were never played: a holdout) and on mutated copies (`tests/test_tools_robustness.py`).
  `tools/calibrate.py` is a sanity check (no absurd targets), not a target to fit.

## Team protocol (every Claude session follows this)

1. **Know who you work with.** The SessionStart hook names the member from the branch
   (`feat/tools` Amine, `feat/roles` Saed, `feat/orchestrator` Jeddou; `MUTUN_MEMBER` overrides). If it
   says unknown, ask before writing anything under `docs/team/`.
2. **Stay in your lane.** Edit only the member's own directories (table in `docs/team/README.md`). A
   change elsewhere - another layer, the kit, `contracts.py` - goes to the user first; if agreed, the
   owner is told via **Asks to others** and a `contracts.py` change gets an entry in `DECISIONS.md`.
3. **Keep the member's file current** (`docs/team/<member>.md`): replace **Now** when the work changes,
   set task status in **Tasks**, append to **Log** after each meaningful step with evidence (commit, test
   result, score, replay link). Never edit another member's file; write requests to them in your own
   **Asks to others**.
4. **Every arena run is recorded.** The PostToolUse hook logs `arena play|run` to `runs/ledger.jsonl`
   and harvests foreground runs. For background runs, run `scripts/harvest.py --set <set> --digest` when
   the notification arrives. Then add a row to **Runs** and use skill `replay-analysis` on weak cases.
5. **Prefer free runs.** Order: unit tests -> local `arena-sim` -> sandbox (D01/D02) -> hosted practice.
   Check the member's quota budget in `docs/team/README.md` and `uv run arena me` before a hosted run.
6. **Before merging into `main`**: `uv run pytest` and `uv run ruff check .` green,
   one sandbox game with `--agent buyer` finishes, the member's file is up to date.

## Lessons from replays (keep this list current)

- Baseline (kit LLM agent, 2026-10-24): mean 54.4. Losses came from too generous first offers
  (supplier "agreed" at round 1 in P02, P18), the relationship charge in P02 (score 0), index
  clauses accepted on rising paths, and closing after 2-3 of 10 rounds.
- "It holds, but it does not change our calculation": verified data alone rarely moves price.
  Trades (payment days, contract length, volume, effective date) and the supplier's own cost drivers do.

- Sandbox D02 (scaffold, 16:14): **an RFQ thread we do not answer times out.** Each thread has its own
  60 s clock; `play()` sends one message per round, so the second bidder got 3 timeouts and walked away
  (we lost our competition). Every live thread needs a message within 60 s of its supplier's reply.
- Sandbox D02: concessions of +0.02-0.14 EUR per round on price alone made the supplier say "without any
  movement on your side..."; it offered trades (standard lead time, more volume) that we never used.
- Sandbox D02 (merged main, 18:22, 0.52): **never send the same e-mail twice.** A supplier's cost breakdown
  re-priced our target far below our last offer; absolute tit-for-tat then copied its 1.40 drop from an
  inflated opening up to its own price, no sheet beat its standing one, and the same keep-alive went out 3x
  ("your email system is stuck") until it closed at its final offer. A big first drop is not a step to copy,
  and a round with nothing to offer still needs a new question.
- Sandbox D02 (fixes, 18:56, 0.36): **never answer a final offer without a move.** Holding our price above
  target while the supplier came down, then repeating our last sheets after its `final_offer`, made it walk
  away; we closed on the dearer rival. Every supplier move gets one back (capped at the midpoint), a re-priced
  band must reach the verdict at once, and the one counter to a final offer must offer it more.

## Conventions

- Python 3.12, type hints on signatures, frozen dataclasses for shared types, ruff (line length 120).
- Tests with pytest in `starter-kit/tests/`, named `test_<what>_<when>_<expected>`; tools are pure
  functions over the SQLite package, so test them against real rows.
- Branches: `feat/tools`, `feat/roles`, `feat/orchestrator` off `main`; merge back at
  each checkpoint (19:00, 22:00, ~02:00, Sun 10:00). Conventional commits with the task ID, e.g.
  `feat(tools): A1 floor from should-cost`. The final runs from `main`.
- No TODO without a task ID from `docs/team/<member>.md`.
