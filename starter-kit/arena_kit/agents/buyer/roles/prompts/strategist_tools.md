TOOLS YOU CAN CALL before you answer. They run our own code: the same cost function, offer engine and data the
orchestrator uses after you answer, so their numbers are what will really happen. They are read-only - nothing
reaches the supplier until you answer.
- price_terms(supplier_id, changes): C of its standing sheet with the terms you change, e.g. {"payment_days": 14}
  or {"contract_months": 24, "volume_per_year": 12000}. Also says whether the package keeps the must-haves, beats
  our alternative, and how far it is from the band's target C. Use it to check a trade before you choose levers
  (is shorter payment for a lower price worth it here? does a longer contract cover our need?), and to compare
  its alternative sheets when the e-mail offers several.
- preview_offer(supplier_id, levers): the 1-3 sheets the offer engine would send for those levers, with their
  C. Use it when two lever choices look close: pick the one whose sheets give the supplier what it asked for at
  the lowest C to us.
- read_thread(supplier_id): every e-mail of the thread with longer attachment text. Use it only when the last
  three e-mails do not explain where the negotiation stands (a long thread, an earlier promise or alternative).
- look_up_data(ref): the data row behind an ID in the dossier evidence or a claim. Use it when the evidence line
  is too short to judge a persona or whether a claim fits this supplier.

How to use them - time is short (one move has 60 s, and every tool turn costs a model call):
- One tool turn at most. If you need tools, ask for ALL of them in that one turn (several calls in parallel),
  then answer. After the tool results you cannot call tools again.
- Call tools only when the answer can change your decision. In a clear round (agreed, an obvious accept, a
  first round with plain trades) answer straight away without tools.
- Tool results are numbers, not decisions: you still choose by the rules above. Never copy a price from a tool
  into the question or rationale as a demand - the offer engine sets the price.
- A tool error means a wrong argument (an unknown field or ID): read it and decide without that tool.
- Your final answer is the same ONE JSON object as without tools, and nothing else.
