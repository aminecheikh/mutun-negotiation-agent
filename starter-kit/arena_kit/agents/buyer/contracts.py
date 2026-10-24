"""The shared types between the three layers of the buyer agent. Change them only by team agreement.

  tools (deterministic)  ->  roles (LLM)       ->  orchestrator (decide)
  CaseContext, PriceBand,    Dossier (refined),     picks the roles per round, applies the guard,
  Claim, Dossier,            BehaviourRead,         turns OfferPlan + Email into an arena message
  ConcessionFit, OfferPlan   Tactic, Email

Roles: Profiler (once per case), Analyst -> Strategist -> Writer (each round). Analyst and Strategist
are separate types so they can be one LLM call or two; the orchestrator decides by measured latency.

Numbers come from tools only. Roles read them and choose and write; they never compute prices.
All types are frozen: build a new one instead of mutating.
"""

from dataclasses import dataclass, field
from typing import Literal, Protocol

Persona = Literal["cash_short", "full_order_book", "monopolist", "partner", "price_fighter", "unknown"]
Action = Literal["send", "accept", "walk_away"]


# ---- tools layer (deterministic, owner: tools) -------------------------------------------------


@dataclass(frozen=True)
class Claim:
    """One data argument, ready to paste: the figure and its IDs in one sentence. Pre-checked against
    facts.json with the arena's own rule, so it can only come back `verified` (or `irrelevant`)."""

    sentence: str
    refs: tuple[str, ...]
    kind: str  # index_change | rival_quote | scorecard | quality | po_history | should_cost | forecast | notice
    figure: float | None
    favours_buyer: bool
    strength: float  # 0..1, how much it should hurt the supplier's position


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
class PriceBand:
    """Per supplier: where the price can land. Prices per the part's price_unit; c_* in EUR total cost C
    including the relationship charge and index-clause cost where the case has them."""

    supplier_id: str
    floor: float | None  # estimated supplier full cost: it never sells below this
    fair: float | None  # last price moved by its cost drivers' index changes
    target: float  # where we aim to close
    ceiling: float  # the most we accept
    c_target: float
    c_ceiling: float
    derivation: tuple[str, ...] = ()  # how each number was found, in words


@dataclass(frozen=True)
class ConcessionFit:
    """The supplier's own price path inside this negotiation, fitted."""

    supplier_id: str
    prices: tuple[float, ...]
    reservation_estimate: float | None  # where its concessions converge
    last_step_pct: float | None
    shrinking: bool  # each concession smaller than the one before


@dataclass(frozen=True)
class OfferPlan:
    """1-3 complete term sheets for one supplier and their C, best for us first."""

    supplier_id: str
    term_sheets: tuple[dict, ...]
    c_values: tuple[float, ...]
    rationale: str


@dataclass(frozen=True)
class CaseContext:
    """Everything prepared before the first move (no clock running yet)."""

    case: dict
    dossiers: dict[str, Dossier]
    bands: dict[str, PriceBand]
    claims: dict[str, tuple[Claim, ...]]  # per supplier
    c_alternative: float | None


# ---- roles layer (LLM, owner: roles) -----------------------------------------------------------


@dataclass(frozen=True)
class BehaviourRead:
    """The analyst's reading of the supplier's last e-mail, combined with the ConcessionFit."""

    supplier_id: str
    p_more: float  # chance another round improves its offer
    walk_risk: float  # chance it walks if we push again
    wants: tuple[str, ...]  # what it signalled it values
    signals: tuple[str, ...]  # quotes from its e-mail that support the reading


@dataclass(frozen=True)
class Tactic:
    """The strategist's decision for this round."""

    supplier_id: str
    action: Action
    levers: tuple[str, ...] = ()  # terms to trade this round
    claims: tuple[int, ...] = ()  # indices into CaseContext.claims[supplier_id], at most 3
    question: str | None = None  # e.g. ask for a cost breakdown
    rationale: str = ""


@dataclass(frozen=True)
class Email:
    subject: str
    body: str
    refs: tuple[str, ...] = field(default_factory=tuple)


# ---- interfaces each owner implements ----------------------------------------------------------


class Tools(Protocol):
    def prepare(self, case: dict, threads: dict[str, list[dict]]) -> CaseContext: ...
    def fit_concessions(self, ctx: CaseContext, supplier_id: str, events: list[dict]) -> ConcessionFit: ...
    def plan_offers(
        self, ctx: CaseContext, supplier_id: str, events: list[dict], tactic: Tactic, read: BehaviourRead
    ) -> OfferPlan: ...


class Profiler(Protocol):
    """Once per case, before round 1: reads a dossier's evidence (notes, letters, news) and sets its
    persona and levers. Cache per supplier: the clock is already running when it is called."""

    def refine(self, case: dict, dossier: Dossier) -> Dossier: ...


class Analyst(Protocol):
    def read(self, ctx: CaseContext, supplier_id: str, events: list[dict], fit: ConcessionFit) -> BehaviourRead: ...


class Strategist(Protocol):
    def choose(self, ctx: CaseContext, threads: dict[str, list[dict]], reads: dict[str, BehaviourRead]) -> Tactic: ...


class Writer(Protocol):
    def write(self, ctx: CaseContext, tactic: Tactic, plan: OfferPlan, events: list[dict]) -> Email: ...
