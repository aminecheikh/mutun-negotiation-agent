# Team Mutun - Tacto Negotiation Arena

An AI buyer agent for Hollerau Pumpenwerke that negotiates by e-mail with the arena's supplier bots.
Score per case = `(C_open - C_deal) / (C_open - C_best)` on total cost C in EUR, not unit price.
Read `brief/brief.md` and `brief/api.md` for the rules; `docs/ROADMAP.md` for plan and owners.

**Deadline: Sun 2026-10-25 13:00 code freeze. Live Final 13:30** (5 waves x 8 min, 10 cases in parallel).

## Layout

- `starter-kit/arena_kit/` - the organisers' kit (client, play loop, cost function, simulator). Change
  it only where needed (e.g. registering `--agent buyer` in `cli.py`); keep their tests passing.
- `starter-kit/arena_kit/agents/buyer/` - **our agent**. `contracts.py` holds the shared types between:
  - `tools/` - deterministic: dossier, price bands, claims, concession fit, offer engine (owner: Amine)
  - `roles/` - LLM roles: analyst, strategist, writer, their prompts and playbook (owner: Saed)
  - `agent.py` - the orchestrator: `decide(case, threads)`, timing, guard, closing (owner: Jeddou)
- `starter-kit/scripts/` - ops: `harvest.py` saves ended sessions and writes a readable digest.
- `starter-kit/runs/` - session dumps and logs (gitignored).
- `data/` (repo) and `starter-kit/data/package` (from `uv run arena data`) - the same data package.

## Commands (run from `starter-kit/`)

```bash
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

## Lessons from replays (keep this list current)

- Baseline (kit LLM agent, 2026-10-24): mean 54.4. Losses came from too generous first offers
  (supplier "agreed" at round 1 in P02, P18), the relationship charge in P02 (score 0), index
  clauses accepted on rising paths, and closing after 2-3 of 10 rounds.
- "It holds, but it does not change our calculation": verified data alone rarely moves price.
  Trades (payment days, contract length, volume, effective date) and the supplier's own cost drivers do.

## Conventions

- Python 3.12, type hints on signatures, frozen dataclasses for shared types, ruff (line length 120).
- Tests with pytest in `starter-kit/tests/`, named `test_<what>_<when>_<expected>`; tools are pure
  functions over the SQLite package, so test them against real rows.
- Branches: `feat/<area>` off `feat/negotiation-agent`, merge back often (at least every few hours).
  Conventional commits. The final runs from `feat/negotiation-agent` (or `main` after merge).
