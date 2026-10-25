"""Deterministic tools (owner: Amine). `DataTools` implements the `Tools` protocol by composing the modules below:

dossier     what the data says about each supplier (persona, levers, evidence lines)
estimates   the supplier estimate for the squeeze charge, the cost drivers' fair price
claims      the arguments we may cite, filtered and ordered
concession  the fit of the supplier's own price path (for the decision agent's read)
planner     every term sheet we send
message     the deterministic e-mail around them
"""

from dataclasses import replace

from ....play import latest, standing
from ..contracts import CaseContext, ConcessionFit, OfferPlan, Tactic
from . import claims, concession, dossier, estimates, message, planner
from .safe_package import thread_safe


class DataTools:
    def __init__(self, package=None):
        self.package = thread_safe(package)  # DataPackage behind a lock (parallel cases), or None

    def prepare(self, case: dict, threads: dict[str, list[dict]]) -> CaseContext:
        dossiers = {sid: dossier.build(case, self.package, sid) for sid in case["suppliers"]}
        documents = {sid: message.supplier_documents(case, self.package, sid) for sid in case["suppliers"]}
        ctx = CaseContext(case=case, dossiers=dossiers, claims={}, c_alternative=_alternative(case),
                          documents=documents, unit=message.unit_of(case, self.package))  # fmt: skip
        return self.refresh(replace(ctx, fair=self._fair(case, threads)), threads)

    def refresh(self, ctx: CaseContext, threads: dict[str, list[dict]]) -> CaseContext:
        """Each round: the arguments follow the threads (its asked price, rival offers, its reactions)."""
        estimates_ = {sid: self.supplier_from_thread(ctx.case, sid, events) for sid, events in threads.items()}
        found = {}
        for sid in ctx.case["suppliers"]:
            documents = ctx.documents.get(sid, "")
            found[sid] = claims.candidates(ctx.case, self.package, sid, threads, estimates_, documents)
        return replace(ctx, claims=found)

    def _fair(self, case: dict, threads: dict[str, list[dict]]) -> dict[str, float]:
        out = {}
        parts = case.get("parts") or []
        for sid, events in threads.items():
            base = case["buyer"].get("current_unit_price") or standing(events)[1].get("unit_price")
            if self.package is None or len(parts) != 1 or not base:
                continue
            fair = estimates.fair_price(self.package, parts[0], base)
            if fair is not None:
                out[sid] = fair.value
        return out

    def supplier_from_thread(self, case: dict, supplier_id: str, events: list[dict]) -> dict | None:
        """Our estimate of the supplier for the squeeze charge, on its standard terms in the thread."""
        return estimates.supplier_estimate(self.package, case, supplier_id, planner.standard(case, events), events)

    def fit_concessions(self, ctx: CaseContext, supplier_id: str, events: list[dict]) -> ConcessionFit:
        estimate = estimates.should_cost(self.package, ctx.case, supplier_id)
        prior = estimate.value if estimate else None
        return concession.fit(supplier_id, events, prior, ctx.case.get("round_limit", 10))

    def plan_offers(
        self, ctx: CaseContext, supplier_id: str, threads: dict[str, list[dict]], tactic: Tactic
    ) -> OfferPlan:
        """The planner's sheets for this thread; in a case with several suppliers a first offer is anchored to the
        best compliant rival. `tactic.price`: the decision agent's share of the planner's step (CONCEDE)."""
        case, events = ctx.case, threads[supplier_id]
        supplier = self.supplier_from_thread(case, supplier_id, events)
        rate = estimates.money_rate(self.package, supplier_id, events)
        ppm = estimates.real_ppm(self.package, case, supplier_id)
        sheets = planner.packages(case, events, supplier, CONCEDE.get(tactic.price, 1.0), rate, ppm)
        if len(threads) > 1:
            estimates_ = {sid: self.supplier_from_thread(case, sid, ev) for sid, ev in threads.items()}
            sheets = planner.rival_anchor(case, supplier_id, threads, sheets, estimates_)
        std = planner.standard(case, events)
        costs = tuple(planner.cost(case, s, std, supplier) for s in sheets)
        step = (
            planner.concession_step(case, events, supplier) if any(m.get("from") == "buyer" for m in events) else None
        )
        why = "anchor" if step is None else f"step {step:.2f} of the price gap"
        their = (latest(events).get("term_sheets") or [{}])[0].get("terms", {}).get("unit_price")
        pushed = f" ({tactic.price})" if tactic.price != "follow" else ""
        return OfferPlan(supplier_id, tuple(sheets), costs, f"{why}{pushed}; theirs {their}")

    def add_package(self, ctx: CaseContext, supplier_id: str, threads: dict[str, list[dict]], plan: OfferPlan,
                    package: dict) -> OfferPlan:  # fmt: skip
        """The plan with the decision agent's package, priced by the planner within the plan's own C."""
        case, events = ctx.case, threads[supplier_id]
        supplier = self.supplier_from_thread(case, supplier_id, events)
        sheets = planner.with_agent_package(case, list(plan.term_sheets), package, events, supplier)
        if len(sheets) == len(plan.term_sheets) and sheets == list(plan.term_sheets):
            return plan
        std = planner.standard(case, events)
        costs = tuple(planner.cost(case, s, std, supplier) for s in sheets)
        return OfferPlan(supplier_id, tuple(sheets), costs, f"{plan.rationale}; agent package {package}")


CONCEDE = {"follow": 1.0, "trim": 0.5, "push": 0.0}  # share of the planner's step above its minimal step


def _alternative(case: dict) -> float | None:
    c = planner.alternative_cost(case)
    return None if c == float("inf") else c
