# Real-world B2B / industrial procurement negotiation practice

**TL;DR.** Practitioner guidance (McKinsey, Roland Berger, ISM, Karrass, IndustryWeek, UK university buying
consortia, Tacto's own Defender Agent material) agrees on one loop. **Separate price from cost**: break the
supplier's claim down into its cost drivers and shares, reprice each share with a public index for the same
reference period, and accept only the justified part ("the supplier should not present a new price which yields
them a higher margin than they were previously achieving"). **Then move the fight off unit price.** The effective
date, phasing, a temporary surcharge with a rollback trigger, a capped symmetric index clause, payment days,
volume or contract length, forecasts, and lead-time or allocation guarantees are all things a supplier can grant
without "touching its list price". **Concessions are traded, never given**, and offered as 2-3 equivalent packages
(MESOs), which research shows beat single offers on both outcome and relationship. **Pick the lever by the
supplier's situation**: a cash-strapped supplier gets cooperative levers (volume, duration, earlier payment, supply
chain finance); a supplier with room to move gets confrontational ones (deadlines, longer payment terms, re-sourcing).
**Do not over-squeeze strategic or bottleneck suppliers.** A 7-year panel of 1,700+ automotive purchasing instances
found that suppliers forced into price concessions cut quality, service and R&D for that customer the next year,
and that fair process ("organisational justice") softened this. Vendor numbers (Pactum/Walmart: 64-68 % of
suppliers closed, 1.5-3 % savings, about 35 extra payment days) say that long-tail suppliers accept term trades
far more often than price cuts.

## Key takeaways for our agent

1. **`tools/claims` + `tools/signals`: answer an increase with a reprice of the supplier's own shares, never with a
   bare "no".** Justified increase = sum over drivers of (share x index change over the matching period). Quote the
   index ID and the period. Accept the justified part openly; it buys credibility for refusing the rest (Tacto's
   Defender Agent works this way: "test every demand, accept the justified ones, counter the rest with concrete
   facts"). Ask the supplier for a cost breakdown and the index it uses. Pin it to *its* stated shares
   (`stated_fair_price`), not to ours.
2. **`tools/levers`: rank the non-price trades by what they cost us versus what they are worth to the
   supplier.** A trade creates value when the supplier values it more than we do. Example: paying earlier when the
   supplier's cost of funds is above our case rate. Two numbers per lever: `eur_to_us` (from `total_cost`) and
   `eur_to_supplier` (an estimate from the persona: cost of capital, capacity load, fixed-cost absorption). Prefer
   levers where the second number is larger than the first.
3. **`tools/levers`, timing levers: effective date and phasing are cheap for the supplier and worth real money to
   us.** The brief prices units bought before the new price takes effect at the current price. A delayed
   effective date, a phased increase or a shorter review cycle "can materially reduce annual impact" while the
   headline increase stays intact (Karrass). So it is the first trade to offer a supplier that "cannot touch its
   list".
4. **`tools/bands` + Strategist, index clauses: accept only a weighted, capped, symmetric clause with a dated base
   period. Refuse on a rising index path.** An index clause moves the supplier's material risk onto us. Index only
   the material share (P = P0 x (a + b x I_t/I_0), with a the fixed share), with a threshold, a cap and a
   downward leg. When the case's index path rises, a fixed price (even a little higher) usually beats the clause.
   Compute both with `total_cost`. Replays showed index clauses accepted on rising paths as a loss source.
5. **Strategist: send multiple equivalent simultaneous offers (MESOs).** 2-3 term sheets of equal value to us
   (for example lower price at 30 days, mid price at 60 days, higher price plus 24-month volume commitment). The
   supplier's pick reveals its priority (cash, volume, list price). Leonardelli et al. (2019, six experiments)
   found MESOs beat a single package offer on economic and relational outcomes.
6. **Profiler: classify the supplier by situation before choosing levers** (Roland Berger's split). *Under
   financial strain*: commit volume, extend duration, finance supply, pay in advance or earlier. *Has room to
   manoeuvre*: speed up the schedule, lengthen payment terms, threaten re-sourcing. Signals: dunning or cash remarks,
   capacity talk ("our lines are full" vs "we need the work"), approval talk ("I need to check with management"),
   remarks on its quotes.
7. **Strategist + `tools/bands`, Kraljic quadrant from the case goal.** `leverage`/`routine`: competition,
   should-cost, frequent re-bids, push to the floor. `strategic`/`stabilise`: joint cost reduction, longer
   contracts, never price below a sustainable margin. `bottleneck`/`secure_supply`: buy predictability
   (allocation, lead time, safety stock) and accept a smaller cut for continuity (Karrass, CIPS, Kraljic).
8. **`tools/concession`: concede in shrinking steps, and only for a get.** Build a give-get table ("if we accept a
   short-term surcharge, we require automatic rollback triggers"). Our sandbox replays already show that
   price-only steps make the supplier say "without movement on your side...".
9. **`tools/claims`, rival quotes: compare on total cost, not on unit price.** Cite a rival's *package* (price,
   payment, Incoterm, lead time) by data ID. A rival quote only counts if it is credible: a qualified supplier,
   the same spec, and switching costs counted in (ADL Prism; PON on sole suppliers). Never invent or round up a
   rival figure.
10. **Writer: formal, factual, direct, and fair in process.** German B2B mail reads like a letter: "Sehr geehrte/r
    ...", *Sie*, a clear "no" without hedging, numbers with their source, a defined next step and date. Fair
    process (explaining *why*, acknowledging the supplier's cost pressure, keeping commitments) is what lowers the
    retaliation found by Carnovale et al. (2019). Thank the supplier for the moves it has made. Threats only as
    a calm statement of our alternative.
11. **Quality cases (`tools/claims` scorecard + levers): turn the red scorecard into contract terms**, not into
    insults. Ask for an 8D corrective-action plan with dates, a PPM target with chargeback per defect above it,
    containment at the supplier's cost, and recovery of past costs. Price relief can be one form of recovery
    ("credit note against the next N deliveries").
12. **`dossier`: read the history.** "Be a historian": earlier increases we absorbed, commitments in the existing
    contract, notice periods, past POs at lower prices. All of these are legitimate and checkable arguments
    (IndustryWeek tip 6). A family firm remembers how it was treated, so an earlier concession of ours is a debt
    we can call in.

## Playbooks by case type

### A. Price-increase letter ("+9.5 % due to energy and wages")

**Sequence**
1. *Acknowledge and ask* (round 1): thank the supplier, say we take the pressure seriously, and ask for (a) the
   cost breakdown, (b) the indices and periods behind each driver, (c) the effective date rationale (Karrass). Do
   not counter with a number in round 1 if we lack its shares: a number-only reply lets its figure set the frame
   (Shapiro). If we already have the shares from the letter, go straight to step 2.
2. *Reprice its own shares*. Example (all numbers hypothetical): the supplier claims material 45 %, energy 10 %,
   labour 30 %, other/margin 15 %. Verified changes: material index -2 %, energy index +12 %, wage tariff +4 %. The
   justified increase is 0.45 x (-2) + 0.10 x 12 + 0.30 x 4 = -0.9 + 1.2 + 1.2 = **+1.5 %**, not +9.5 %. The
   "other/margin" share does not inflate automatically. Also ask what the supplier did to offset the increase
   itself (productivity, energy hedging), and whether its proposal includes a return to lower prices if costs
   fall (SUPC).
3. *Offer the justified part plus a structure* as MESOs:
   - (i) +justified % from a later effective date;
   - (ii) +justified % now, but as a separate surcharge line with an index-linked rollback (IndustryWeek tip 4:
     "show the increase as a separate line item and keep the base pricing unchanged");
   - (iii) fixed price for 12-24 months at a slightly higher figure, in exchange for volume, forecast or longer
     payment terms.
4. *Close on terms*, with the effective date, review date and rollback trigger written into the term sheet.

**Levers**: effective date, phasing, cap, surcharge with sunset, applying the increase only to orders placed
after a date (IndustryWeek tip 5), carving out SKUs (put the increase on low-volume items, protect high-volume
ones), volume or duration for price protection, earlier payment for a cash-tight supplier.
**Pitfalls**: accepting a blanket increase across items with different material content (IndustryWeek: a single
component's increase spread over a whole assembly); arguing with indices that do not match the supplier's drivers
or period (the arena will mark them `irrelevant` or `contradicted`); accepting an index clause plus an increase
(double recovery); squeezing a `stabilise` supplier below its sustainable margin.

### B. Expiring framework contract / renewal

**Sequence**: review the old contract and its performance first (prices paid, OTD/PPM, increases absorbed) →
state our need and horizon → open with a package that rewards continuity (longer term, volume commitment,
forecast) in exchange for price and index protection → MESOs on duration vs price vs payment days → close before
expiry. Do not let the clock run out, because the fallback price is usually worse.
**Levers**: contract length (the supplier values security of utilisation and financing; PON on sole suppliers:
"could the supplier benefit from longer contractual commitments to secure better financing or volume
guarantees?"), volume tiers, forecast sharing in return for lead-time guarantees, payment terms, index clause
design, cancellation terms.
**Pitfalls**: a short renewal that covers only part of the need. The brief's example shows that uncovered need at
the fallback price can make a "cheaper" deal score 0. Check coverage first. Also avoid automatic renewal with an
open-ended escalation clause.

### C. RFQ with 2-3 competing suppliers

**Sequence**: normalise every quote to total cost C (price, payment, Incoterm/freight, lead time, PPM, tooling,
index clause) → keep **every thread alive within its 60 s clock** (losing a bidder kills our leverage; ADL notes
that a buyer left with one supplier before the big issues are settled "has no leverage") → tell each bidder where
it stands on total cost without disclosing the rival's identity, citing quote IDs → invite a best-and-final
package → award on C.
**Levers**: rival packages cited by ID, volume split vs single award (a supplier will pay for the whole volume),
contract length for the winner, payment days, lead time.
**Pitfalls**: comparing unit prices only; bluffing about a rival figure (it gets checked by ID, and `contradicted`
costs trust); ignoring the incumbent's switching-cost advantage (qualification, tooling) or the newcomer's risk;
pushing a near-monopolist bidder with competition it knows is not credible.

### D. Quality problem (red scorecard)

**Sequence**: state the facts from the scorecard/notifications by ID (PPM, OTD, notification count) → ask for
root cause and an 8D/corrective-action plan with dates (Oshkosh's supplier procedure: "an effective 8D must drive
permanent improvement of supplier processes") → convert to contract terms: PPM target, chargeback per defect
or per notification, containment and sorting at the supplier's cost, a credit for past costs → offer continuity
(no re-sourcing, a review date) if the plan is credible.
**Levers**: chargeback rate, PPM commitment, warranty, credit notes, re-sourcing threat (only if a qualified
alternative exists), future volume conditional on scorecard recovery.
**Pitfalls**: chasing every claim (Claimlane: thresholds, not principle); a chargeback a supplier will just price
back in (forum practitioners: "the supplier will increase his prices to compensate"), so value PPM x defect cost
- chargeback with `total_cost`; humiliating tone with a supplier we still need.

### E. Secure supply / stabilise (strategic or bottleneck supplier)

**Sequence**: acknowledge dependency honestly → ask what the supplier needs to stay healthy and allocate capacity
to us → buy predictability rather than price: allocation priority, lead-time SLAs, safety stock or consignment,
escalation paths (Karrass: "accept a smaller increase in exchange for continuity protections") → offer the trades
that improve its economics at low cost to us (earlier payment, longer contract, firm forecast, tooling
co-investment) → keep the supplier's margin at or above its sustainable level.
**Why**: suppliers allocate scarce resources to preferred customers (Pulles, Veldman & Schiele 2016, 163 buying
firms: preferential resource allocation mediates the buyer's competitive advantage). OEMs with better supplier
relations get "more favourable pricing, earlier access to new technology, and preferential capacity allocation"
(Plante Moran WRI, secondary summary). Squeezed suppliers cut quality, service and R&D (Carnovale et al. 2019).
**Pitfalls**: applying leverage-quadrant tactics (e-auction tone, re-bid threats) to a near-monopolist; pricing
below `full_cost x (1 + sustainable margin)`, which the arena charges for directly.

## Valuing levers in EUR

Notation: P unit price, Q committed units over the horizon, r_b our case interest rate (published per case),
r_s the supplier's cost of funds (estimated), d days.

| Lever | EUR to us (arena C) | EUR to the supplier (estimate) | Source / default |
|---|---|---|---|
| Payment days (Δd later) | saves P x Q x r_b x Δd / 365 vs reference | costs it P x Q x r_s x Δd / 365 | brief section 6 example; time value of money |
| Pay earlier (Δd earlier) | costs P x Q x r_b x Δd / 365 | worth P x Q x r_s x Δd / 365 | a trade only if r_s > r_b (cash-hungry supplier) |
| Early-pay discount d % for paying N-D days early | | implied APR = d / (100 - d) x 365 / (N - D) | 2/10 net 30 = 37.2 % (365-day) / 36.7 % (360-day) |
| Effective-date delay (t days) | ΔP x units bought before the new date | the same EUR (a pure transfer, but the "list" stays intact) | brief: units before the new price take effect at the old price |
| Phasing (k steps) | ΔP x Σ units in each step x (1 - step share) | same | Karrass |
| Index clause | Q_remaining x P x b x E[ΔI/I_0] on the case path, plus the goal's index risk | removes its material risk | a+b formula; Tacto glossary example: 60 % weight, 5 % threshold |
| Cap on index clause | removes the upper tail above the cap | | Tacto glossary (illustrative 15 %/yr, both directions) |
| Volume / contract length | avoids uncovered need at fallback price + fallback risk | contribution margin x extra units; utilisation, financing security | brief section 6; PON |
| Lead time (days beyond need) | € per day beyond need (case rate) | cost of expediting / holding stock | brief; holding cost 15-25 %/yr of stock value (APICS rule of thumb) |
| PPM / chargeback | expected defects x cost per defect - chargebacks | cost of quality work vs risk of paying chargebacks | brief |
| Relationship (stabilise / secure supply) | € per margin point below sustainable margin | its survival / willingness to allocate | brief; Carnovale 2019 |

**Formulas and defaults**

- *Payment terms, gains from trade.* Our cost of moving payment Δd days earlier is `P*Q*r_b*Δd/365`. The supplier
  gains `P*Q*r_s*Δd/365`. Any unit-price cut between `P*r_b*Δd/365` and `P*r_s*Δd/365` leaves both better off.
  Example (hypothetical): P = 40, Q = 8,000, Δd = 30, r_b = 8 % → our cost is EUR 2,104 (EUR 0.26/unit). If
  r_s = 15 %, the supplier gains EUR 3,945 (EUR 0.49/unit). Ask for a cut of about EUR 0.40/unit.
- *Supplier cost of funds r_s (no hard default in the literature).* Reference points: the average German
  corporate lending rate was about 3.5 % in Q3 2025 (KfW Research, secondary report). The SME "credit hurdle" hit
  a record 37.8 % in Q4 2025 (KfW-ifo), so credit is rationed for many Mittelstand firms. Factoring costs a fee of
  roughly 0.5-5 % of the invoice plus interest; one worked example uses a 1 % fee + 8 % p.a. ≈ 1.9 % of a 45-day
  invoice, i.e. ≈ 15 % annualised (single example, not a market average). A supplier that *offers* skonto is
  revealing that it values cash at ~37 % APR (2/10 net 30). Suggested defaults, to tune only on behaviour:
  healthy group ≈ r_b; Mittelstand family firm ≈ r_b + a few points; cash-hungry job shop ≥ factoring-implied
  ~15 % (unverified as a general rate; derived from the single example above).
- *Legal ceiling on payment days (Germany).* § 271a BGB: payment terms over 60 days between businesses are valid
  only if expressly agreed and "not grossly unfair" to the creditor. In standard terms (AGB), more than 30 days is
  presumed unfair (§ 308 Nr. 1a BGB, per a law-firm note). Late payment interest is base rate + 9 points
  (§ 288 (2) BGB). So asking a Mittelstand supplier for 90-120 days is legally and relationally aggressive. The
  Writer should justify it or trade it.
- *Reverse factoring / supply chain finance.* Lets the supplier be paid early at the buyer's credit rating while
  the buyer pays late. ZLC research says SMEs may unlock >10 % of working capital, but that pairing it with a
  terms extension lowers total chain value unless the buyer's own funding cost is high (MIT CTL newsletter). In
  the arena it shows up as "pay earlier / later" only, so value it through r_s vs r_b.
- *Index clause.* `P_t = P_0 x (a + b x I_t / I_0)`, a + b = 1, b = the supplier's material (or energy) cost share
  (Tacto: "reflect the actual raw material share in the end product"). Add a trigger threshold (sources cite 5-10
  %), a cap or collar, symmetric application, a dated base period (Freshfields: "sufficient clarity relating to the
  base price"), a named public index (Destatis producer price index / GENESIS series, LME for non-ferrous, EEX for
  power), and a review cadence. Expected cost to us = `b x P_0 x Σ_t Q_t x (I_t/I_0 - 1)` on the case's index path.
- *Should-cost.* Bottom-up cost (material at index price x net weight / yield + energy + labour time x rate +
  overhead + margin). McKinsey's Cleansheet splits the gap into does-cost / should-cost / could-cost and moves the
  conversation "away from relative price changes toward fact-based bottom-up estimates". Their brochure shows
  13 % in one illustrative example (not a benchmark). Foundries: energy + materials ≈ 40 % of costs, personnel
  similar (Magma, one source; shares vary widely by process).

## Supplier archetypes and what each wants

| Persona (signals in data / e-mails) | Situation | Wants | Our best trades | Avoid |
|---|---|---|---|---|
| **Family foundry / Mittelstand firm** (owner signs, long history, call notes about loyalty, "we have supplied you for 20 years") | price-sensitive to energy and wages; remembers treatment | security of volume, fairness, being paid on time, respect | longer contract, firm forecast, earlier payment, acknowledging past concessions, phased increase | ultimatums, citing rivals aggressively, 90+ day terms; the relationship is a priced asset in C |
| **Group with approval ladder** ("I need approval", "above my authority", standard T&Cs, list price policy) | the rep has a discount band; bigger moves go up the ladder (vendor deal-desk matrices escalate at roughly 5-15 % / 15-30 % thresholds, illustrative) | a deal its boss signs; list price preserved | non-list levers: payment days, effective date, rebate or volume bonus, free tooling or freight, index cap; give the rep an argument to take upstairs | demanding list-price cuts beyond the rep's band in one step; expect a delay round when a move needs approval |
| **Cash-hungry job shop** (dunning mentions, "cash flow", asks for deposit or shorter terms, spare capacity, "we need the work") | short of liquidity, under-utilised | cash now, utilisation | pay earlier (only if r_s > r_b), skonto-style price cut, volume/backlog, advance payment for tooling (Roland Berger "cooperative" levers) | long payment terms (they hurt it the most and may break it); squeezing below cost floor |
| **Near-monopolist** (unique spec/tooling, "take it or leave it", few moves, full order book) | strong BATNA; barely moves | predictability, low effort, its list | ask for predictability (allocation, lead time, safety stock); a small price move for a long commitment; build credible alternatives over time (PON: "invest now to create competition for the next negotiation"; Kearney: change the nature of demand/spec) | bluffing a rival it knows is not qualified; long fights for small euros |
| **Underperformer (red scorecard)** (PPM/OTD misses, quality notifications) | at risk of losing the account; defensive | to keep the business | 8D plan + PPM target + chargeback; credit notes; continuity conditional on recovery | price cuts it cannot fund (it cuts quality further: Carnovale 2019) |
| **Bidder hungry for share (RFQ newcomer)** | wants a foothold | the award, reference customer | single award or volume split, contract length, rival package cited on total cost | taking an unsustainable low bid in a `secure_supply` case |

**Steele & Court supplier preferencing (the supplier's view of us):** suppliers rank accounts by attractiveness
(profitability, growth, payment behaviour) and relative value. *Core* accounts get priority service, *development*
accounts get investment, *exploitable* accounts get premium prices, and *nuisance* accounts get neglected (CIPS).
Paying reliably, forecasting well and being easy to deal with move us toward "core", and that is currency too.

## Sources

- McKinsey, *Responding to inflation and volatility: Time for procurement to lead* - indexing, should-cost, nerve
  centre; a basic-materials company cut a major supplier's increase by >20 %.
  https://www.mckinsey.com/capabilities/operations/our-insights/responding-to-inflation-and-volatility-time-for-procurement-to-lead
- Supply Chain Digital summary of McKinsey on supply-driven price hikes - check whether the increase is in line
  with the supplier's costs. https://supplychaindigital.com/supply-chain-risk-management/mckinsey-warns-firms-supply-driven-price-hikes
- McKinsey Cleansheet, *When the price isn't right: using should-costs to reduce does-costs* (PDF) -
  does/should/could-cost. https://www.mckinsey.com/~/media/mckinsey/business%20functions/operations/how%20we%20help%20clients/cleansheet/overview/when-the-price-isnt-right-using-should-costs-to-reduce-does-costs-vf.pdf
- Roland Berger, *Managing supplier claims for price increases* - differentiate supplier situations; cooperative
  vs confrontational levers; clawback. https://www.rolandberger.com/en/Insights/Publications/Managing-supplier-claims-for-price-increases-in-the-procurement-process.html
- Karrass, *Procurement negotiation: price increases* (2026) - effective date, phasing, caps/floors, give-get
  table, sole-source predictability. https://www.karrass.com/blog/procurement-negotiation-price-increases
- IndustryWeek, *Hit with a price increase? Seven tips* - unpack the BOM, carve out SKUs, temporary line item,
  timing, "be a historian". https://www.industryweek.com/supply-chain/supplier-relationships/article/21160288/hit-with-a-price-increase-seven-tips-for-negotiating-with-suppliers
- SUPC (UK universities purchasing consortium) - divorce price from cost; no higher margin than before; ask for a
  return to lower prices. https://www.supc.ac.uk/?p=7755
- ISM, *Inflationary impacts: pushing back on supplier price increases* (2021, member login) - fact packs, cost
  models, index comparison. https://www.ismworld.org/supply-management-news-and-reports/news-publications/inside-supply-management-magazine/blog/2021/2021-08/inflationary-impacts-pushing-back-against-suppliers-price-increases/
- Shapiro Negotiations - do not open with a counter-number; test the supplier's logic first. https://www.shapironegotiations.com/?p=33527
- Tacto, Defender Agent webinar (Wölfle) - ERP + contracts + indices → counter with index values for the
  reference period and contract clauses; accept justified increases. https://www.tacto.ai/de/resources-posts/webinaraufnahme-preiserhoehungen-mit-ki-agenten-abwehren-wie-woelfle-preiserhoehungen-automatisch-abwehrt-und-mit-marktdaten-kontert
- Tacto glossary, *Raw material price adjustment clause* - weighted/threshold models; LME example 60 % weight, 5 %
  threshold. https://www.tacto.ai/en/procurement-glossary-downloads/raw-material-price-adjustment-clause
- Tacto glossary, *Escalation clause* - symmetric cap example. https://en.tacto.ai/en/procurement-glossary/escalation-clause
- Tacto lexicon, *Preisverhandlungstechniken* - BATNA, should-cost, volume-for-price (vendor claims 5-15 %
  savings, unsourced). https://www.tacto.ai/de/einkaufer-lexikon/preisverhandlungstechniken
- Freshfields, *Price escalation clauses on major projects* - base price clarity, index weighting. https://riskandcompliance.freshfields.com/post/102hzuu/dealing-with-supply-cost-volatility-on-major-projects-the-use-of-price-escalatio
- Porter Hedges - triggers and notice in escalation clauses. https://www.porterhedges.com/texas-construction-law/price-escalation-considerations-in-construction-contracts
- WKO (Austrian chamber) on Preisgleitklauseln - Destatis producer price index as a reference index. https://www.wko.at/gewerbe-handwerk/metalltechniker/anwendung-von-preisgleitklauseln-bei-preiserhoehungen
- § 271a BGB text - 60-day limit on B2B payment terms. https://gesetze.legal/bund/bgb/271a ; law-firm summary
  https://www.it-recht-kanzlei.de/zahlungsverzug-unternehmer.html
- NOAA, *Determining cost effective discounts* - discount APR table (2/10 net 30 = 36.0 % simple, 360-day). https://noaa.gov/node/6196
- Dummies, discount APR formula. https://www.dummies.com/article/how-to-use-financial-reports-to-decide-if-discount-offers-make-good-financial-sense-157438
- MIT CTL Supply Chain Frontiers #62 - reverse factoring; terms extension lowers chain value unless the buyer's
  funding cost is high. https://ctl.mit.edu/pub/newsletter/supply-chain-frontiers-62-how-delayed-payments-can-be-win-win-buyers-and-suppliers
- KfW Research press releases (credit hurdle record 37.8 % Q4 2025). https://www.kfw.de/Über-die-KfW/Newsroom/Aktuelles/Pressemitteilungen-Details_877312.html
- Factoring cost overview (DE) - 0.5-5 % fee; worked example ≈1.9 % for 45 days. https://kostenlose-erechnung.de/ratgeber/skonto-vs-factoring-kmu-vergleich/
- Pactum, Campaigns agent - Walmart: about 35-day payment-term extension, early-pay discount options as the
  trade (vendor figures, 68 % / 72 % conflict). https://pactum.com/campaigns-agent/
- PYMNTS / Bloomberg on Walmart + Pactum - 68 % close rate, ~3 % savings; 75 % of vendors preferred the bot. https://www.pymnts.com/news/artificial-intelligence/2023/walmart-finds-75-percent-vendors-prefer-negotiating-with-chatbot/partial/
- Fortune (2021) on Pactum - value functions; trade-offs and red lines set up front; up to 30 tradable items per
  use case (freight, warehousing, contract length, cancellation terms, growth rebates). https://www.fortune.com/2021/04/27/a-i-startup-pactum-negotiating-software-saving-walmart-money
- HBR store, *Pactum's AI in contract negotiations: Walmart and Maersk* (case, not read). https://store.hbr.org/product/pactum-s-ai-in-contract-negotiations-walmart-and-maersk/TB0756
- Carnovale, Henke, DuHadway & Yeniyurt (2019), *Unintended consequences: how suppliers compensate for price
  concessions*, J. Business Logistics 40(3) 187-203 - 1,700+ instances; quality/service/R&D cuts; justice
  mitigates. https://pdxscholar.library.pdx.edu/busadmin_fac/151
- Plante Moran Working Relations Index - supplier relations vs commercial outcomes. https://www.plantemoran.com/explore-our-thinking/areas-of-focus/working-relations-index-study
- Pulles, Veldman & Schiele (2016), IJOPM, *Winning the competition for supplier resources*. https://www.emerald.com/insight/content/doi/10.1108/IJOPM-03-2014-0125/full/html
- Schiele, Calvi & Gibbert (2012), IMM 41(8), customer attractiveness / preferred customer status. https://eprints.glos.ac.uk/2198/
- CIPS, Kraljic matrix and Supplier preferencing matrix. https://1prd-dxp.cips.org/intelligence-hub/supplier-relationship-management/kraljic-matrix ;
  https://1prd-dxp.cips.org/intelligence-hub/supplier-relationship-management/supplier-preferencing
- Kearney, *The Purchasing Chessboard* - high supply power → change the nature of demand (specs). https://www.kearney.com/procurement/the-purchasing-chessboard/strategies
- PON (Lax), *Gain greater leverage with sole suppliers* - setup moves, longer commitments, create future
  competition. https://www.pon.harvard.edu/daily/gain-greater-leverage-with-sole-suppliers
- PON on MESOs; Leonardelli et al. (2019) OBHDP 152. https://www.pon.harvard.edu/daily/business-negotiations/how-to-use-mesos-in-business-negotiations ;
  https://researchers.mq.edu.au/en/publications/multiple-equivalent-simultaneous-offers-mesos-reduce-the-negotiat/
- Mason et al. (2013) *Precise offers are potent anchors* (preprint) - precise first offers draw smaller
  counteroffers. https://drupalgsb-test.paas.cc.columbia.edu/sites/default/files-efs/pubfiles/5957/precise_offers.pdf
- ADL Prism (2009) *Effective procurement* - competition + total value of ownership. https://www.adlittle.com:443/sites/default/files/prism/ADL_Prism_1_2009_Effective_procurement.pdf
- Oshkosh 8D corrective action procedure. https://osn.oshkoshcorp.com/-/media/OSN/supplier-resources/Training/SQ-Core-Tools/8D-Corrective-Action/8D-Corrective-Action-Procedure.pdf
- Claimlane on supplier chargebacks and thresholds (vendor blog). https://www.claimlane.com/resources/blog/supplier-chargebacks-recovering-warranty-costs
- Magma (2012) energy and raw-material efficiency - energy + materials ≈ 40 % of foundry costs. https://www.magmasoft.com/export/shared/.galleries/pdfs_publications/2012_Energy-and-Raw-Material-Efficiency_engl_A4.pdf
- APICS chapter blog - carrying cost rule of thumb 15-25 %/yr. https://apicsprsjorg.starchapter.com/blog/id/6
- WEDC, *Understanding German business culture* - formality, directness, data-driven decisions. https://wedc.org/market-intelligence/understanding-german-business-culture-and-communication-customs

*Not verified / gaps*: no Destatis series code for cast parts was confirmed (search GENESIS for the GP product
code); no independent study ranks which concessions suppliers accept most often (Pactum data is vendor-reported);
supplier cost-of-capital defaults above are reasoned from single data points, not a benchmark.
