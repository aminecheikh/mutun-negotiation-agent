TODO(S2, docs/team/saed.md): strategist prompt. Input: dossier persona and levers, price bands (C, target,
ceiling), behaviour reads per thread, available claims (index + sentence), rounds left. Output ONE JSON
object matching `contracts.Tactic`: {"supplier_id", "action": "send"|"accept"|"walk_away",
"levers": [...], "claims": [indices], "question": str|null, "rationale": str}. No numbers of its own.
