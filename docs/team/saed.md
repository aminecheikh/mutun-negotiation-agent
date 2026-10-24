# Saed - LLM roles

Branch `feat/roles` · owns `starter-kit/arena_kit/agents/buyer/roles/` (code + `prompts/`) · skills: `replay-analysis`, `arena-practice-run`

## Now

- Not started: S1 next.

## Tasks

| ID | Task | Status | Due |
|---|---|---|---|
| S1 | Writer v1 (`prompts/writer.md`): tone per persona, no numbers of its own, no leaks; offline test on recorded threads in `runs/sessions/` | todo | Sat 19:00 |
| S2 | Strategist as an LLM call (`prompts/strategist.md`) returning `Tactic`; `RuleStrategist` stays the fallback | todo | Sat 22:00 |
| S3 | Analyst as an LLM call (`prompts/analyst.md`) returning `BehaviourRead`; numbers only from `ConcessionFit` | todo | Sat 22:00 |
| S4 | Profiler (`prompts/profiler.md`): persona + levers from dossier evidence, precomputed for all 46 suppliers, cached | todo | Sun 02:00 |
| S5 | Playbook: what moved / backfired per persona, mined from `runs/digest-practice.md`, fed to the strategist | todo | Sun 02:00 |
| S6 | Latency: measure each role at 10 parallel cases; with Jeddou decide whether analyst + strategist become one call | todo | Sat 22:00 |
| S7 | Pitch slides: architecture + one replay walked through | todo | Sun 11:00 |

## Asks to others

## Log

- 16:30 - scaffold in place (rule-based analyst/strategist, LLM writer with template fallback, prompt stubs) - commit on `feat/negotiation-agent`

## Runs

| When | Commit | Cases | Sessions | Mean | Notes |
|---|---|---|---|---|---|
