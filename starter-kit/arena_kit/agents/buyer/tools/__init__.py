"""Deterministic tools (owner: Amine). `DataTools` implements the `Tools` protocol by composing the
modules below; replace their placeholder logic, keep the signatures (or change them in contracts.py
with the team)."""

from dataclasses import replace

from ....docs import standard_terms
from ..contracts import BehaviourRead, CaseContext, ConcessionFit, OfferPlan, Tactic
from . import bands, claims, concession, dossier, offers


class DataTools:
    def __init__(self, package=None):
        self.package = package  # arena_kit.data.DataPackage, or None (tests, no data)

    def prepare(self, case: dict, threads: dict[str, list[dict]]) -> CaseContext:
        return CaseContext(
            case=case,
            dossiers={sid: dossier.build(case, self.package, sid) for sid in case["suppliers"]},
            bands=bands.price_bands(case, threads, self.package),
            claims=self._claims(case, threads),
            c_alternative=bands.alternative_cost(case),
        )

    def refresh(self, ctx: CaseContext, threads: dict[str, list[dict]]) -> CaseContext:
        """Each round: claims follow the threads (series the supplier names, rival offers, its asked price).
        Bands stay as prepared. Cheap (milliseconds); IDs already cited stay usable - the writer's guard
        enforces 'each ID once per case'."""
        return replace(ctx, claims=self._claims(ctx.case, threads))

    def _claims(self, case: dict, threads: dict[str, list[dict]]) -> dict:
        return {sid: claims.candidates(case, self.package, sid, threads) for sid in case["suppliers"]}

    def supplier(self, ctx: CaseContext, supplier_id: str, terms: dict, std: dict | None = None) -> dict | None:
        """Our estimate of the supplier (full cost, cost of money, standard terms) for supplier_value."""
        return bands.supplier_estimate(ctx.case, self.package, supplier_id, terms, std or {})

    def fit_concessions(self, ctx: CaseContext, supplier_id: str, events: list[dict]) -> ConcessionFit:
        return concession.fit(supplier_id, events)

    def plan_offers(
        self, ctx: CaseContext, supplier_id: str, events: list[dict], tactic: Tactic, read: BehaviourRead
    ) -> OfferPlan:
        quotes = (m["term_sheets"][0]["terms"] for m in events if m.get("from") != "buyer" and m.get("term_sheets"))
        opening = next(quotes, {})
        supplier = self.supplier(ctx, supplier_id, opening, standard_terms(events)) if opening else None
        return offers.plan(ctx, supplier_id, events, tactic, read, supplier)
