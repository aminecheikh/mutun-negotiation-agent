"""What a thread says, derived from its e-mails (owner: Jeddou).

`rebuild` recomputes everything the policy needs from the thread's messages - status, standing sheet and its C,
the C path, which data IDs we already cited, how often we answered a final offer - so a resumed session, a
single kit-style decision and a live worker all see the same state. Reading the supplier's *words* is the
roles' job (Saed); this module only reads structured fields.
"""

import logging
import math
import re

from ...docs import standard_terms
from ...play import ENDED, latest, standing
from ..llm import ID_RE
from .state import CaseSession, ThreadState

log = logging.getLogger("arena")

DE_MARKERS = re.compile(
    r"\b(der|die|das|und|wir|sie|ihnen|ihr|nicht|für|mit|angebot|preis|zahlungsziel|lieferzeit|grüße|"
    r"sehr geehrte|guten tag|freundlichen)\b",
    re.I,
)
EN_MARKERS = re.compile(r"\b(the|we|our|your|price|offer|payment|regards|dear|thank|please|with)\b", re.I)


def ids_in(message: dict) -> set[str]:
    """Every data ID a message cites: in its body or in its refs (both count for the arena)."""
    return set(ID_RE.findall(str(message.get("body") or ""))) | {r for r in message.get("refs") or () if r}


def detect_language(text: str) -> str:
    """'de', 'en' or 'mixed' from function words; used for logs and the jury trail, not for decisions."""
    de, en = len(DE_MARKERS.findall(text or "")), len(EN_MARKERS.findall(text or ""))
    if de >= 2 * max(en, 1):
        return "de"
    if en >= 2 * max(de, 1):
        return "en"
    return "mixed"


def supplier_sheets(events: list[dict]) -> list[dict]:
    """The terms of each supplier term sheet in the thread, oldest first."""
    out = []
    for m in events:
        if m.get("from") != "buyer":
            for sheet in (m.get("term_sheets") or [])[:1]:
                if sheet.get("terms") is not None:
                    out.append(sheet["terms"])
    return out


def rebuild(session: CaseSession, th: ThreadState) -> None:
    """Recompute the thread's derived fields from its e-mails (caller holds no lock: pure over `th.events`)."""
    events = th.events
    if not events:
        return
    last = latest(events)
    th.std = standard_terms(events)
    if session.estimator is not None:
        try:
            session.estimates[th.supplier_id] = session.estimator(th.supplier_id, events)
        except Exception:  # an estimate is a refinement: without it C leaves the relationship charge out
            log.exception("%s: supplier estimate failed", th.supplier_id)
    sheet_id, terms = standing(events)  # its first sheet: the only one an accept may name (api.md)
    th.status = str(last.get("status") or "open")
    th.standing_id, th.standing_terms = sheet_id, dict(terms)
    th.alive = th.status not in ENDED
    th.c = session.cost(th.supplier_id, terms) if terms.get("unit_price") is not None else math.inf
    th.c_history = tuple(
        session.cost(th.supplier_id, t) if t.get("unit_price") is not None else math.inf
        for t in supplier_sheets(events)
    )
    th.cited_offers = {r for m in events if m.get("from") == "buyer" for r in ids_in(m) if r.startswith("offer:")}


def rebuild_case(session: CaseSession) -> None:
    """Every thread, plus the case-wide sets: IDs already cited and offer IDs that exist in this session."""
    for th in session.threads.values():
        rebuild(session, th)
    burned, offers = set(), set()
    for th in session.threads.values():
        for m in th.events:
            if m.get("from") == "buyer":
                burned |= {r for r in ids_in(m) if not r.startswith("offer:")}
            else:
                offers |= {s["term_sheet_id"] for s in m.get("term_sheets") or [] if s.get("term_sheet_id")}
    session.burned, session.offers = burned, offers


def report_claims(session: CaseSession, th: ThreadState, sent: dict, reply: dict) -> dict:
    """Count the arena's checks of the arguments we just sent; a `contradicted` one is a bug upstream (claims
    are pre-verified), so it is logged loudly with the sentence that carried it."""
    counts: dict[str, int] = {}
    for r in reply.get("claim_results") or []:
        counts[r.get("result", "?")] = counts.get(r.get("result", "?"), 0) + 1
    if counts.get("contradicted"):
        log.error(
            "%s/%s: %d claim(s) contradicted - body was: %s",
            session.case.get("case_id"),
            th.supplier_id,
            counts["contradicted"],
            str(sent.get("body") or "")[:600],
        )
    return counts
