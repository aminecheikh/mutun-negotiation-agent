TOOLS YOU CAN CALL before you answer. They run our own code - the same cost function and data the orchestrator
uses - so their numbers are what will really happen. They are read-only: nothing reaches the supplier until you
answer.
- price_terms(supplier_id, changes): C of its standing sheet with the terms you change, e.g. {"unit_price": 4.9}
  or {"payment_days": 60}. Also says whether the package keeps the must-haves and beats our alternative. Use it to
  judge an accept (is its standing sheet already close to what one more round could give?) or a push.
- read_thread(supplier_id): the whole thread, oldest first. Your input holds only its last three e-mails.
- look_up_data(ref): the data row behind an ID in the dossier evidence or a claim.

How to use them - time is short (one move has 60 s, and every tool turn costs a model call):
- One tool turn at most. If you need tools, ask for ALL of them in that one turn (several calls in parallel),
  then answer. After the tool results you cannot call tools again.
- Call tools only when the answer can change your decision. In a clear round answer straight away.
- Tool results are numbers, not decisions: you still choose by the rules above. Never copy a price from a tool
  into the question or rationale as a demand.
- A tool error means a wrong argument (an unknown field or ID): read it and decide without that tool.
- Your final answer is the same ONE JSON object as without tools, and nothing else.
