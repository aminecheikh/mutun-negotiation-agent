# Saed - LLM roles

Branch `feat/roles-saeb` · owns `starter-kit/arena_kit/agents/buyer/roles/` (code + `prompts/`) · skills: `replay-analysis`, `arena-practice-run`

## Now

- S2+S3 code done and tested; waiting for the API key for the first real-LLM run (sandbox, no quota). Next: S4 profiler.

## Tasks

| ID | Task | Status | Due |
|---|---|---|---|
| S1 | Writer v1 (`prompts/writer.md`): tone per persona, no numbers of its own, no leaks; offline test on recorded threads in `runs/sessions/` | done (LLM run pending key) | Sat 19:00 |
| S2 | Strategist as an LLM call (`prompts/strategist.md`) returning `Tactic`; `RuleStrategist` stays the fallback | done (LLM run pending key) | Sat 22:00 |
| S3 | Analyst as an LLM call (`prompts/analyst.md`) returning `BehaviourRead`; numbers only from `ConcessionFit` | done - merged into the S2 call (see Log 17:05) | Sat 22:00 |
| S4 | Profiler (`prompts/profiler.md`): persona + levers from dossier evidence, precomputed for all 46 suppliers, cached | todo | Sun 02:00 |
| S5 | Playbook: what moved / backfired per persona, mined from `runs/digest-practice.md`, fed to the strategist | todo | Sun 02:00 |
| S6 | Latency: measure each role at 10 parallel cases; with Jeddou decide whether analyst + strategist become one call | todo (needs key) | Sat 22:00 |
| S7 | Pitch slides: architecture + one replay walked through | todo | Sun 11:00 |

## Asks to others

- @team - the Anthropic API key is not on Saed's machine yet; roles are tested with their fallbacks until it is.
- @jeddou - please wire the new strategist in `buyer_factory`: `LLMStrategist(complete)` instead of `RuleStrategist()` (exported from `roles`). With no model it returns exactly the rule's tactic. It writes the model's refined `BehaviourRead` back into the `reads` dict `_round` passes to `_act`, so keep passing that same dict to `plan_offers`.
- @amine - sandbox D01 (16:59, replay https://arena.tacto.ai/replay/872eb1c173aaf469, score 0.11): `offers.plan` sent one sheet per round and ignored `tactic.levers` (strategist asked for unit_price + payment_days + contract_months); our price went 48.67 -> 48.67 -> 48.71 -> 48.79 while the supplier came down 52.90 -> 51.90 and asked three times for "a reciprocal step from your side", then went final at 51.80. Needs: vary the tactic's levers across the 1-3 sheets, a visible step whenever the supplier moved, contract_months covering the horizon (12 months left need uncovered).
- @jeddou - `scripts/harvest.py` writes `runs/sessions/*.json` with the platform encoding (cp1252 on Windows); `encoding="utf-8"` on `write_text`/`read_text` would fix it. `tests/test_writer.py` reads both meanwhile.
- @jeddou - Saed's branch is `feat/roles-saeb` (the hook matches the `feat/roles` prefix, so nothing breaks); the table in `docs/team/README.md` still says `feat/roles`.

## Log

- 16:30 - scaffold in place (rule-based analyst/strategist, LLM writer with template fallback, prompt stubs) - commit on `feat/negotiation-agent`
- 16:40 - `roles/playbook.md`: data analysis for S4/S5 - the supplier notes are ~12 fixed templates mapped to 14 archetypes and levers; index moves that help or backfire; fair-increase check per price letter (P01 asks +9.5 %, data justifies +2.3 %; P02 asks +6.5 %, copper justifies +11 %)
- 16:50 - S1 writer v1: tone per persona in `prompts/writer.md`; `check()` rejects changed claims, own numbers/IDs, leaks (P01/P05 'costs trust'); persona template fallback - `tests/test_writer.py` 13 passed, full suite 53 passed
- 17:05 - S2+S3 in one call: `LLMStrategist` (`roles/strategist.py`, `prompts/strategist.md`) returns reads + tactic; `safe()` blocks first-round accepts, walks without a cheaper alternative, unknown/already-cited/unfavourable claims, non-negotiable levers, questions with figures; accept policy `accept_ready` (agreed, out of messages, final_offer beating the alternative, C <= band target). `RuleAnalyst` reads wants and irritation from the supplier's words; `RuleStrategist` probes >= 2 terms with claims and a cost-breakdown question - `tests/test_strategist.py` 19 passed
- 17:05 - writer: packages are described from the real sheets (`movement()`), never from intended levers; "we moved" only when our price rose; the supplier's step is acknowledged - D01 showed the template claiming trades the sheet did not contain - full suite 72 passed, 1 skipped

## Runs

| When | Commit | Cases | Sessions | Mean | Notes |
|---|---|---|---|---|---|
| 16:59 | uncommitted S2 | D01 (sandbox) | 0 | 0.11 | rule path (no key); plumbing ok, 0.0 s per decision; low score from the offers placeholder (no levers, no steps) - ask to Amine |
