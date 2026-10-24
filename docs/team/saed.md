# Saed - LLM roles

Branch `feat/roles` · owns `starter-kit/arena_kit/agents/buyer/roles/` (code + `prompts/`) · skills: `replay-analysis`, `arena-practice-run`

## Now

- S1 writer v1: harvesting recorded threads, then tone per persona + leak checks in `prompts/writer.md`.

## Tasks

| ID | Task | Status | Due |
|---|---|---|---|
| S1 | Writer v1 (`prompts/writer.md`): tone per persona, no numbers of its own, no leaks; offline test on recorded threads in `runs/sessions/` | doing | Sat 19:00 |
| S2 | Strategist as an LLM call (`prompts/strategist.md`) returning `Tactic`; `RuleStrategist` stays the fallback | todo | Sat 22:00 |
| S3 | Analyst as an LLM call (`prompts/analyst.md`) returning `BehaviourRead`; numbers only from `ConcessionFit` | todo | Sat 22:00 |
| S4 | Profiler (`prompts/profiler.md`): persona + levers from dossier evidence, precomputed for all 46 suppliers, cached | todo | Sun 02:00 |
| S5 | Playbook: what moved / backfired per persona, mined from `runs/digest-practice.md`, fed to the strategist | todo | Sun 02:00 |
| S6 | Latency: measure each role at 10 parallel cases; with Jeddou decide whether analyst + strategist become one call | todo | Sat 22:00 |
| S7 | Pitch slides: architecture + one replay walked through | todo | Sun 11:00 |

## Asks to others

- @team - the Anthropic API key is not on Saed's machine yet; roles are tested with their fallbacks until it is.

## Log

- 16:30 - scaffold in place (rule-based analyst/strategist, LLM writer with template fallback, prompt stubs) - commit on `feat/negotiation-agent`
- 16:40 - `roles/playbook.md`: data analysis for S4/S5 - the supplier notes are ~12 fixed templates mapped to 14 archetypes and levers; index moves that help or backfire; fair-increase check per price letter (P01 asks +9.5 %, data justifies +2.3 %; P02 asks +6.5 %, copper justifies +11 %)

## Runs

| When | Commit | Cases | Sessions | Mean | Notes |
|---|---|---|---|---|---|
