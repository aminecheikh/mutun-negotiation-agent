# Saed - LLM roles

Branch `feat/roles-saeb` · owns `starter-kit/arena_kit/agents/buyer/roles/` (code + `prompts/`) · skills: `replay-analysis`, `arena-practice-run`

## Now

- S1-S4 done and benched with the real model (no quota). Next: S5 playbook from the 22:00 sweep replays.

## Tasks

| ID | Task | Status | Due |
|---|---|---|---|
| S1 | Writer v1 (`prompts/writer.md`): tone per persona, no numbers of its own, no leaks; offline test on recorded threads in `runs/sessions/` | done | Sat 19:00 |
| S2 | Strategist as an LLM call (`prompts/strategist.md`) returning `Tactic`; `RuleStrategist` stays the fallback | done | Sat 22:00 |
| S3 | Analyst as an LLM call (`prompts/analyst.md`) returning `BehaviourRead`; numbers only from `ConcessionFit` | done - merged into the S2 call (see Log 17:05) | Sat 22:00 |
| S4 | Profiler (`prompts/profiler.md`): persona + levers from dossier evidence, precomputed for all 46 suppliers, cached | done - `LLMProfiler`, `profiles.json` (Log 17:55) | Sun 02:00 |
| S5 | Playbook: what moved / backfired per persona, mined from `runs/digest-practice.md`, fed to the strategist | todo | Sun 02:00 |
| S6 | Latency: measure each role at 10 parallel cases; with Jeddou decide whether analyst + strategist become one call | done - one call is right: strategist median 4.0 s / p90 7.3 s, writer 4.1 s / 7.6 s at 10 in parallel (Log 17:15) | Sat 22:00 |
| S7 | Pitch slides: architecture + one replay walked through | todo | Sun 11:00 |

## Asks to others

- @amine @jeddou **URGENT for the final** (17:35) - `DataPackage` shares ONE sqlite connection (`check_same_thread=False`) across all agents. Bench with tools v1 from main, 10 cases in parallel as in a final wave: **6 of 40 rounds crashed** (5 x `sqlite3.InterfaceError: bad parameter or other API misuse` in `estimates.index_change -> DataPackage.query`, 1 x `IndexError`); with one lock around data access: **0 of 40**. A crashed round falls back to the naive move. Fix: a `threading.Lock` around `DataPackage.query`/`execute` (kit file `arena_kit/data.py` - agree who changes it), or one connection per thread. Repro (no quota): `uv run python -m arena_kit.agents.buyer.roles.bench`, then the same with `--lock`.
- @jeddou - please wire the roles in `buyer_factory` (both exported from `roles`): `LLMStrategist(complete)` instead of `RuleStrategist()`, and `LLMProfiler(complete)` instead of `PassthroughProfiler()`. With no model each returns exactly the old behaviour. The strategist writes the model's refined `BehaviourRead` back into the `reads` dict `_round` passes to `_act`, so keep passing that same dict to `plan_offers`. The profiler is a cache lookup (no LLM) for every supplier in the data package; it calls the model (6 s cap) only when the case brings its own facts about a supplier - in an RFQ with 3 new suppliers, refining the dossiers in parallel threads in `_context` would keep round 1 short.
- @amine - sandbox D01 (16:59, replay https://arena.tacto.ai/replay/872eb1c173aaf469, score 0.11): `offers.plan` sent one sheet per round and ignored `tactic.levers` (strategist asked for unit_price + payment_days + contract_months); our price went 48.67 -> 48.67 -> 48.71 -> 48.79 while the supplier came down 52.90 -> 51.90 and asked three times for "a reciprocal step from your side", then went final at 51.80. Needs: vary the tactic's levers across the 1-3 sheets, a visible step whenever the supplier moved, contract_months covering the horizon (12 months left need uncovered).
- @jeddou - `scripts/harvest.py` writes `runs/sessions/*.json` with the platform encoding (cp1252 on Windows); `encoding="utf-8"` on `write_text`/`read_text` would fix it. `tests/test_writer.py` reads both meanwhile.
- @jeddou - Saed's branch is `feat/roles-saeb` (the hook matches the `feat/roles` prefix, so nothing breaks); the table in `docs/team/README.md` still says `feat/roles`.

## Log

- 16:30 - scaffold in place (rule-based analyst/strategist, LLM writer with template fallback, prompt stubs) - commit on `feat/negotiation-agent`
- 16:40 - `roles/playbook.md`: data analysis for S4/S5 - the supplier notes are ~12 fixed templates mapped to 14 archetypes and levers; index moves that help or backfire; fair-increase check per price letter (P01 asks +9.5 %, data justifies +2.3 %; P02 asks +6.5 %, copper justifies +11 %)
- 16:50 - S1 writer v1: tone per persona in `prompts/writer.md`; `check()` rejects changed claims, own numbers/IDs, leaks (P01/P05 'costs trust'); persona template fallback - `tests/test_writer.py` 13 passed, full suite 53 passed
- 17:05 - S2+S3 in one call: `LLMStrategist` (`roles/strategist.py`, `prompts/strategist.md`) returns reads + tactic; `safe()` blocks first-round accepts, walks without a cheaper alternative, unknown/already-cited/unfavourable claims, non-negotiable levers, questions with figures; accept policy `accept_ready` (agreed, out of messages, final_offer beating the alternative, C <= band target). `RuleAnalyst` reads wants and irritation from the supplier's words; `RuleStrategist` probes >= 2 terms with claims and a cost-breakdown question - `tests/test_strategist.py` 19 passed
- 17:05 - writer: packages are described from the real sheets (`movement()`), never from intended levers; "we moved" only when our price rose; the supplier's step is acknowledged - D01 showed the template claiming trades the sheet did not contain - full suite 72 passed, 1 skipped
- 17:15 - real-model bench (`roles/bench.py`, recorded threads, 10 in parallel, no quota): strategist answer used 40/40, writer 23/24 (1 timeout at 15 s -> template), 0 unsafe e-mails, 0 errors; writer now rejects "we have moved" when our price did not rise; strategist survives malformed model fields (lists/numbers/null) - role tests 34 passed
- 17:31 - merged main (tools v1) into `feat/roles-saeb`: 342 passed. Real-model bench with real claims/bands: 40/40 strategist, 40 claims cited verbatim in 25 e-mails, 0 unsafe; writer now also rejects data talk without a claim ("comparable parts", "market prices", "our analysis") - Amine's D02 ask - and allows years recalled from our own notes. `playbook.md` marked reference-only (Amine's ask): prompts carry only general rules, no practice names or figures; cited IDs are tracked once per case (`cited_refs`).
- 17:55 - S4 profiler: `LLMProfiler` judges the rules' dossier (persona, levers) and adds traits the rules cannot express as `profile:` evidence lines (hedged until YYYY-MM, uninformed_owner, open_book, pricing_committee, price_freeze, not_strategic, partnership_memory ...), every trait backed by an evidence ID. `profiles.json` precomputed for 46/46 suppliers in 28 s (no quota); spot checks match the data (70520 hedged 2027-03 + committee; 70420 price freeze 2027-12; 70150 cash need + idle + open book). Live call only for case facts. Strategist now reads attachment text (cost breakdown = cost floor); writer rejects revealing our private notes (bank, idle shifts, spreadsheet, hedges) - real-model bench: 40/40 strategist, 26/27 writer, 42 claims cited, 0 unsafe - 351 passed

## Runs

| When | Commit | Cases | Sessions | Mean | Notes |
|---|---|---|---|---|---|
| 16:59 | uncommitted S2 | D01 (sandbox) | 0 | 0.11 | rule path (no key); plumbing ok, 0.0 s per decision; low score from the offers placeholder (no levers, no steps) - ask to Amine |
