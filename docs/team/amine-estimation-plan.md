# Tools: estimating what we cannot see (Amine, A1-A5)

Three hidden quantities drive the bands and the offers. For each: the signals in our data, the method,
and how it is corrected live. Every estimate carries its source and an uncertainty, so the strategist and
the pitch can say *why*.

## 1. The supplier's full cost (floor, margin penalty)

| Signal | Data | Quality |
|---|---|---|
| Bottom-up model of **this supplier's plant** | `should_cost` with `supplier_id` (22 of 28 rows) | +-5 %; already in its own country's cost base |
| German cost base for the part | `should_cost` without `supplier_id` | +-5-15 %; scale by country: AT/CH 1.05, CZ/PL/HU 0.82, TR 0.75, CN 0.7 |
| Recent prices, rival quotes | `price_conditions`, `quotes`, `po_lines` | price = cost + margin: an upper bound |
| "accepted below full cost to keep the volume" | `negotiation_history.note` | its floor can sit **below** full cost |
| Cost breakdown XLSX (if we ask and it is open) | attachment in the thread | exact: replaces the estimate |
| Its concessions converging (see 3) | the thread | live correction |

**Method.** `full_cost` = should-cost (scaled if generic). Without a row: the lowest recent price for the
part (any supplier) x (1 - 8 %) typical margin, flagged low confidence. `floor` = full_cost, minus a
tolerance for suppliers with the below-cost note. Observed: P02's should-cost (374.95) is above today's price
(348.32) - squeezing that supplier is what cost EUR 139k in the baseline.

## 2. The supplier's cost of money (value of payment days and dates to it)

The relationship charge uses `supplier_value`, which needs the supplier's `interest_rate`: payment days and
the effective date are worth more to a cash-short supplier. We do not see it; we estimate it.

| Signal | Data | Reading |
|---|---|---|
| Its own early-payment discount | `suppliers.skonto_pct`, `skonto_days`, `payment_days` | implied rate = skonto/(100-skonto) x 360/(net - skonto days). 4 of 46 offer one: **36-54 %** - they need cash |
| Credit index and its trend | `supplier_intel.credit_index`, `credit_index_12m_ago` (higher = worse) | e.g. sup:70280 230 -> 302: worsening |
| Equity ratio | `supplier_intel.equity_ratio_pct` | thin equity = dear money |
| How late we pay it | `invoices.paid_date - due_date` | we already squeeze its cash |
| Notes, letters, news (cash, order book) | `documents`, `news` | Profiler (Saed) turns them into a persona |
| Its answer to our payment-day sheet | the thread | revealed: rate ~ (price step / price) x 360 / (day step) |

**Method.** Default 8 % (our own rate, `company.md`). Skonto present: its implied rate (capped at 40 %).
Else add a premium from credit index (> 250: +4 pt, > 300: +8 pt), worsening trend (+4 pt) and equity
< 25 % (+2 pt). Update live when it trades price for payment days. Source string on every estimate.

## 3. Where the supplier will stop (reservation price) and how fast we concede

Literature: time-dependent tactics (Faratin, Sierra & Jennings 1998): offer(t) = start + (limit - start) x
(t/T)^(1/beta); beta < 1 is Boulware (holds, concedes late), beta > 1 Conceder. Opponent modelling learns
the other side's reservation value from its offers: Bayesian learning over hypotheses (Zeng & Sycara 1998),
and regression on its concession curve to recover its tactic and limit (Hou 2004; Brzostowski & Kowalczyk
estimate beta from observed offers). Arena-specific: a supplier "gives nothing without a move from you" and
bigger moves need its boss - pure Boulware stalls it (sandbox D02).

**Method (A4).** Grid posterior over reservation R in [floor x 0.95, its opening]: prior from the floor
estimate (normal, sigma from its uncertainty); likelihood of its observed prices under
p_t = R + (p_0 - R) x (1 - (t/T)^(1/beta)) for beta in {0.3, 0.5, 1, 2}. Output R_hat and P(it concedes
more than X). **Our concession (A2):** Boulware with beta ~0.5 as the baseline, a minimum step that answers
its last move (relative tit-for-tat), never past the target before the last rounds, never past the ceiling.
Tune beta on sandbox + replays per case type.

## Order of work

1. `estimates.py` (new, pure data): full cost, cost of money, fair price, index change - tests on real rows.
2. `bands.py`: target = cheapest package in C (incl. relationship term) that the supplier can still accept
   (its value >= floor); ceiling from C_open / alternative.
3. `offers.py`, `claims.py` (index change reused), `concession.py` posterior, `dossier.py`.

Sources: [Zeng & Sycara and other opponent models, TU Delft survey](https://tracinsy.ewi.tudelft.nl/pubtrac/GeniusWeb/wiki/OpponentModels?version=55),
[time-dependent tactics and beta](https://www.iaeng.org/publication/WCE2007/WCE2007_pp31-36.pdf),
[detecting time-dependent tactics](https://scitepress.org/publishedPapers/2012/39682/pdf/index.html),
[cash discount implied rate](https://www.accountingtools.com/articles/what-is-a-cash-discount.html).
