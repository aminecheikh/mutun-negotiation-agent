# Negotiation playbook - what the data says, and how to turn it into agent skills

Source: data package v1 (as of 2026-09-30). Everything here was computed from `data/` - the
queries are reproducible. **Status: hypotheses until confirmed by practice replays.** Mark each
rule ✅ / ❌ as replays confirm or refute it.

---

## 1. The key finding: suppliers speak a closed vocabulary

The 124 meeting notes, call notes, letters and memos are **not free text**. They are built from
about 12 fixed sentence templates, and the quote remarks from 6 more. Every detailed supplier gets
a few of them. The hidden final has new suppliers "in the same world, with the same case types and
rules", so **the same templates will describe them**.

So the core skill is: **detect the templates → map to an archetype → apply that archetype's
levers.** Detection is plain string matching, done in code (cheap, reliable, no LLM).

Some notes are noise by design (holiday shutdown, packaging, ISO certificate, trade fair, new
contact person, news topic `noise`). Ignore them.

---

## 2. Archetypes (signal → meaning → lever)

| # | Archetype | Exact trigger text (notes / quote remarks / news) | What it means | Lever: what to offer or ask | Don't |
|---|---|---|---|---|---|
| A | **Cash-starved** | "asked twice whether we could pay earlier … bank is watching" · "I cannot sign anything that lowers our margin now" · "Could you pay our invoices within 10 days? … cash discount" · quote: "Payment within 10 days requested." · news `restructuring`, `credit_watch`, `credit_downgrade`, `short_time_work` | Money now is worth far more to them than to us (our rate is 8%). | **Trade short payment days for price.** Offer 2-3 sheets: e.g. 60d at price X, 14d at X−a, 10d at X−b. Code checks each in C: we lose 8%/yr on early pay, they value it much more. | Don't squeeze margin - stabilise/secure_supply cases charge for that. |
| B | **Idle capacity** | "could take additional parts at short notice; second shift on hold" · news `free_capacity` ("looks for new customers") · "half the shop floor was idle" | Hungry for volume. | **Volume and contract length for price.** Commit to the full need over the full horizon. Push price hard. RFQ: cite rival quotes. | Don't reveal we need them. |
| C | **Uninformed owner** | "works from his own spreadsheet, last updated two years ago" · "had not followed his material and energy prices closely since the 2022 peak; his office manager does the costing" | Data **disarms** them ("we didn't know that"). | **Cite index facts** that undercut the increase, one per sentence with ID and %. Ask for a cost breakdown. | Don't cite indices that went *up* more than the ask. |
| D | **Open book** | "offered to walk us through their calculation (hourly rates and material)" | They will show costs. | **Ask for the cost breakdown XLSX** in round 1, then argue line by line against should-cost (`cost:<part>`). | - |
| E | **Partnership / memory** | "reminded us that in 2022 they kept supplying at the old price for a quarter and expects partnership in return" · negotiation_history note "accepted below full cost to keep the volume" · "important reference customer" | Relationship matters; often already priced **below** should-cost. Usually `stabilise`. | **Appreciation, security (longer contract, volume), non-price terms.** Settle near should-cost + a sustainable margin, not below. | Aggressive tone, threats, lowballing - backfire, and C adds a relationship charge. |
| F | **Hedged** | "bought their electricity / copper / nickel forward until <date> … market price is 'not our reality'" · letter "requirement is covered at fixed prices until <date>" · quote: "Material covered until <date>" · news `energy_contract` | Index arguments **don't move them** before that date. But their costs also **didn't rise** before it. | Use the **effective_date**: push any increase to after the hedge ends. Argue "your costs are fixed until X". | Don't waste claims on index moves of the hedged input. |
| G | **Committee / ladder** | "price changes above 2 % need the approval of their pricing committee, which meets every two weeks" | The sales rep can move **≤ 2 %** alone. Bigger = boss approval, slower and harder. | Ask for moves just under 2% per round. Bundle non-price terms. | Don't demand 8% in one step. |
| H | **Price freeze** | "head office has frozen all price reductions until December 2027. He was open on payment terms and call-off flexibility" · "price list applies to all customers without exception … happy to discuss logistics and payment" · quote: "discounts not possible this year (head-office directive)" | Unit price will not move. | **Win on other fields:** payment_days (longer = cheaper for us), Incoterm DAP, lead time, chargeback, ppm. | Don't burn rounds on price. |
| I | **Volume-only discounts** | "standard items are not discounted; only multi-year volume agreements change prices" | Price moves only for commitment. | Offer **max contract_months + full volume** in exchange for price. | - |
| J | **Not strategic / refocus** | "volumes are 'not strategic' … replies have become slower" · "reviewing small accounts … focus on customers with volume" · news `refocus` · quote: "List conditions; supply from stock only." · "individual agreements will not be extended" | Low interest in us, low flexibility. May walk. | Small asks. If there's an alternative or a rival quote, lean on it - or walk away if the alternative is cheaper in C. | Long pushing. Watch for `final_offer`. |
| K | **Fully booked** | news `full_order_book` · "cannot confirm your full forecast … will prioritise customers with firm, long-term commitments" · "confirm additional volumes only against a binding 12-month commitment" · news `big_order` | Seller's market. Capacity is the scarce thing. | Secure **coverage and lead time** first (uncovered need is very expensive in C). Offer firm commitment. | Don't lowball. They walk easily. |
| L | **Allocation / bottleneck** | news `allocation` ("deliveries … allocated in line with last year's call-offs") · chip_shortage | Supply risk dominates. Usually `secure_supply`. | Coverage, lead time, long contract. Price is secondary. | Don't squeeze margin (relationship charge). |
| M | **New owner / investor** | news `new_owner` ("plans to review prices and terms with all customers") | Expect pressure for increases. Professional and numbers-driven. | Data-driven arguments plus a rival quote. | - |
| N | **Succession / for sale** | news `succession` | Owner wants stable revenue to sell. | Long contract plus volume for price. | - |

A supplier often has **several** archetypes (e.g. Tarsevi: idle capacity, cash-starved, open book,
restructuring). Combine the levers. When two disagree, use the case's category goal to decide.

---

## 3. The market (index moves, last 12 months)

**Arguments that help the buyer** (falling, or flat versus the ask):
| Series | 12m | Note |
|---|---|---|
| PPI-D351 electricity | **−3.7%** | −43% vs the 2022 peak. Kills "energy costs" excuses. |
| PPI-D35 energy | +1.2% | −37% vs peak |
| WB-FEORE iron ore | −5.4% | |
| PPI-C2451 iron castings | +1.8% | Foundries asking +9.5% are far off. |
| PPI-C2594 fasteners | +1.7% | |
| PPI-C2711 motors | +1.6% | |
| PPI-C2815 bearings | +0.8% | |
| LCI-C labour | +2.7% | "Wage increases" only justify ~+2.7% × labour share. |
| ECB-HUF / ECB-CNY | −6.8% / −7.6% | EUR stronger, so HU/CN suppliers' costs fall in EUR. |
| AS-14301 / AS-14404 alloy surcharge | +8.6% / +5.9% | but −45% / −36% vs the 2022 peak |

**Arguments that backfire** (they rose a lot, so citing them helps the supplier):
CU-NOTATION copper **+47.7%**, WB-CU +45%, PPI-C2612 electronic boards **+40.8%**, PPI-C2611
components +13.5%, PPI-C2431 cold-drawn bars +12.3%, PPI-C2732 wires +8.9%, PPI-C241 steel
+6.5%, WB-NGEU gas +128.6%, SMARD-DA day-ahead power +73% (spot - but PPI-D351, what plants
actually pay, is −3.7%), **ECB-TRY +15.5%** (TRY weaker, so a Turkish supplier's EUR costs *fall*).

Claim rules (from api.md): 2 × `idx` of one series, ≤ 12 months apart, ending within the last 12
months of the data. Figure = `(later/earlier − 1) × 100`, ±0.5 pp tolerance. At most 3 claims per
message, one per sentence. Each ID counts once per case.

---

## 4. Fair-increase check (cost-driver weights × index moves)

`Σ weight × 12m change` from `cost_drivers`. This is what the data can justify:

| Letter | Asked | Index-justified | Verdict |
|---|---|---|---|
| Brandlhuber 70110, volute casing 310010 | +9.5% | **+2.3%** | Massive over-ask. But price is already **5% below should-cost** and they sold below cost 3 times. Strategic (`stabilise`), so don't push below cost. Uninformed owner (C) + partnership (E): disarm with data, settle near should-cost. |
| Lindlbauer 70230, coupling hub 320060 | +7.0% | **+4.6%** | Moderate over-ask. Open book (D): ask for the breakdown. |
| Nordmark 70510, motor 350011 | +6.5% | **+11.2%** (copper) | **Under-ask.** Citing indices helps *them*. Already 8% below should-cost. Accept a modest increase, trade terms, avoid an index clause on copper (index_path cost). |
| Platinenwerk 70550, PCBA 350030 | +8.0% | **+8.0%** | Exactly justified. 8% below should-cost, sold below cost 3 times. Don't fight price; win on payment, effective_date and terms. |

**Rule:** code computes this for every price-increase case before round 1. If asked > justified,
argue with the falling/flat drivers. If asked ≤ justified, don't argue indices - trade terms.

---

## 5. Hard per-supplier signals (computed, not prose)

| Signal | Where | Reading |
|---|---|---|
| Margin vs should-cost | `should_cost.unit_cost` vs latest `price_conditions.price` | Negative = already below cost. Don't squeeze (it walks or gets a relationship charge). +14-19% (seals groups 70410, 70420) = room. |
| Sold below cost before | `negotiation_history.note` | "Partnership" debt (E). |
| Past concession ratio | `negotiation_history` asked vs settled | Groups settled ~+0 to +2%, job shops ~−1 to −3%. That's the normal concession depth. |
| Credit index, now vs 12m ago | `supplier_intel` | Higher = worse (Creditreform-style scale). **70280: 230→302, 70350: 248→343** = deteriorating, so cash-starved (A). |
| Equity ratio < 25% | `supplier_intel` | Thin cushion, so cash matters (A). |
| How late *we* pay them | `invoices` paid − due | 70580 19d, 70280 15d, 70230/70350 12d, 70210 11d. They feel it (70210 wrote "overdue invoices … review our conditions"). **Promising on-time payment is a free concession.** |
| Scorecard red | `scorecards` latest | 70140, 70150, 70170, 70310, 70350: quality case. Cite `sc:`/`qn:` ppm, demand ppm commitment + chargeback. Chargebacks and ppm are in C. |
| Hedge end date | notes / quote remarks | Set the effective_date after it (F). |
| Owner type | `supplier_intel.owner_type` | `group`: ladder/committee/freeze (G, H). `family`/`founder`: personal, memory (E), cash (A). |

---

## 6. Category goal → default posture

| Goal | Posture | Watch in C |
|---|---|---|
| `stabilise` (castings, electrical) | Fair, long-term. Settle near should-cost + sustainable margin. | Relationship charge if margin < sustainable. Estimate `full_cost` from `cost:<part>`. |
| `secure_supply` (seals, capacity cases) | Coverage and lead time first, price second. | Uncovered need × fallback × (1+risk), late_cost_per_day, relationship charge. |
| `quality_first` (red scorecards) | ppm commitment + chargeback ≥ cost_per_defect. | Quality term dominates. |
| `leverage` (machining, sheet, raw material) | Push hard. Use competition and rival quotes. | Price. |
| `routine` (C-parts, kits) | Bundle, one supplier, low effort, payment/logistics. | Price + process. |

---

## 7. Turning this into skills

Following our rule (code decides numbers and term sheets, the LLM writes):

1. **`profile.py` (code):** for each case supplier, run the template matchers (section 2) over
   its notes, letters, news and quote remarks, plus the numeric signals (section 5). Output: a
   list of archetypes plus key facts (hedge date, should-cost margin, fair increase).
2. **`levers.py` (code):** archetypes + category goal → which fields to move and in what order;
   2-3 candidate term sheets, each priced with `total_cost` (with a `supplier` estimate in
   stabilise/secure_supply). Concession schedule (e.g. ≤ 2% steps for G).
3. **`claims.py` (code):** choose ≤ 3 verifiable claims per message that **help us** (section 3),
   pre-formatted as sentences with ID and figure. Never a rising series.
4. **LLM skill prompt (prose only):** per archetype, a short tone/content guide - e.g. E:
   "acknowledge the long relationship and 2022 support, then the facts"; A: "lead with the payment
   offer"; H: "accept that the list price is fixed; focus on payment and delivery". The LLM gets
   the chosen term sheets and claim sentences and only writes the e-mail around them.
5. **Accept/walk (code):** C of the standing sheet vs alternative vs our target; respect
   `final_offer`; read the quote PDF's standard terms.

---

## 8. Hypotheses to test in practice (in priority order)

1. Cash-starved suppliers give more price for short payment days than the 8% interest costs us.
2. Index claims move uninformed owners (C) but not hedged ones (F).
3. Group suppliers accept ≤ 2% steps quickly; bigger asks stall.
4. Partnership suppliers react badly to threats and well to appreciation (compare two replays).
5. Price-freeze suppliers concede payment_days / Incoterm.
6. In RFQs, citing a rival's `quote:`/`offer:` moves idle-capacity bidders most.

Log each practice run as: case, archetypes detected, levers used, score, what the replay's
"reasons" said.
