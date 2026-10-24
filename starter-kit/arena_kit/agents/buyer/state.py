"""The live state of one case (owner: Jeddou): one `ThreadState` per supplier, one `CaseSession` around them.

Shared by the thread workers and the arbiter. Fields the arbiter reads (`in_flight`, `alive`, `status`, `c`,
`standing_id`) are written under `CaseSession.lock`; everything derived from a thread's e-mails is rebuilt
from the e-mails themselves (`observe.rebuild`), so the state can never drift from what the arena saw.
"""

import contextlib
import math
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from ...cost import total_cost
from ...play import moves_left
from .config import OrchestratorConfig
from .contracts import BehaviourRead, CaseContext, ConcessionFit


@dataclass
class ThreadState:
    supplier_id: str
    events: list[dict]  # the thread exactly as the arena shows it, buyer and supplier messages, oldest first
    received_at: float  # time.monotonic() when we received the supplier's latest message
    # derived from `events` by observe.rebuild
    std: dict = field(default_factory=dict)  # the supplier's standard terms from its latest quote PDF
    status: str = "open"
    standing_id: str | None = None
    standing_terms: dict = field(default_factory=dict)
    c: float = math.inf  # C of its standing sheet on closing terms (our evaluator, relationship-aware)
    c_history: tuple[float, ...] = ()  # C of each of its sheets, oldest first
    alive: bool = True
    final_countered: bool = False  # we already answered one final_offer with a counter
    keepalives: int = 0  # our messages without a term sheet
    cited_offers: set[str] = field(default_factory=set)  # rival offer IDs already cited to this supplier
    # live only
    in_flight: bool = False  # our message is out, the supplier's reply has not come back yet
    errors: int = 0
    rule_only_once: bool = False  # after a resume: the first answer uses rule twins only (the clock may be short)
    walk_hint: bool = False  # the strategist asked to walk away (the arbiter decides at case level)
    refused: set[str] = field(default_factory=set)  # sheet IDs the arena refused to let us accept: never again
    exhausted: bool = False  # the arena said this thread's round limit is reached: no more messages, accept or not
    slowest_round_s: float = 0.0  # longest decision + reply so far on this thread
    fit: ConcessionFit | None = None
    read: BehaviourRead | None = None

    @property
    def our_rounds(self) -> int:
        return sum(m.get("from") == "buyer" for m in self.events)

    @property
    def awaiting_us(self) -> bool:
        """The supplier spoke last: its 60-second clock for our answer is running."""
        return bool(self.events) and self.events[-1].get("from") != "buyer"

    def moves_left(self, case: dict) -> int:
        return 0 if self.exhausted else moves_left(case, self.events)


class SendGate:
    """At most one POST in flight per session; waiting threads go in order of their deadline (oldest reply
    first). Only used when the server turns out to serialise calls on a session (config.serialised_session)."""

    def __init__(self):
        self._cv = threading.Condition()
        self._busy = False
        self._waiting: list[tuple[float, int]] = []
        self._seq = 0

    @contextlib.contextmanager
    def __call__(self, priority: float) -> Iterator[None]:
        with self._cv:
            self._seq += 1
            me = (priority, self._seq)
            self._waiting.append(me)
            self._waiting.sort()
            while self._busy or self._waiting[0] != me:
                self._cv.wait(timeout=1.0)
            self._waiting.pop(0)
            self._busy = True
        try:
            yield
        finally:
            with self._cv:
                self._busy = False
                self._cv.notify_all()


class NoGate:
    @contextlib.contextmanager
    def __call__(self, priority: float) -> Iterator[None]:
        yield


@dataclass
class CaseSession:
    case: dict
    ctx: CaseContext
    threads: dict[str, ThreadState]
    config: OrchestratorConfig
    table: object | None = None  # play.RemoteTable; None in single-decision mode (BuyerAgent.decide)
    estimates: dict[str, dict | None] = field(default_factory=dict)  # supplier estimate per thread (tools)
    # (supplier_id, events) -> our estimate of the supplier for the relationship charge; re-run on every reply so
    # a cost breakdown the supplier sends replaces the data-based estimate (tools.supplier_from_thread)
    estimator: Callable[[str, list[dict]], dict | None] | None = None
    c_open: float = math.inf  # the cheapest opening sheet that keeps the must-haves, in our C
    deadline: Callable[[], float | None] | None = None  # the wave's cap as time.time(), or None (practice)
    burned: set[str] = field(default_factory=set)  # data IDs already cited in this case (each counts once)
    offers: set[str] = field(default_factory=set)  # every supplier term sheet ID seen (citable as offer:)
    freeze: bool = False
    result: dict | None = None
    end_reason: str | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)
    gate: SendGate | NoGate = field(default_factory=NoGate)

    def __post_init__(self):
        self._cv = threading.Condition(self.lock)
        self._ended = threading.Event()
        self._known_fn: Callable[[str], bool] | None = None

    # ---- evaluation ------------------------------------------------------------------------------------

    @property
    def c_alt(self) -> float | None:
        return self.ctx.c_alternative

    def cost(self, supplier_id: str, terms: dict) -> float:
        """C of a term sheet with this supplier: its standard terms for anything left out, and our estimate of
        the supplier for the relationship charge in stabilise / secure_supply cases. inf if it has no price."""
        th = self.threads[supplier_id]
        try:
            return total_cost(self.case, terms, th.std, self.estimates.get(supplier_id))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return math.inf

    def band(self, supplier_id: str):
        return self.ctx.bands.get(supplier_id)

    def persona(self, supplier_id: str) -> str:
        dossier = self.ctx.dossiers.get(supplier_id)
        return dossier.persona if dossier else "unknown"

    def known(self, ref: str) -> bool:
        """A data ID the arena can check: in facts.json, in the case's own facts, or an offer of this session."""
        if ref in self.offers or ref in (self.case.get("facts") or {}):
            return True
        return bool(self._known_fn and self._known_fn(ref))

    def set_lookup(self, fn: Callable[[str], bool] | None) -> None:
        self._known_fn = fn

    # ---- time ------------------------------------------------------------------------------------------

    def seconds_left(self) -> float | None:
        cap = self.deadline() if callable(self.deadline) else None
        return None if cap is None else cap - time.time()

    def close_margin(self) -> float:
        slowest = max((t.slowest_round_s for t in self.threads.values()), default=0.0)
        return max(self.config.close_margin_s, slowest + self.config.round_margin_s)

    def in_close_window(self) -> bool:
        left = self.seconds_left()
        return left is not None and left <= self.close_margin()

    # ---- coordination ----------------------------------------------------------------------------------

    @property
    def ended(self) -> bool:
        return self._ended.is_set()

    def notify(self) -> None:
        with self._cv:
            self._cv.notify_all()

    def wait_change(self, timeout: float) -> None:
        with self._cv:
            if not self.ended:
                self._cv.wait(timeout=timeout)

    def finish(self, result: dict | None, reason: str) -> bool:
        """Record the case's end once; later calls are ignored. True if this call ended it."""
        with self.lock:
            if self.ended:
                return False
            self.result, self.end_reason = result, reason
            self._ended.set()
            self._cv.notify_all()
            return True

    def alive(self) -> list[ThreadState]:
        return [t for t in self.threads.values() if t.alive]
