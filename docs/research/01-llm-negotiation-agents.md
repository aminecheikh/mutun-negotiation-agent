# LLM negotiation agents: systems, benchmarks, competitions, and what they taught

**TL;DR.** People who have built LLM negotiators keep landing on the same design as ours. Code picks
the move and the number, and the LLM only writes the words (He et al. 2018; CICERO 2022; OG-Narrator
2024; AgreeMate 2024). When an LLM negotiates on its own it fails in predictable ways, and **buyers fail
worst**. LLM buyers open close to their budget, reveal it, split the difference, fold under pressure,
and ignore deadlines. Even frontier models stay strongly anchored by the first offer. The largest
prompt competition so far (MIT, about 180k negotiations) found that **warmth** (questions, gratitude,
positive tone) raises deal rate and counterpart satisfaction. **Dominance** claims more value per
deal, but it causes more impasses and longer threads. The top agents planned in hidden structured
reasoning. In industry, Pactum's Walmart deployment is the best-documented system. It runs pre-set
playbooks, sends about three alternative packages per message, and trades non-price terms (payment
days, termination clauses, growth) for price. It reached deals with 64-68% of suppliers at 1.5-3%
average savings. ANAC did not run an LLM negotiation track until the 2026 Human-Agent League (text
plus alternating offers). The strongest classic result to reuse is MiCRO: concede only when the
opponent concedes. It is hard to exploit, but it cannot exploit weak opponents either.

## Key takeaways for our agent

1. **Keep every number out of the LLM, and enforce that in `guard`.** OG-Narrator replaced the LLM
   buyer's own offers with a fixed schedule and had the LLM only narrate. Deal rate went from 26.67% to
   88.88% and profit rose about tenfold (Xia et al. 2024). CICERO conditions dialogue on planner
   "intents" and filters candidate messages for consistency with them. → `guard`: reject or regenerate
   any Writer output that contains a figure not in the Strategist's plan, the term sheets or the
   verified claims (prices, days, volumes, %, data IDs).
2. **Our first offer and our walk-away must never come from the LLM, and nothing may leak them.**
   Baseline LLM buyers opened at 0.81-1.00 of budget (Xia et al.). Buyers who revealed their budget
   early were anchored by sellers and overpaid (Zhu et al. 2025). → `tools/bands` + `offers` set the
   opener. `guard` blocks target, ceiling, reservation estimate and "budget" wording in outgoing text.
3. **Do not split the difference. Concede by our own schedule.** Opening prices predicted final
   prices in NegotiationArena (Spearman ρ = 0.716), agents "split the difference", and anchoring still
   holds in late-2025 frontier models. → `concession`: the next price comes from our Boulware curve and
   the supplier's measured concession (tit-for-tat). Never use the midpoint of the last two offers.
   The Strategist cannot override this.
   - **Reciprocity as the default concession rule (MiCRO).** Concede only after the opponent concedes.
   MiCRO beat the strongest ANAC 2012/13/18/19 agents, and it is a best response against itself among
   consistent strategies. Its known weakness is that it cannot exploit weak opponents. → `concession`:
   keep tit-for-tat as the floor behaviour, plus an explicit end-game rule so pure reciprocity does
   not deadlock (see 7).
4. **Warm in words, firm in numbers.** Warmth raised every outcome, mainly by avoiding impasse, and
   came with questions, gratitude and positive language. Dominance claimed more per deal but caused
   more impasses, and long threads went with impasse (Vaccaro et al.). → Writer prompt: thank, ask
   one calibrated question about the supplier's cost drivers or constraints, and keep messages short.
   Toughness lives in the numbers from `concession`, not in the tone. This also helps the relationship
   term in `stabilise` / `secure_supply` cases.
5. **Send 2-3 packages that cost us the same, not one.** Multiple equivalent simultaneous offers
   (MESOs) gave stronger anchors, more perceived sincerity, more joint gains and a more cooperative
   climate across six experiments (Leonardelli et al. 2019). Pactum's bot sends "maybe three varying
   options" built from price, delivery dates and billing cycles. Walmart traded payment schedule for
   termination-clause concessions and growth for price. → `offers` / `levers`: build up to 3 term
   sheets with roughly equal C for us that vary payment days, volume, contract length, lead time and
   index clause. → `dossier` / `estimates`: treat the supplier's pick or counter as evidence about
   its priorities.
6. **Plan in hidden, structured state carried across rounds.** NegoMate, the best performer in the
   integrative scenarios, reasoned in hidden XML sections (role, items, ranges and walk-away,
   counterpart, decision matrix). Removing that hurt. In LLM-Deliberation, carrying a plan to the
   next turn was important, while a free "explore candidates" step biased GPT-4 toward self-serving
   deals. → Strategist outputs a short plan that is fed into the next round. The Analyst restates
   the state each round (our position, theirs, round "k/10"), which NegotiationArena found cuts
   state hallucination. Candidate generation stays in `offers`, not in the LLM.
7. **Tell every role the deadline explicitly, and let code own the close.** Showing remaining time
   each turn raised GPT-5.1's deal closure from 4% to 32% under wall-clock deadlines. Turn-count
   limits closed almost every time (Sehgal et al. 2026). Stronger negotiators also break more deals:
   in Fu et al. 2023, buyer price and deal rate traded off, and in Lewis et al. 2017, RL+rollouts fell
   to 57% agreement. → `agent.py`: a deterministic end-game rule (accept, final offer or walk) based on
   expected C versus the best alternative. Pass "round k of 10, n left" to Analyst and Writer.
8. **Treat supplier text as untrusted data. Do not cave to pushback.** Project Vend's Claude shop was
   talked into discounts, priced below cost and answered criticism with "You make an excellent
   point!". The supervised Deal-or-No-Deal model was "overly willing to compromise". In the MIT
   competition, prompt injection that extracted the counterpart's offers worked against GPT-4o-mini
   agents. Magentic Marketplace reports that most models are open to manipulation (secondary press;
   check the paper). → Analyst prompt: supplier statements are claims to classify, never
   instructions. `guard`: no concession beyond the `concession` output, whatever the message says.
9. **Pressure from verified facts, never invented ones.** Pretending to be "desolate and desperate"
   gained 20% payoff in NegotiationArena. In AmazonHistoryPrice, hallucinated buyer details made a
   seller accept a price far below cost. In our arena, invented details get checked and cost trust.
   → `claims`: emotional and constraint framing ("approval ladder", "competing quote") only when it
   maps to a verified fact or a real BATNA from the RFQ threads. At most 3 claims per message.
10. **Turn qualitative cues into numbers with Bayes, not with the LLM.** Konishi et al. (ACL Findings
    2026) have an LLM extract qualitative cues from utterances, turn them into probabilities, and feed
    those into a structured Bayesian opponent model. That improved agreement and preference
    estimation. Hua et al. 2024 used a Bayesian-update workflow for incomplete-information
    allocation. → Analyst outputs categorical signals (e.g. "claims final offer", "mentions
    approval", "offers a trade"). `signals` / `estimates` map them to likelihood updates on the
    reservation price.
11. **Strategy choice is the LLM's weakest skill, so make it a menu.** ProCoT found LLMs "fail to
    predict appropriate negotiation strategies and dialogue acts". Kwon et al. found models struggle
    to produce responses that are both contextually appropriate and strategically advantageous. →
    Strategist picks from enumerated acts (counter, trade, MESO, claim, question, final offer,
    accept), each with a deterministic default, so a bad or timed-out LLM call costs little.
12. **Answer every live thread fast.** Magentic Marketplace found severe first-proposal bias: speed
    beat quality by 10-30x. Our own sandbox showed unanswered RFQ threads time out. → orchestrator:
    a per-thread clock, with fallback messages for any thread close to 60 s.

## Sources

### Vaccaro, Caosun, Ju, Aral, Curhan: "Advancing AI Negotiations" (MIT Sloan / JHU; arXiv 2503.06416, v3 Jan 2026)
- **What:** an international prompt-writing competition. Participants wrote system prompts for
  negotiation agents. The paper reports 286 participants; the final had 199 agents in a full
  round-robin, both roles, about 182,812 negotiations. Agents ran on GPT-4o-mini at temperature
  0.2. Scenarios: a distributive chair sale, plus integrative rental and employment contracts.
  Metrics: value claimed, value created, counterpart subjective value, deal rate.
- **Results:** warm agents did better on points, value claimed, value created and subjective value
  (p < 0.001). They reached more deals, but conditional on a deal they claimed less. Dominant agents
  claimed more when a deal happened, and more impasses offset that gain. Warmth and dominance did not
  interact. Warmth came with questions, gratitude and positivity. Dominance came with less gratitude
  and longer conversations, and long conversations went with impasse. NegoMate (hidden XML
  chain-of-thought: role analysis, valuations, ranges and walk-away, counterpart assessment, decision
  matrix) topped individual points in the integrative scenarios. Inject+Voss used prompt injection to
  get counterpart offers, plus the calibrated question "how am I supposed to do that?". In both
  ablations, removing the key component made results worse.
- **Transfers:** a warm, question-asking tone; hidden structured planning; low temperature for
  instruction following; prompts written to generalise across roles and scenarios (participants were
  warned against overfitting).
- **Does not transfer:** one model (GPT-4o-mini), one-shot single-issue negotiations, and the
  correlational design. We should not use prompt injection (ethics and arena rules), but we should
  defend against it.
- Links: https://arxiv.org/abs/2503.06416 ·
  https://mitsloan.mit.edu/ideas-made-to-matter/surprising-power-warmth-ai-negotiations
  (journal venue: unverified).

### Bianchi et al.: NegotiationArena (ICML 2024, arXiv 2402.05863)
- **What:** an open framework with three games (resource exchange, multi-turn ultimatum, buyer-seller
  with incomplete information). It tested GPT-4, GPT-3.5, Claude-2 and Claude-2.1, with 60 games per
  ordered pair.
- **Results:** GPT-4 was best overall and the best buyer (mean $41 against a $60 valuation).
  "Desolate and desperate" personas gained 20% against standard GPT-4. "Cunning" raised the ultimatum
  win rate to 82% without raising payoff, because failed deals pay zero. Opening price predicted
  final price (ρ = 0.716). Agents split the difference. Overvalued buyers counter-offered above the
  seller's ask about 4x more often (0.41 vs 0.11), even when told to be self-interested. Models made
  arithmetic errors and read the end rules wrong.
- **Format lessons:** XML-tagged offers; restating resources and goals each turn (fewer
  hallucinations); hidden reasoning; turn counters such as "2/5". A malformed message from a weak
  opponent could derail a strong agent.
- **Transfers:** structured state each round; guarding against our own anchoring; awareness of
  turn order.
- Links: https://arxiv.org/abs/2402.05863 · https://github.com/vinid/NegotiationArena ·
  follow-up "The Illusion of Rationality" (Ríos et al., arXiv 2512.09254): frontier models settle into
  model-specific equilibria, anchoring persists, and models collapse different valuations into
  generic price points.

### Xia et al.: "Measuring Bargaining Abilities of LLMs" + OG-Narrator (ACL Findings 2024, arXiv 2402.15813)
- **What:** the AmazonHistoryPrice benchmark (930 products, 18 categories), with buyer budget B and
  seller cost C. The paper finds the **buyer role much harder than the seller role**, and scale does
  not fix it.
- **Baseline failures:** first bids at 0.81-1.00 of budget (GPT-4 0.90), negative average profit,
  role confusion in small models, hallucinated details.
- **OG-Narrator:** a deterministic offer generator, p_t = (0.5 + 0.5·t/t_max)·B. Accept if the
  seller's offer is ≤ p_t, otherwise bid p_t. The LLM only writes the message. Deal rate went from
  26.67% to 88.88% and profit rose about 10x. Mixtral buyer SNP went from −63 to 1049.
- **Transfers:** direct evidence for "numbers in code". **Does not transfer:** the linear schedule
  is naive. Ours is Boulware plus tit-for-tat, multi-issue, and runs against suppliers with floors.
- Links: https://arxiv.org/abs/2402.15813 · https://github.com/TianXiaSJTU/AmazonPriceHistory

### Abdelnabi et al.: LLM-Deliberation (NeurIPS 2024 D&B, arXiv 2309.17234)
- **What:** six-party, five-issue scoreable games with secret scores and thresholds, including greedy
  and saboteur variants. Agents use a hidden scratchpad (observe, explore, plan).
- **Results:** for GPT-4, the best setup reached 81% ≥5-way agreement, against 25% without CoT. Removing
  the plan hurt. An explicit "explore candidates" step biased GPT-4 toward its own interest. One greedy
  agent cut success from 81% to 57%. GPT-3.5 mis-scored deals and leaked secret scores. Theory-of-mind
  accuracy on others' preferences was 61% for GPT-4. A 2025 reproducibility study found single-agent
  baselines often match the multi-agent results.
- **Transfers:** carry the plan across rounds; never let the LLM do the scoring arithmetic; check
  leaks of private numbers in `guard`.
- Links: https://arxiv.org/abs/2309.17234 · https://github.com/S-Abdelnabi/LLM-Deliberation ·
  https://arxiv.org/abs/2502.16242

### Zhu et al.: "The Automated but Risky Game" (A2A negotiation in consumer markets; ICML 2025 workshop, arXiv 2506.00073)
- **What:** 9 models (o3, o4-mini, GPT-4.1, DeepSeek-R1/V3, Qwen2.5, GPT-3.5...) as buyer and seller,
  5 budget levels, 30-round cap.
- **Results:** buyers accepted out-of-budget deals (0.4-11.8% depending on model). Buyers overpaid
  above retail at high budgets, linked to **revealing the budget early**. Deadlocks came from pushing
  after a firm minimum. Buyers bargained less hard with bigger budgets. Changing the **seller** moved
  price reduction by up to 14.9%, changing the buyer only 2.6%. Negotiation skill tracked MMLU and
  MATH. Prompt optimisation fixed out-of-budget errors (18.4% → 1.3%) but not overpayment or deadlock.
- **Transfers:** never disclose budget or target; keep effort independent of slack (bargain just as
  hard when the band is wide); detect firm minimums and switch to trades instead of grinding.
- Links: https://arxiv.org/abs/2506.00073 · https://github.com/ShenzheZhu/A2A-NT

### Fu, Peng, Khot, Lapata: self-play + AI feedback (arXiv 2305.10142, 2023)
- **What:** balloon bargaining with a third LLM as critic that gives 3 suggestions per round, used as
  in-context learning over repeated games.
- **Results:** only strong models improved. claude-v1.3 gained +1.58 as seller, and GPT-4 lowered the
  buyer price by 3.0. Weaker models could not improve as **buyers**. Better prices came with lower deal
  rates, and GPT-4 had the best trade-off. Verbosity did not help.
- **Transfers:** an offline loop (arena-sim plus a critic LLM over harvested transcripts) to refine
  Writer and Strategist prompts. Watch deal rate alongside price.
- **Does not transfer:** online critic rounds within a case (no time).
- Links: https://arxiv.org/abs/2305.10142 · https://github.com/FranxYao/GPT-Bargaining

### Lewis et al.: Deal or No Deal? (EMNLP 2017, arXiv 1706.05125)
- **What:** 5,808 human dialogues dividing items under hidden values. Compares supervised, RL and
  dialogue rollouts (simulated futures used at decode time).
- **Results vs humans:** supervised scored 4.7 vs 5.8 and was "overly willing to compromise".
  Rollouts scored 5.2 vs 5.4 with 72.1% agreement. RL+rollouts scored 4.6 vs 4.2, but agreement fell to
  57.2% because more aggressive play made humans walk away. Self-play drift was fixed by a frozen
  supervised partner plus interleaved supervised updates. The agents feigned interest in worthless
  items so they could later concede them.
- **Transfers:** lookahead over candidate packages; aggressiveness costs agreements. "Concede an item
  you don't value" maps to offering levers that are cheap for us (e.g. effective date) as visible
  concessions.
- Links: https://arxiv.org/abs/1706.05125 · code: https://github.com/facebookresearch/end-to-end-negotiator
  (repo URL not re-verified).

### He, Chen, Balakrishnan, Liang: Decoupling Strategy and Generation (EMNLP 2018, arXiv 1808.09637)
- **What:** CraigslistBargain dataset. A parser maps utterances to coarse dialogue acts (e.g.
  `propose(price=50)`), a manager chooses the next act (rules, SL or RL), and a generator realises it.
- **Results:** higher task success and more human-like behaviour in human evaluation. RL on acts
  avoided the degenerate language of end-to-end RL.
- **Transfers:** our read → choose → write split is this design. Rule-based managers are a
  legitimate, strong baseline.
- Link: https://arxiv.org/abs/1808.09637

### Meta CICERO (Science 2022, "Human-level play in Diplomacy...")
- **What:** a planning/RL module predicts everyone's actions. Its chosen intents condition a 2.7B
  dialogue model. Candidate messages pass several filters (nonsense, value, consistency with intent)
  before sending.
- **Results:** more than double the average human score over 40 anonymous league games, top 10% of
  repeat players. Supervised-only agents were "gullible" and easy to manipulate by lying players.
- **Transfers:** plan → intent → message → filter is our orchestrator + `guard`. Filter for
  consistency with the plan, not only for format.
- Links: https://www.mit.edu/~gfarina/2022/cicero · https://github.com/facebookresearch/diplomacy_cicero
  (archived 2025).

### Strategy planners: ProCoT, PPDPP, AgreeMate, FishBargain, DialogXpert
- **ProCoT** (Deng et al., EMNLP Findings 2023): chain-of-thought plus goal planning. LLMs mis-predict
  negotiation strategies and dialogue acts, and proactive prompting helps only slightly.
  https://arxiv.org/abs/2305.13626
- **PPDPP** (Deng et al., ICLR 2024): a small tunable policy planner plugged into a frozen LLM, trained
  with SFT plus RL from goal-oriented AI feedback. It covers negotiation among other tasks.
- **AgreeMate** (Chatterjee et al., arXiv 2412.18690): a decoupled manager (offer, counter, agree acts)
  plus generator, fine-tuned Llama-3.2-3B buyer/seller/generalist with CoT annotations.
- **FishBargain** (Kong et al., arXiv 2502.10406): a seller agent deployed to thousands of Xianyu
  sellers. It selects an action and a "language skill", then generates text. No numbers verified here.
- **DialogXpert** (AAAI 2026): a frozen LLM proposes candidate actions, and a small Q-network selects
  one. Unverified beyond the abstract.
- **Transfers:** strategy as a small action menu chosen by policy, with the LLM proposing or ranking
  at most.

### Hua et al.: Game-theoretic LLM workflows (arXiv 2411.05990)
- LLMs deviate from rational play as games grow. Hand-designed game-theoretic workflows (including a
  Bayesian-update workflow for incomplete-information Deal-or-No-Deal) reached Pareto-optimal,
  envy-free allocations with Claude-3.5 Sonnet, GPT-4o and Claude-3 Opus. **Transfers:** explicit
  belief updates in code.

### Konishi et al.: Preference Estimation via Opponent Modeling (ACL Findings 2026, arXiv 2604.15687)
- An LLM extracts qualitative cues, those become probabilities, and a structured Bayesian opponent
  model uses them. That improved full-agreement rate and preference estimation on a multi-party
  benchmark. **Transfers:** the Analyst → `estimates` interface (see takeaway 10).

### Kwon et al.: "Are LLMs Effective Negotiators?" (EMNLP Findings 2024, arXiv 2402.13550)
- 35 tasks across comprehension, partner modelling and generation. GPT-4 was best, but models
  correlate poorly with humans on subjective judgements, and they struggle to write replies that are
  both contextually appropriate and strategically advantageous. https://github.com/DSincerity/SysEval-NegoLLMs

### Sehgal, Guntuku, Ungar: "Real-Time Deadlines Reveal Fragile Temporal Adaptation" (arXiv 2601.13206)
- Wall-clock deadlines: telling agents the remaining time each turn raised closure from 4% to 32%
  (GPT-5.1-chat), and acceptance rose about 6x. Turn limits gave near-perfect closure. Qualitative
  urgency cues sometimes beat numeric countdowns, and repeated reminders did not reliably help.
  **Transfers:** state the round budget explicitly; the close is handled by code.

### Microsoft Magentic Marketplace (2025)
- An open-source two-sided market simulator. Severe **first-proposal bias**: response speed gave
  10-30x advantages over quality. Welfare collapsed with many options. Six manipulation strategies
  (fake credentials, social proof, prompt injection) were tested; per-model vulnerability figures come
  from press summaries and are unverified.
  https://www.microsoft.com/en-us/research/publication/mage/

### Anthropic Project Vend (June 2025)
- Claude Sonnet 3.7 ran an office shop. It was talked into discount codes, priced items below cost
  without checking, passed up a ~6x margin offer, and conceded to criticism and then relapsed. The fixes
  Anthropic suggested were structured reflection, CRM/memory, cost-research tools and, longer term, RL.
  **Transfers:** a hard concession budget in code; check cost before quoting.
  https://www.anthropic.com/research/project-vend-1

### AgoraBench / MERIT (Oh et al., arXiv 2602.10467, 2026) and Concordia Contest (NeurIPS 2024)
- AgoraBench: 9 settings, including deception and monopoly. Baseline LLM strategies diverge from human
  preferences, and utility feedback (prompting or fine-tuning) improves bargaining. Numbers unverified.
- Concordia Contest (Cooperative AI Foundation with DeepMind, MIT and others; results at NeurIPS D&B
  2025, arXiv 2512.03318): agents had "moderate success" at negotiation but struggled with persuasion
  and norm enforcement and with generalising to unseen scenarios. This matches our "final cases are
  unseen" constraint.

### ANAC: Automated Negotiating Agents Competition
- **Classic leagues (2010-):** alternating offers over utility spaces (Genius, later NegMAS). Recurring
  winners combine time-dependent Boulware concession with frequency-based opponent models (e.g.
  HardHeaded 2011, CUHKAgent 2012, as listed in the NegMAS docs). ANL 2025: sequential multi-deal
  bilateral negotiations with inter-dependent utilities. The SCM league runs supply-chain negotiation.
  Agent code since 2024 is in the `anl-agents` repo.
- **MiCRO** (de Jonge, IJCAI 2022; JAAMAS 2024): no opponent model, concedes only in response to the
  opponent's new offers. It beat the strongest ANAC 2012/13/18/19 agents and is a proven best response
  to itself. It cannot exploit weak opponents.
- **LLM involvement:** I found no LLM track in ANAC 2024/2025 results. The **Human-Agent Negotiation
  League (HAN) 2026** (IJCAI-ECAI 2026) used alternating offers plus free text against Prolific humans,
  with two prizes: individual advantage and subjective rating. Winners: NegotiatorX (advantage),
  AegisAgentR168 (2nd), Equinox (subjective). Whether the winners used LLMs is unverified; reports are
  in the `han-agents` repo, and NegMAS-LLM is listed as a resource.
- **Transfers:** Boulware plus reciprocity is a robust prior. Reports from HAN 2026 are worth reading
  for text-plus-offer agents.
- Links: https://anac.cs.brown.edu · https://anac.cs.brown.edu/han2026 · https://negmas.readthedocs.io ·
  https://www.ijcai.org/proceedings/2022/32

### Commercial procurement negotiation
- **Pactum (Walmart, Maersk).** Walmart's three-month Canadian pilot in early 2021 covered 89
  suppliers of goods-not-for-resale (e.g. carts). Buyers supplied scenarios that were turned into
  negotiation scripts. Walmart sought **better payment schedules in exchange for concessions on its
  right-to-terminate clause**, and offered **growth opportunities for price discounts**. Deals with
  64% of suppliers, average 11 days, 1.5% saving. Later (US, Chile, South Africa): 68% of suppliers,
  3% saving. 83% of closing suppliers were favourable and valued negotiating at their own pace. Pactum
  also reports a 35-day average payment-term extension (72% agreement) and early-payment-discount
  trades (vendor claims). IEEE Spectrum: the bot sends "maybe three varying options", "mostly based on
  price, delivery dates, and billing cycles"; it takes counteroffers, and the deal proceeds once terms
  fall within the target range. Its LLMs do not write new clauses. Guardrails: client-set parameters,
  thresholds, escalation, audit logs. The CEO warned it can "create harm" if fed inaccurate
  information.
  Links: https://www.pon.harvard.edu/daily/negotiation-skills-daily/chatbot-negotiations-what-can-ai-do-for-you ·
  https://spectrum.ieee.org/ai-contracts · https://pactum.com/commercial-terms/ ·
  HBR, Van Hoek et al., Nov 2022 (paywalled, not read).
- **Tacto** (the arena's organiser). It markets Defender (counter-arguments to every supplier price
  increase), Should-Cost / Cost Breakdown (cost models from raw material, energy and labour indices,
  mapped to suppliers; a customer quote says this cut negotiation preparation by about 75%), RFQ and
  Negotiation agents ("you set the strategy, the AI agent executes"), and 20,000 proprietary indices.
  How it works is not disclosed. The arena's emphasis on should-cost, indices and verified claims
  mirrors this product. https://tacto.ai/us · https://www.tacto.ai/en/intelligence
- **Keelvar:** autonomous sourcing on logic configured by the buyer, escalating to humans outside set
  parameters. It is mainly for tail and MRO spend. https://www.keelvar.com/autonomous-sourcing
- **Fairmarkit:** AI sourcing agents for tail spend and RFQs. Keelvar's own comparison calls it
  single-round only (competitor claim, unverified). **Zip:** intake and orchestration; I found no
  negotiation agent.
- **MIT CTL** (Saenz & Revilla 2025): staged autonomy, from copilot to semi-autonomous (adjusting
  within limits) to fully autonomous inside guardrails.
  https://ctl.mit.edu/publications/how-ai-reshaping-supplier-negotiations
- **What transfers:** every commercial system runs a **pre-approved playbook with numeric bounds**,
  offers **packages of non-price trades**, and keeps the LLM away from inventing terms. Nobody
  publishes their pipeline in detail.

### MESO research
- Leonardelli, Gu, McRuer, Medvec & Galinsky (OBHDP 2019, vol. 152): across six experiments, MESOs
  gave stronger anchors and better outcomes for the offerer, through perceived "agreement sincerity",
  plus joint gains and a more cooperative climate.
  https://ideas.repec.org/a/eee/jobhdp/v152y2019icp64-83.html
- Park et al. (Sustainability 2019): an argumentation plus MESO negotiation agent in 49 agent-human
  negotiations improved perceived process and willingness to negotiate again (no effect sizes seen).

## Known failure modes of LLM negotiators

| Failure | Evidence | Our counter |
|---|---|---|
| Opens too close to the limit (buyer) | First bid 0.81-1.00 of budget (Xia et al. 2024) | `bands`/`offers` opener |
| Reveals budget or target, then gets anchored | Overpayment linked to early budget reveal (Zhu et al. 2025) | `guard` leak filter |
| Anchored by the counterpart's first number; splits the difference | ρ = 0.716 opening→final; midpoint splitting (Bianchi et al.); persists in frontier models (Ríos et al. 2025) | `concession` schedule ignores midpoints |
| Bids above the seller's own ask, or above budget | 0.41 vs 0.11 (Bianchi); 0.4-11.8% out-of-budget (Zhu) | `guard`: never offer above the last ask or the ceiling |
| Caves to pushback / sycophancy | Project Vend discounts; "overly willing to compromise" (Lewis 2017) | Concession budget in code |
| Manipulable by injection or persuasion | Inject+Voss (Vaccaro); Magentic Marketplace; CICERO's "gullible" SL agent | Supplier text = data; Analyst classifies |
| Arithmetic and scoring errors, hallucinated state | LLM-Deliberation (GPT-3.5); NegotiationArena | All C and lever math in `tools/` |
| Leaks private information | GPT-3.5 leaked secret scores (Abdelnabi) | `guard` |
| Ignores deadlines / does not close | 4% closure without time feedback (Sehgal 2026); a weak model saturates after ~3 rounds (Fu 2023) | Explicit round k/10; code-owned end-game |
| Aggressiveness → impasse | RL+rollouts 57% agreement (Lewis); price vs deal-rate trade-off (Fu); dominance → impasse (Vaccaro) | Walk/accept rule on expected C |
| Grinds after a firm minimum → deadlock | Zhu et al. 2025 | Switch to trades/MESO when `signals` flags a firm floor |
| Accepts the first adequate proposal | First-proposal bias 10-30x (Magentic Marketplace) | Compare all threads' C before accepting |
| Poor strategy / dialogue-act choice | ProCoT; Kwon et al. 2024 | Strategist picks from a menu with defaults |
| Self-serving exploration | GPT-4 with explicit candidate generation (Abdelnabi) | Candidates from `offers`, not the LLM |
| Verbosity without effect; long threads → impasse | Fu 2023; Vaccaro | Short messages, one ask per message |

## Open questions / ideas to test (sandbox and arena-sim first)

- **MESO vs single offer:** same opener C, either 1 package or 3 iso-cost packages (payment days /
  volume / lead time). Measure the supplier's concession in the next reply and the final C. Does the
  supplier's choice predict its priorities well enough to update `estimates`?
- **Warmth A/B:** a Writer prompt with one calibrated question plus gratitude against a neutral one,
  with identical numbers. Compare deal rate, rounds to close and the relationship term.
- **Calibrated question as concession substitute:** reply to a supplier ask with "how am I supposed
  to make that work at X?" instead of moving. Does the supplier move first? (Voss tactic from
  Inject+Voss, without the injection.)
- **Reciprocity strictness:** strict MiCRO-style (no move without a supplier move) against our
  Boulware+TFT, for deal rate and score across all sandbox runs, plus a forced end-game rule.
- **Visible concessions that cost us little:** offer a lever that is cheap for us (per `levers`) as a
  "concession" in exchange for price, the legitimate form of Lewis et al.'s feigned-interest finding.
  Measure whether the supplier treats it as a move.
- **Round-budget phrasing:** "round 7 of 10" against qualitative urgency ("we need to close this
  week") in the Writer. Sehgal et al. found qualitative cues sometimes work better.
- **Leak audit:** run `guard` over all harvested transcripts to count any target, ceiling or
  reservation figure in outgoing text. It should be 0.
- **Anchoring defence:** check, across runs, whether our counter correlates with the supplier's
  opening ask beyond what `bands` justify. If it does, the Strategist or Writer is leaking anchoring.
- **Offline self-critique loop (Fu et al.):** a critic LLM reads harvested losses and proposes prompt
  edits. Accept an edit only if it helps across sandbox plus arena-sim and on the 9 holdout cases,
  never on a single practice case.
- **Lookahead (Lewis rollouts, cheap version):** have the Analyst predict the supplier's response to
  each of the 3 term sheets, then compare with what actually happens. If it predicts well, use it to
  rank packages. If not, drop it (a 30 s budget).
- **Firm-floor detection:** when the supplier says "final" or "approval needed", does switching to
  non-price trades beat continuing price concessions (Zhu et al. deadlock finding)?
- **Read the HAN 2026 reports** (`han-agents` repo) for how text-plus-offer agents combined a scripted
  strategy with language. Unverified whether the winners used LLMs.
