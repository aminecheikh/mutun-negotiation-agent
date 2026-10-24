"""Deterministic tools (owner: Amine). `DataTools` implements the `Tools` protocol by composing the
modules below; replace their placeholder logic, keep the signatures (or change them in contracts.py
with the team)."""

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
            claims={sid: claims.candidates(case, self.package, sid) for sid in case["suppliers"]},
            c_alternative=bands.alternative_cost(case),
        )

    def fit_concessions(self, ctx: CaseContext, supplier_id: str, events: list[dict]) -> ConcessionFit:
        return concession.fit(supplier_id, events)

    def plan_offers(
        self, ctx: CaseContext, supplier_id: str, events: list[dict], tactic: Tactic, read: BehaviourRead
    ) -> OfferPlan:
        return offers.plan(ctx, supplier_id, events, tactic, read)
