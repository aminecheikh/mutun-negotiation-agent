"""Deterministic tools (owner: Amine). `DataTools` implements the `Tools` protocol by composing the
modules below; replace their placeholder logic, keep the signatures (or change them in contracts.py
with the team)."""

from dataclasses import replace

from ....docs import standard_terms
from ..contracts import BehaviourRead, CaseContext, ConcessionFit, OfferPlan, Tactic
from . import bands, claims, concession, dossier, levers, offers, signals
from .safe_package import thread_safe


def opening_terms(events: list[dict]) -> dict:
    """The supplier's first term sheet in the thread (its quote), or {}."""
    quotes = (m["term_sheets"][0]["terms"] for m in events if m.get("from") != "buyer" and m.get("term_sheets"))
    return next(quotes, {})


class DataTools:
    def __init__(self, package=None):
        self.package = thread_safe(package)  # DataPackage behind a lock (parallel cases), or None

    def prepare(self, case: dict, threads: dict[str, list[dict]]) -> CaseContext:
        return CaseContext(
            case=case,
            dossiers={sid: dossier.build(case, self.package, sid) for sid in case["suppliers"]},
            bands=bands.price_bands(case, threads, self.package),
            claims=self._claims(case, threads),
            c_alternative=bands.alternative_cost(case),
            trades=self._trades(case, threads),
        )

    def refresh(self, ctx: CaseContext, threads: dict[str, list[dict]]) -> CaseContext:
        """Each round: claims follow the threads (series the supplier names, rival offers, its asked price).
        Bands and trades are re-priced only once a supplier has sent its cost breakdown; otherwise they stay as
        prepared. Cheap (milliseconds); the writer's guard enforces 'each ID once per case'."""
        ctx = replace(ctx, claims=self._claims(ctx.case, threads))
        if any(signals.read(ev, self.package).breakdown_cost for ev in threads.values()):
            ctx = replace(
                ctx, bands=bands.price_bands(ctx.case, threads, self.package), trades=self._trades(ctx.case, threads)
            )
        return ctx

    def _claims(self, case: dict, threads: dict[str, list[dict]]) -> dict:
        return {sid: claims.candidates(case, self.package, sid, threads) for sid in case["suppliers"]}

    def _trades(self, case: dict, threads: dict[str, list[dict]]) -> dict:
        out = {}
        for sid, events in threads.items():
            terms, std = opening_terms(events), standard_terms(events)
            if terms.get("unit_price") is not None:
                stated = {f: std[f] for f in case["negotiable"] if f in std}
                supplier = self.supplier_from_thread(case, sid, events)
                out[sid] = levers.trades(case, {**stated, **terms}, std, supplier)
        return out

    def supplier(self, ctx: CaseContext, supplier_id: str, terms: dict, std: dict | None = None) -> dict | None:
        """Our estimate of the supplier (full cost, cost of money, standard terms) for supplier_value."""
        return bands.supplier_estimate(ctx.case, self.package, supplier_id, terms, std or {})

    def supplier_from_thread(self, case: dict, supplier_id: str, events: list[dict]) -> dict | None:
        """As `supplier`, from its quote in the thread - and its own cost breakdown once it has sent one."""
        terms = opening_terms(events)
        if terms.get("unit_price") is None:
            return None
        cost = bands.cost_estimate(case, self.package, supplier_id, signals.read(events, self.package))
        return bands.supplier_estimate(case, self.package, supplier_id, terms, standard_terms(events), cost)

    def fit_concessions(self, ctx: CaseContext, supplier_id: str, events: list[dict]) -> ConcessionFit:
        band = ctx.bands.get(supplier_id)
        return concession.fit(supplier_id, events, band.floor if band else None, ctx.case.get("round_limit", 10))

    def plan_offers(
        self, ctx: CaseContext, supplier_id: str, events: list[dict], tactic: Tactic, read: BehaviourRead
    ) -> OfferPlan:
        supplier = self.supplier_from_thread(ctx.case, supplier_id, events)
        return offers.plan(ctx, supplier_id, events, tactic, read, supplier)
