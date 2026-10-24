TODO(S3, docs/team/saed.md): analyst prompt. Input: the supplier's last 2-3 e-mails, status, signals,
claim results and the concession fit (prices, last step %, shrinking, reservation estimate). Output ONE
JSON object matching `contracts.BehaviourRead`: {"p_more": 0-1, "walk_risk": 0-1, "wants": [...],
"signals": [short quotes from its e-mail]}. Interpret words; take numbers only from the fit.
