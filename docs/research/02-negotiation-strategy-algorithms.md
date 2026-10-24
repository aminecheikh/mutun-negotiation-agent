# Negotiation strategy as algorithms: what the evidence says we should encode

**TL;DR.** The agent-negotiation literature (Faratin-Sierra-Jennings, the ANAC competitions, Baarslag et al.) and
the human-negotiation literature (anchoring, concession patterns, MESOs, BATNA, hard vs soft bargaining) agree on
a few points. (1) The **bidding/concession strategy decides most of the outcome**: 58% of the explained variance
in one ANAC study, against 12% for acceptance and 4% for the opponent model. (2) A **steady, visible concession**
is the most robust. Pure Boulware gets the best price when a deal happens but loses deals, most of all under
short deadlines, and we have at most 10 rounds. (3) **Acceptance should compare against our own next offer and,
near the end, against the best offer seen recently** (`AC_combi(T, MAX^W)`), not trust status words such as
"final offer". (4) **Ambitious anchors work, and they work better when precise and justified**. Their cost is
impasse and anger (meta-analysis: g = -0.42 on deals, g = -0.40 on recipients' subjective value), which matters
for `stabilise`/`secure_supply`. (5) **Three equal-cost term sheets (MESOs)** improve both anchoring and joint
value, and they show what the supplier values. (6) **Competition is worth more than negotiating skill**
(Bulow-Klemperer), so every RFQ bidder has to stay alive. (7) **Warm wording with firm numbers** gets more deals
without giving up value. Our current design is broadly consistent with this. The main gaps are an explicit
`AC_next`/`AC_combi` acceptance rule, the shape of our late concessions (increasing under Boulware, against the
evidence that decreasing steps signal a limit), and absolute tit-for-tat, which can be exploited.

## Key takeaways for our agent

1. **orchestrator / Strategist acceptance: add `AC_next`, plus `AC_combi(T, MAX^W)` near the end.** Today we
   accept on the supplier status `agreed`/`final_offer`/out of messages, with the guard checking the BATNA.
   Evidence: `AC_combi(0.98, MAX^W)` scored 0.762, at least 12% better than `AC_next` alone (p < 0.01), and
   `AC_combi(T, MAX^T)` (wait for the best offer ever seen) lost deals (Baarslag, Hindriks & Jonker 2014).
   In cost terms: accept if `C(standing) <= C(our next planned sheet)`. In the last 1-2 rounds, also accept if
   `C(standing) <= min C the supplier offered in the previous window of the same length`. Treat "final offer" as
   a signal for the reservation estimate, not as a trigger to accept.
2. **concession: keep a time curve, but make the steady/linear part the backbone.** In Faratin et al. (1998) the
   best tactic overall was **Linear** (beta = 1). Boulware (beta in {0.01, 0.2}) made "significantly fewer deals",
   and with short deadlines faster conceders won. Our beta = 0.5 plus the "linear pace until target by halfway"
   floor already blends the two, which **agrees** with the evidence. Do not move toward lower beta.
3. **concession / Writer: make the late steps get smaller.** A Boulware curve with beta = 0.5 gives
   `share = (t/T)^2`, so each step is larger than the one before (1, 3, 5, 7...). That pattern signals that more
   is coming. Tey et al. (2021, 7 studies, N = 2,311) found that a series of **decreasing** concessions makes
   recipients overestimate the reservation price and counter less ambitiously. Proposal: keep the cumulative
   curve, but once we pass the target, spend the remaining room in tapering steps (for example halving), and
   let the Writer say we are near our limit. (The black-hat/white-hat evidence of Hilty & Carnevale 1993 points
   the other way for *early* moves: tough first, then concede. The two combine as "hold early, then taper".)
4. **offers: tit-for-tat in relative terms, never as an unconditional floor.** Faratin's imitative tactics
   "never do better than other tactics; the best they do is gain equal utility to the best tactic". Matching the
   supplier's absolute EUR concession lets an inflated opening "buy" equal moves from us. Better: match the
   *fraction of its own range* it conceded (relative TfT, or averaged over gamma = 2 moves), capped by the time
   curve and the ceiling. Keep "always move something" (the arena rule "gives nothing without a move").
5. **offers (anchor): the midpoint anchor is consistent with the evidence. Add precision and a cap.** LLM
   negotiators split the difference: new proposals track the mean of the last two offers, and the first offer
   predicts the final price with Spearman rho = 0.716 (Bianchi et al. 2024, NegotiationArena). Humans show
   r ≈ 0.5 (Orr & Guthrie 2006, figure seen only second-hand) and r = 0.62 across 90 studies (Petrowsky et al.
   2025). So `anchor = 2*target - their_open` is well founded. Two additions: use **precise** prices (e.g. 4.37,
   not 4.40). Round offers draw larger counter-adjustments (Mason et al. 2013). Second, keep the floor guard:
   ambitious first offers raise impasses and anger, and complexity (more issues) weakens the anchor.
6. **bands / Writer: as the responder, anchor on our own numbers.** The supplier quotes first. The first-offer
   advantage disappeared when the responder focused on the opponent's reservation price, its alternatives, or
   their own target (Galinsky & Mussweiler 2001, also over e-mail). Target focus also protects against the
   decreasing-concession tactic (Tey et al. 2021). The Writer should argue from should-cost, target and
   alternatives, never "you are X% too high".
7. **offers (variants): 2 equal-cost variants + main = a 3-sheet MESO, which agrees with the evidence.** Six
   experiments: MESOs give stronger anchors (perceived "agreement sincerity"), more joint gain, and a more
   cooperative reputation (Leonardelli et al. 2019). Build each variant on our iso-cost curve and make one of
   them the point **closest to the supplier's last sheet** (the similarity trade-off of Faratin et al. 2002).
   Vary **different levers** across sheets (payment days, volume/contract length, lead time, index clause), so
   that the supplier's choice tells us its priorities.
8. **signals / estimates: a cheap opponent model is enough. Put the effort into priors.** The opponent model
   explained only 4% of performance; "once an agent employs a reasonable opponent model, there is not much more
   to gain" (Baarslag et al. 2014, ECAI). With fewer than 10 points, the should-cost prior dominates our Bayesian
   reservation fit (Zeng & Sycara 1998 style, with Faratin-family likelihoods as in Yu et al. 2013). Keep sigma
   honest: overconfidence "may lead to incorrect results" (Hindriks & Tykhonov 2008). Add a **frequency model**
   on non-price terms: terms the supplier keeps fixed matter to it, terms it trades freely are cheap for it.
9. **Strategist (RFQ): competition beats skill.** An auction with one extra bidder beats an optimal negotiation
   (Bulow & Klemperer 1996). In the lab, the best procurement design was an English auction followed by a
   take-it-or-leave-it offer to the winner (Shachat & Tan 2015). Encode it as: keep every bidder's thread alive
   (the D02 lesson), let rivals know they compete (only with verified claims), then make a final sheet to the
   cheapest bidder near its estimated reservation price.
10. **Writer tone: warm words, firm numbers. Strategist: soften the line only where the relationship term
    charges for it.** Hardline tactics give better economic results and softline tactics better socioemotional
    ones (Hüffmeier et al. 2014 meta-analysis). In 180k AI-vs-AI negotiations, warmth raised the deal rate,
    dominance raised the value claimed given a deal, and there was no warmth x dominance interaction (Vaccaro
    et al. 2025). A competitively prompted buyer chatbot won on price, payment terms and speed, while a
    collaborative one won supplier trust (Herold et al. 2025). In `stabilise` cases, use the relationship cost
    term, not the tone, to limit how hard we squeeze.
11. **Deadline: plan to be at our final position by round T-2.** Agreements cluster at the deadline (Roth,
    Murnighan & Schoumaker 1988). Holding out to the last round risks no deal: the supplier needs a round to
    accept, and a timeout ends the thread. Revealing a final deadline made counterparts concede faster (Moore
    2004). Worth a test: say "we decide this week".

## Techniques

### 1. Time-dependent tactics (Faratin, Sierra & Jennings 1998)
- **Definition** (primary text, *Robotics and Autonomous Systems* 24:159-182): the offer for issue j at time t is
  `x[j](t) = min_j + alpha(t)(max_j - min_j)` if utility decreases in j (a buyer's price), or
  `min_j + (1 - alpha(t))(max_j - min_j)` if it increases.
  - Polynomial: `alpha(t) = kappa + (1 - kappa) * (min(t, t_max)/t_max)^(1/beta)`
  - Exponential: `alpha(t) = exp((1 - min(t, t_max)/t_max)^beta * ln kappa)`
  - `beta < 1` is **Boulware** (holds, then concedes near the deadline). `beta > 1` is **Conceder**. `kappa` sets
    the opening point in the range.
- **Evidence** (same paper): with experiment values Boulware beta in {0.01, 0.2}, Linear beta = 1, Conceder beta in
  {20, 40} and kappa = 0.1, the **Linear** tactic dominated across deadlines. Boulware got high utility *when*
  it made deals but made far fewer of them. Under short deadlines, Conceder/Impatient won. Behaviour-dependent
  (imitative) tactics ranked 3rd-4th. The same family is the standard opponent model elsewhere, as
  `s(t) = 1 - (1 - RU)(t/T)^(1/beta)` ([Sanchez-Anguix et al., arXiv 1604.04737](https://arxiv.org/pdf/1604.04737)).
- **ANAC practice:** HardHeaded (ANAC 2011 winner) used a monotonically decreasing target with one inflection,
  Boulware first and then Conceder when discounting applied. Gahboninho (2nd, judged the most robust) conceded
  steadily to utility 0.9 in the first bids to probe the opponent, then turned selfish against conceders and
  conceded against hardheads. Its target was `U_t = U_max - (U_max - U_min) * t` over the opponent's observed
  range ([Baarslag et al. 2013, AIJ 198](https://homepages.cwi.nl/~baarslag/pub/Evaluating_practical_negotiating_agents-results_and_analysis_of_the_2011_international_competition.pdf)).
  Their conclusion: low concession pays against steady conceders, and conceding pays against hardheads. That is
  the "teacher vs learner" dilemma, so **detect the opponent's adaptivity first**.
- **For <= 10 e-mail rounds:** t = round/round_limit is coarse, so beta below 0.3 leaves almost all movement to
  the last 2 rounds. That is the impasse risk Faratin measured. Keep beta in about [0.5, 1] plus the visible
  linear floor. Source: [Faratin et al. 1998 PDF](http://jmvidal.cse.sc.edu/library/faratin98a.pdf).

### 2. Behaviour-dependent tactics (tit-for-tat variants)
- **Definitions** (Faratin et al. 1998, Sec. 4.3). With the opponent's offers `x_b` and ours `x_a` over the
  thread:
  - Relative TfT (delta >= 1): `x_a(t_{n+1}) = min(max( x_b(t_{n-2δ}) / x_b(t_{n-2δ+2}) * x_a(t_{n-1}), min_j), max_j)`
  - Random absolute TfT: `x_a(t_{n+1}) = min(max( x_a(t_{n-1}) + (x_b(t_{n-2δ}) - x_b(t_{n-2δ+2})) + (-1)^s R(M), min_j), max_j)`,
    with R(M) a random integer in [0, M]
  - Averaged TfT (window gamma): the proportion is taken over gamma past moves. gamma = 1 is relative TfT.
- **Evidence:** imitative tactics never beat the best tactic in Faratin's tournaments. In ANAC, Nice Tit for Tat
  (2011 finalist) cooperated first, mirrored concessions in its *own* utility, and aimed at an estimated Nash
  point ([Baarslag et al. 2013 chapter](https://research.vu.nl/en/publications/a-tit-for-tat-negotiation-strategy-for-real-time-bilateral-negoti/),
  details second-hand via [arXiv 1909.09461](https://arxiv.org/pdf/1909.09461)). Human data, second-hand:
  against a tough opening a *matching* strategy produced more yielding (Lawler & MacMurray 1980).
- **For us:** use TfT as a *response modifier* around the time curve (the ANAC pattern), expressed relative to
  the supplier's own range so that an inflated opening does not inflate our steps.

### 3. Acceptance strategies (Baarslag, Hindriks & Jonker)
- **Definitions** ([Effective acceptance conditions, DSS 60:68-77, 2014](https://homepages.cwi.nl/~baarslag/pub/Effective_Acceptance_Conditions_in_Real-time_Automated_Negotiation.pdf)), with U = our utility:
  - `AC_next(α, β)`: accept if `α·U(x_opp) + β >= U(x_our_next)`
  - `AC_prev(α, β)`: the same, against our previous offer. `AC_gap(β) = AC_prev(1, β)`
  - `AC_const(α)`: `U(x_opp) >= α`. `AC_time(T)`: accept once `t >= T`
  - `AC_combi(T, α)`: `AC_next ∨ (AC_time(T) ∧ U(x_opp) >= α)`, with α equal to `MAX^W` (best offer in the
    previous window W = [t - r, t], r = 1 - t), `AVG^W`, or `MAX^T` (best ever).
- **Evidence:** in the agent pool surveyed, `AC_next(1,0)` was the most common. `AC_next` is never worse than
  `AC_prev`. `AC_combi(T, MAX^W)` and `AC_combi(T, AVG^W)` dominated, with T in {0.98, 0.99} not significantly
  different. `MAX^T` lost deals. IAMhaggler used `AC_const(0.88) ∨ AC_next(1.02,0) ∨ AC_prev(1.02,0)`.
  Acceptance explains 12% of performance on average but **5% to 47% as the opponent gets more cooperative**
  ([Baarslag et al., ECAI 2014](https://homepages.cwi.nl/~baarslag/pub/The_significance_of_bidding_accepting_and_opponent_modeling_in_automated_negotiation.pdf)).
- **Pseudo-code in cost terms (lower C is better):**
  ```
  c_now  = C(standing sheet incl. relationship term); c_next = C(our planned main sheet)
  if c_now <= c_next * (1 + eps): accept                      # AC_next, eps ~ 0..0.02 (alpha 1..1.02)
  rounds_left = round_limit - round
  if rounds_left <= 1 and c_now <= min(C of supplier sheets in the last k rounds) and c_now <= C_batna: accept
  ```
  With 10 rounds, "T = 0.98" means "the last round". The window W then covers the previous 1-2 supplier sheets.

### 4. Opponent modelling with few rounds
- **Bayesian reservation price** (Zeng & Sycara 1997/1998): hypotheses `H_i: rv = v_i` with domain priors, updated
  by Bayes on each offer. The estimate is the probability-weighted mean. Yu et al. (2013) combine non-linear
  regression on a time-dependent tactic, estimating rv and the deadline, with Bayesian updating. Hou (2004) fits
  which tactic family is used, together with its parameters ([survey: Baarslag, Hendrikx, Hindriks & Jonker
  2016, JAAMAS 30:849-898](https://homepages.cwi.nl/~baarslag/pub/Learning_about_the_opponent_in_automated_bilateral_negotiation-a_comprehensive_survey_of_opponent_modeling_techniques.pdf)).
  Our `tools/concession.py` follows this pattern (Faratin likelihood averaged over beta, should-cost prior).
- **Hindriks & Tykhonov (AAMAS 2008)**, multi-issue Bayesian: hypotheses over issue-weight rankings and value
  shapes (downhill/uphill/triangular). Likelihood `P(b_t|h) = N(u(b_t|h); u'(b_t), σ)` with a predicted linear
  concession `u'(b_t) = 1 - 0.05 t`. σ encodes certainty about the opponent's tactic. Set it too low and the
  results are wrong ([PDF](https://aamas.csc.liv.ac.uk/Proceedings/aamas08/proceedings/pdf/paper/AAMAS08_0385.pdf)).
- **Frequency model** (HardHeaded, Nice TfT, ANAC): issue values the opponent offers often are preferred, and
  issues that change often are unimportant. It is cheap and robust.
- **Few-round reality:** model choice matters little (4% of variance). With 3-6 supplier price points, the prior
  (should-cost floor, history) carries the estimate. Use the supplier's choice among our 3 sheets as an extra,
  cleaner observation of its priorities.

### 5. Multi-issue trade-offs and MESOs
- **Similarity trade-off** (Faratin, Sierra & Jennings 2002, *AIJ* 142:205-237): generate offers on our own
  iso-utility (iso-cost) curve and use fuzzy similarity plus hill-climbing to pick the one most similar to the
  opponent's last offer, as the most acceptable one at no cost to us
  ([record](https://eprints.soton.ac.uk/256872)). Our `equal_cost_variants` is this idea without the similarity
  pick.
- **MESOs** ([Leonardelli, Gu, McRuer, Medvec & Galinsky 2019, OBHDP 152:64-83](https://ideas.repec.org/a/eee/jobhdp/v152y2019icp64-83.html)):
  six experiments against a single package showed (a) stronger anchoring through perceived sincerity, (b) more
  often an option attractive to the recipient, so higher joint gain, and (c) a cooperative reputation and
  climate. The practitioner guidance is three offers of equal value to you
  ([Medvec & Galinsky 2005](https://www.kellogg.northwestern.edu/faculty/research/detail/2005/putting-more-on-the-table-how-making-multiple-offers/)).
  Building them needs a scoring system across unlike issues, which is our C function.
- **Revealing preferences backfires:** a first offer that discloses *compatible* preferences gives a first-mover
  *disadvantage* (Loschelder et al. 2014, *Psych Science* 25:954-962,
  [PubMed](https://pubmed.ncbi.nlm.nih.gov/24525264/)). Do not state in prose which terms we care little about.
  Let the sheets imply it.
- **Fixed-pie bias:** most negotiators assume fully opposed interests and miss compatible issues (Thompson &
  Hastie 1990). Payment days and index clauses are often worth more to the supplier than they cost us, which is
  the integrative core of our lever values.

### 6. Anchoring and the first offer
- **Galinsky & Mussweiler (2001, JPSP 81:657-669):** whoever made the first offer did better, and first offers
  predicted the settlement, face to face and **by e-mail**. Focusing on the opponent's alternatives or reservation
  price, or on one's own target, cancelled the effect
  ([Kellogg](https://www.kellogg.northwestern.edu/faculty/research/detail/2001/first-offers-as-anchors-the-role-of-perspective-taking)).
- **Robustness and limits:** the effect holds across cultures, power levels and multi-issue settings, and works
  through the distributive issues ([Gunia et al. 2013, PSPB 39](https://www.london.edu/faculty-and-research/academic-research/t/the-remarkable-robustness-of-the-first-offer-effect-across-culture-power-and-issues)).
  The 2025 preregistered meta-analysis (374 effects, 90 studies, N = 16,334) found first-offer magnitude to
  agreement r = 0.62. Ambitious first offers bring **more impasses (g = -0.42)** and **worse subjective value for
  recipients (g = -0.40)**, mediated by anger, and the effects shrink as complexity grows
  ([Petrowsky et al. 2025, OBHDP 191:104448](https://pubdata.leuphana.de/entities/publication/29dc7e5d-01eb-420a-b73d-66e0be7cd440)).
  Extreme openers offend, and low-power recipients walk away
  ([Schweinsberg et al. 2012, JESP 48:226-231](https://www.london.edu/faculty-and-research/academic-research/s/starting-high-and-ending-with-nothing-the-role-of-anchors-and-power-in-negotiations)).
- **Precision:** recipients adjust less from precise first offers than from round ones, because precision signals
  knowledge. Too aggressive a precise offer reads as inflexible (Mason, Lee, Wiley & Ames 2013, JESP 49:759-763,
  [preprint](https://drupalgsb-test.paas.cc.columbia.edu/sites/default/files-efs/pubfiles/5957/precise_offers.pdf)).
- **Range offers:** a *bolstering* range (from the target toward more ambitious) often improved the outcome
  without relational cost. *Backdown* ranges lost value (Ames & Mason 2015, JPSP 108:254-274). A secondary source
  says a width of 5-25% captures most of the benefit
  ([Columbia summary](https://business.columbia.edu/insights/chazen-global-insights/anchor-negotiation-two-points-may-be-better-one)).
- **LLM counterparts:** first offer to final price rho = 0.716, split-the-difference counters, buyers ended
  below the midpoint in most model pairs ([Bianchi et al., ICML 2024](https://proceedings.mlr.press/v235/bianchi24a.html)).
- **Our case:** the supplier opens, so we are the responder. Our first counter is in effect a first offer on the
  remaining gap, and the midpoint rule puts the expected split at our target.

### 7. Concession size and pattern, deadlines, "final offer" credibility
- **Decreasing concessions** signal a limit. Recipients counter less ambitiously and get worse deals. The effect
  weakens when steps shrink too slowly or too fast, and with too few or too many rounds
  ([Tey, Schaerer, Madan & Swaab 2021, OBHDP](https://www.isb.edu/faculty-and-research/research-directory/the-impact-of-concession-patterns-on-negotiations-when-and-why-decreasing-concessions-lead-to-a-distributive-disadvantage)).
  Second-hand: a decreasing opponent pattern drew larger initial concessions (MacMurray & Lawler 1986), and
  concession *rate* mattered more than the size of the first concession (Druckman et al. 1972).
- **Black-hat/white-hat** (few early concessions, then larger ones) was more effective than the reverse
  (Hilty & Carnevale 1993, OBHDP 55:444-469, known to us only through an
  [MIT OCW lecture](https://ocw.mit.edu/courses/15-067-competitive-decision-making-and-negotiation-spring-2011/)).
  To reconcile with Tey: hold early, then concede in *shrinking* steps toward the end.
- **Deadline effect:** agreements cluster just before the deadline (Roth, Murnighan & Schoumaker 1988, AER
  78:806-823). Revealing one's final deadline led counterparts to concede faster, against negotiators'
  intuition (Moore 2004, OBHDP 94:125-139, [IDEAS](https://ideas.repec.org/a/eee/jobhdp/v94y2004i2p125-139.html)).
- **"Final offer" credibility:** we found no direct experiment on revised "final" offers. Theory: take-it-or-
  leave-it offers become credible through a reputation for obstinacy (Ozyurt 2015,
  [SSRN](https://papers.ssrn.com/abstract=2671411)), and practitioner accounts warn that repeated "final" offers
  destroy credibility. **Unverified as an empirical regularity.** Rule: we say "final" at most once per thread
  and never move after it. A supplier's "final" lowers our reservation-room estimate but does not trigger an
  automatic accept.

### 8. BATNA and competition in multi-supplier cases
- **Bulow & Klemperer (1996, AER 86:180-194):** a no-reserve auction with N+1 bidders beats an optimally
  structured negotiation with N, so "the value of negotiating skill is small relative to the value of additional
  competition" ([IDEAS](https://ideas.repec.org/a/aea/aecrev/v86y1996i1p180-94.html)).
- **Shachat & Tan (2015, Mgmt Sci 61:1036-1051):** the optimal procurement design is an English auction plus a
  take-it-or-leave-it offer to the winner. Sellers exited at cost and accepted any strictly profitable offer.
  Buyers' TIOLI offers drifted with the auction price, which is a bias to avoid
  ([IDEAS](https://ideas.repec.org/a/inm/ormnsc/v61y2015i5p1036-1051.html)).
- **Communication can raise procurement prices:** experiments with procurement managers found communication
  raised prices, and auctions without communication often did better in competitive markets
  ([ZEW 2025 discussion paper](https://www.zew.de/PU95635), abstract only).
- **BATNA:** having an alternative improves individual outcome and share. Pairing it with a specific goal raised
  impasses (Brett, Pinkley & Jackofsky 1996, second-hand). The responder's focus on the opponent's alternatives
  neutralises its anchor (Galinsky & Mussweiler 2001).
- **Relationship cost of auctions:** open-bid reverse auctions raised suppliers' suspicion of buyer opportunism
  (Jap 2007, J. Marketing 71:146-159, findings only second-hand). This matters in `stabilise` cases.
- **For us:** in RFQs, run each thread on its own 60 s clock, keep it moving, and quote rivals only through
  verified claims. Converge on the cheapest bidder with a final sheet near its estimated rv. Keep the runner-up
  warm until we accept.

### 9. Relationship, fairness, integrative vs distributive
- **Hüffmeier, Freund, Zerres, Backhaus & Hertel (2014, J. Management 40:866-892):** 34 studies. Hardline
  strategies give better economic outcomes, softline better socioemotional outcomes
  ([UvA record](https://dare.uva.nl/id/801197e8-e374-4128-aeb3-c66c06cb8812)).
- **Subjective value pays later:** round-1 subjective value predicted better round-2 individual and joint
  outcomes, and the wish to renegotiate with the same partner (Curhan, Elfenbein & Eisenkraft 2010, JASP
  40:690-709).
- **AI negotiators:** warmth raised the deal rate and the counterpart's subjective value. Dominance claimed more
  given a deal but created less. There was no interaction
  ([Vaccaro et al., arXiv 2503.06416](https://arxiv.org/pdf/2503.06416)). Feigned desperation gained about 20%
  against GPT-4 (Bianchi et al. 2024). We cannot use it, because it would be an unverifiable claim.
- **Procurement chatbot:** competitive prompting won price, payment terms and speed. Collaborative prompting won
  trust and willingness to work together again (Herold, Heller, Rozemeijer & Mahr 2025, JPSM 101012,
  [record](https://cris.maastrichtuniversity.nl/en/publications/brave-new-procurement-deals-an-experimental-study-of-how-generati/)).
- **For us:** the score counts only C, and the `stabilise`/`secure_supply` relationship term already prices the
  squeeze below a sustainable margin. So the numbers stay hardline down to the relationship-adjusted optimum,
  and the tone stays warm (gives deals, costs nothing per Vaccaro).

## Constants with literature backing

| Parameter | Suggested value / range | Source |
|---|---|---|
| Boulware beta (time curve) | 0.5-1.0 for <= 10 rounds. Linear (1.0) was the most robust | Faratin et al. 1998: Boulware {0.01, 0.2} made fewer deals, Linear dominated. Our 0.5 is mild Boulware |
| Conceder beta (reference only) | {20, 40} | Faratin et al. 1998, experiment table |
| kappa (opening share of range) | 0.1 in experiments. We set the opening by the midpoint rule instead | Faratin et al. 1998 |
| NegMAS time-based defaults | Boulware exponent 4, Conceder 0.25 (aspiration exponent; mapping to 1/beta not verified) | [NegMAS docs](https://negmas.readthedocs.io/en/latest/modules/sao.html) |
| Relative TfT delay delta / window gamma | delta = 1, gamma = 1-2 | Faratin et al. 1998 (RE: delta = 1; RA: delta = 1, M in {1, 3}) |
| `AC_next` alpha, beta | alpha = 1-1.03, beta = 0-0.2 (most common: 1, 0. IAMhaggler/Bayesian agent: 1.02/1.03) | Baarslag et al. 2014, Table 2 |
| `AC_combi` T | 0.98-0.99 (no significant difference), i.e. the last round of 10 | Baarslag et al. 2014 |
| `AC_combi` alpha | `MAX^W` (best supplier offer in the previous equal-length window) | Baarslag et al. 2014: score 0.762, at least 12% over `AC_next` |
| `AC_const` / `AC_time` (do not use alone) | 0.88-0.9 / 0.92 | Baarslag et al. 2014, Table 2 (IAMhaggler, Agent Smith) |
| Number of simultaneous sheets (MESO) | 3 | Medvec & Galinsky 2005 (practitioner). The arena maximum is also 3 |
| Bolstering range width (prose only) | 5-25% above the point offer | Ames & Mason 2015, via a Columbia summary (second-hand) |
| Opponent predicted concession (Bayesian likelihood) | linear, `u' = 1 - 0.05 t`, Gaussian sigma = certainty | Hindriks & Tykhonov 2008 |
| Probe phase before classifying the opponent | concede steadily to about 0.9 of own utility range in the first bids | Gahboninho, ANAC 2011 (Baarslag et al. 2013) |
| First-offer precision | non-round price (e.g. cents not ending in 0/5) | Mason et al. 2013 (no numeric threshold given) |
| Late concession taper | decreasing steps at a "moderate" rate. No number published that we could verify | Tey et al. 2021 (Study 4a: too slow or too fast weakens the effect) |

## Open questions / ideas to test

- **Taper vs Boulware:** compare the current `(t/T)^2` steps against "same cumulative curve, decreasing steps after
  the target" in `arena-sim` and the sandbox. Measure supplier counter-moves after our step (Tey predicts smaller
  supplier ambition).
- **Relative vs absolute TfT:** with an inflated supplier opening, does absolute matching leak value? Test on
  mutated cases with high openings (`tests/test_tools_robustness.py` style), not on practice scores.
- **`AC_next` eps:** in cost terms, should eps be 0 or 1-2% (alpha 1.02)? Choose the least-bad value across
  case types. Every accept must still pass the guard's BATNA and must-have checks.
- **Opponent classification à la Gahboninho:** after 2-3 supplier moves, classify it as conceder or hardhead
  (variance and size of its moves). Then become stingier against conceders and move faster against hardheads.
  This is the evidence-backed answer to the teacher/learner dilemma.
- **Similarity pick for one variant:** make one equal-cost sheet the iso-cost point nearest to the supplier's
  last sheet (Faratin 2002). Track how often the supplier picks it.
- **MESO choice as data:** log which sheet the supplier counters from. Feed that to the frequency model to get
  per-lever priorities for the next round's variants.
- **Deadline disclosure (Moore 2004):** add one Writer line such as "we need to decide by Friday". It is cheap,
  is not a data claim, and can be tested in the sandbox.
- **Range phrasing in prose:** "we can work in the 4.31-4.37 region" alongside a sheet at 4.31 (bolstering). Risk:
  the bot settles at the top of the range. Test before adopting.
- **When to say "final":** once per thread, only when `p_room` is low and we are at our relationship-adjusted
  limit. Measure how often suppliers accept within one round of it.
- **RFQ convergence:** after how many rounds should we focus on the cheapest bidder and send a take-it-or-leave-it
  sheet (Shachat & Tan)? Keep the runner-up active with minimal moves, so we still have a BATNA.
- **Unverified items to check if time allows:** Orr & Guthrie's r = 0.497 (blog citation only), the details of
  Jap 2007 and Hilty & Carnevale 1993 (secondary sources only), and Nice TfT's Nash-point mechanism.
