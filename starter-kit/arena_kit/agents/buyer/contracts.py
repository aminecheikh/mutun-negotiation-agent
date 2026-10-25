"""The shared types between the three layers of the buyer agent. Change them only by team agreement.

  tools (deterministic)        ->  roles (LLM)                  ->  orchestrator (decide)
  CaseContext, Claim, Dossier,     Dossier (refined),               the policy's verdict, the guard, the arena
  ConcessionFit, OfferPlan         BehaviourRead, Tactic, Email     message from OfferPlan + Email

The deterministic planner (tools.planner) builds every term sheet and tools.message the e-mail around it; the
strategist is the decision agent on top of it (accept, push for more price or follow the planner, which arguments,
one question); the writer words a pushed message's paragraph.
Numbers come from tools only. All types are frozen: build a new one instead of mutating.
"""

from dataclasses import dataclass, field
from typing import Literal, Protocol

Persona = Literal["cash_short", "full_order_book", "monopolist", "partner", "price_fighter", "unknown"]
Action = Literal["send", "accept", "walk_away"]
PriceDecision = Literal["follow", "trim", "push"]


# ---- tools layer (deterministic) -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    """One data argument, ready to paste: the figure and its IDs in one sentence, computed from the facts the
    arena checks against (tools.claims)."""

    sentence: str
    refs: tuple[str, ...]
    kind: str  # notice | rival | quality | index | volume | cost | delivery | history | news
    figure: float | None
    favours_buyer: bool
    strength: float  # rank for the decision agent, 1 = best


@dataclass(frozen=True)
class Dossier:
    """What the data says about one supplier, before any e-mail."""

    supplier_id: str
    name: str
    country: str | None
    persona: Persona
    levers: tuple[str, ...]  # terms it likely values: payment_days, volume_per_year, contract_months, ...
    history_give_pct: float | None  # mean (asked - settled) points in past renewals
    second_source: bool  # do we have a qualified alternative for the part
    evidence: tuple[str, ...]  # short lines with their doc/news/sc IDs, for the prompts and the pitch


@dataclass(frozen=True)
class ConcessionFit:
    """The supplier's own price path inside this negotiation, fitted."""

    supplier_id: str
    prices: tuple[float, ...]
    reservation_estimate: float | None  # where its concessions converge
    last_step_pct: float | None
    shrinking: bool  # each concession smaller than the one before
    p_room: float | None = None  # probability it can still go >= 1 % below its last price (None: no evidence)


@dataclass(frozen=True)
class OfferPlan:
    """1-3 complete term sheets for one supplier and their C, the main sheet first."""

    supplier_id: str
    term_sheets: tuple[dict, ...]
    c_values: tuple[float, ...]
    rationale: str


@dataclass(frozen=True)
class CaseContext:
    """Everything prepared before the first move (no clock running yet), refreshed as the threads grow."""

    case: dict
    dossiers: dict[str, Dossier]
    claims: dict[str, tuple[Claim, ...]]  # per supplier, best first
    c_alternative: float | None
    fair: dict[str, float] = field(default_factory=dict)  # per supplier: today's price moved by its cost drivers
    fits: dict[str, ConcessionFit] = field(default_factory=dict)  # per supplier, this round's fit of its prices
    documents: dict[str, str] = field(default_factory=dict)  # per supplier: its letters and contracts (the rules read)
    unit: str = "units"  # the part's unit of measure, for the commitment sentence
    offers: dict[str, dict[str, OfferPlan]] = field(default_factory=dict)  # this round: {sid: {follow|push: plan}}


# ---- roles layer (LLM) -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BehaviourRead:
    """The reading of the supplier's last e-mail, combined with the ConcessionFit."""

    supplier_id: str
    p_more: float  # chance another round improves its offer
    walk_risk: float  # chance it walks if we push again
    wants: tuple[str, ...]  # what it signalled it values
    signals: tuple[str, ...]  # quotes from its e-mail that support the reading


@dataclass(frozen=True)
class Tactic:
    """The decision agent's choice for this round, on top of the planner."""

    supplier_id: str
    action: Action
    claims: tuple[int, ...] = ()  # indices into CaseContext.claims[supplier_id], at most 3
    question: str | None = None  # one question about a term, no figures
    rationale: str = ""
    price: PriceDecision = "follow"  # the planner's whole step, trim: half of it, push: only its minimal step
    package: dict | None = None  # non-price terms of one more package; the planner prices it within its own C


@dataclass(frozen=True)
class Email:
    subject: str
    body: str
    refs: tuple[str, ...] = field(default_factory=tuple)


# ---- interfaces each owner implements --------------------------------------------------------------------------


class Tools(Protocol):
    def prepare(self, case: dict, threads: dict[str, list[dict]]) -> CaseContext: ...
    def fit_concessions(self, ctx: CaseContext, supplier_id: str, events: list[dict]) -> ConcessionFit: ...
    def plan_offers(
        self, ctx: CaseContext, supplier_id: str, threads: dict[str, list[dict]], tactic: Tactic
    ) -> OfferPlan: ...
    def add_package(
        self, ctx: CaseContext, supplier_id: str, threads: dict[str, list[dict]], plan: OfferPlan, package: dict
    ) -> OfferPlan: ...


class Profiler(Protocol):
    """Once per case, before round 1: reads a dossier's evidence (notes, letters, news) and sets its persona and
    levers. Cache per supplier: the clock is already running when it is called."""

    def refine(self, case: dict, dossier: Dossier) -> Dossier: ...


class Analyst(Protocol):
    def read(self, ctx: CaseContext, supplier_id: str, events: list[dict], fit: ConcessionFit) -> BehaviourRead: ...


class Strategist(Protocol):
    def choose(self, ctx: CaseContext, threads: dict[str, list[dict]], reads: dict[str, BehaviourRead]) -> Tactic: ...


class Writer(Protocol):
    def write(self, ctx: CaseContext, tactic: Tactic, plan: OfferPlan, events: list[dict]) -> Email: ...
