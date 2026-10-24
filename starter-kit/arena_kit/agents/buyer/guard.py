"""The last check before anything reaches a supplier (owner: Jeddou). Pure functions, no LLM.

Defence in depth: the writer (roles) already checks its own e-mail, the tools build valid sheets; the guard
re-checks the *final* message against the arena's rules and our invariants, because one bad message costs more
than any clever one gains:

  term sheets  exactly the negotiable fields, values the arena accepts (an option or the supplier's standard
               value), at most 3, and each one a deal we would be content with if the supplier says 'agreed'
  body         every data ID known to the arena and cited at most once per case, never an offer ID (no
               inter-supplier comparison), at most 3 checkable arguments, no leak of urgency, budget or a
               missing alternative (English and German), bounded lengths
"""

import math
import re
from dataclasses import dataclass

from ...cost import complete_offer
from ..llm import ID_RE
from . import policy
from .contracts import Email
from .state import CaseSession, ThreadState

MAX_BODY, MAX_SUBJECT, MAX_REFS, MAX_SHEETS = 8000, 300, 20, 3
MIN_WORDS = 12  # a body cut shorter than this by the guard is replaced by the template
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ\"'(])")
# Leaks the brief warns about: urgency, a budget, a missing alternative, dependence. English and German.
LEAKS = re.compile(
    r"\b(urgent(?:ly)?|asap|as soon as possible|deadline|our budget|budget|no (?:other )?alternative|"
    r"no other (?:supplier|source)|only (?:possible )?supplier|depend(?:ent)? on you|last chance|"
    r"dringend|dringlich|schnellstm[öo]glich|so schnell wie m[öo]glich|keine alternative|"
    r"kein(?:en)? anderen lieferanten|einzige[nr]? lieferant(?:en)?|angewiesen|letzte chance)\b",
    re.I,
)


@dataclass(frozen=True)
class Checked:
    message: dict
    problems: tuple[str, ...]  # what the guard removed or replaced, for the logs


def scrub_ids(text: str, known) -> str:
    """Remove data IDs we cannot back: an unknown ID reads as a lie (`contradicted`)."""
    cleaned = ID_RE.sub(lambda m: m.group(0) if known(m.group(0)) else "", text)
    return re.sub(r"\s*\(\s*[,;\s]*\)", "", cleaned)


# ---- term sheets ----------------------------------------------------------------------------------------


def normalise_sheet(session: CaseSession, th: ThreadState, sheet: dict, force_snap: bool = False) -> dict | None:
    """Exactly the case's negotiable fields with values the arena accepts. A value outside `term_options` is
    kept only if it is the supplier's own standard value (the arena allows that); otherwise it is snapped."""
    case = session.case
    negotiable = list(case.get("negotiable") or ())
    if not isinstance(sheet, dict):
        return None
    options = case.get("term_options") or {}
    valid = set(sheet) == set(negotiable) and not force_snap
    for f in negotiable:
        if f == "unit_price" or not valid:
            continue
        opts = options.get(f)
        if opts and sheet.get(f) not in opts and sheet.get(f) != th.std.get(f):
            valid = False
    out = dict(sheet) if valid else complete_offer(case, {**th.standing_terms, **sheet})
    price = out.get("unit_price")
    if not isinstance(price, int | float) or isinstance(price, bool) or not price > 0 or math.isinf(price):
        return None
    out["unit_price"] = round(float(price), 2)
    return {f: out.get(f) for f in negotiable}


def check_sheets(session: CaseSession, th: ThreadState, sheets) -> tuple[list[dict], list[str]]:
    kept, problems = [], []
    for raw in list(sheets or ())[:MAX_SHEETS]:
        sheet = normalise_sheet(session, th, raw)
        if sheet is None:
            problems.append("sheet dropped: no valid price")
            continue
        ok, why = policy.sheet_acceptable(session, th, sheet)
        if not ok:
            problems.append(f"sheet dropped: {why}")
            continue
        if sheet not in kept:
            kept.append(sheet)
    return kept, problems


# ---- body ---------------------------------------------------------------------------------------------


def cite_problem(session: CaseSession, th: ThreadState, ref: str) -> str | None:
    """Why a cited ID may not go out, or None if it may."""
    if not session.known(ref):
        return f"unknown ID {ref}"
    if ref.startswith("offer:"):  # its own offer says nothing; another bidder's is inter-supplier comparison
        return f"{ref} is an offer: no inter-supplier comparison, and its own offer proves nothing"
    if ref in session.burned:
        return f"{ref} already cited in this case"
    return None


def clean_body(session: CaseSession, th: ThreadState, body: str) -> tuple[str, set[str], list[str]]:
    """(body, IDs it cites, problems): sentences with an unusable ID or a leak are removed; arguments beyond
    the per-message limit are removed from the end."""
    problems, cited, arguments, lines = [], set(), 0, []
    for line in str(body or "").replace("\r\n", "\n").split("\n"):
        kept = []
        for sentence in SENTENCE_END.split(line):
            ids = set(ID_RE.findall(sentence))
            bad = next((why for ref in sorted(ids) if (why := cite_problem(session, th, ref))), None)
            if bad:
                problems.append(f"sentence removed: {bad}")
                continue
            if leak := LEAKS.search(sentence):
                problems.append(f"sentence removed: leak {leak.group(0)!r}")
                continue
            if ids:
                if arguments >= session.config.max_claims:
                    problems.append("sentence removed: more than 3 arguments")
                    continue
                arguments += 1
                cited |= ids
            kept.append(sentence)
        if kept or not line.strip():
            lines.append(" ".join(kept))
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text[:MAX_BODY], cited, problems


def build_message(session: CaseSession, th: ThreadState, sheets, email: Email | None) -> Checked | None:
    """The arena message, or None when nothing sendable is left (the caller falls back to the template)."""
    if email is None:
        return None
    kept, problems = check_sheets(session, th, sheets)
    body, cited, body_problems = clean_body(session, th, email.body)
    problems += body_problems
    if len(body.split()) < MIN_WORDS:
        return None
    refs = [r for r in dict.fromkeys(email.refs or ()) if r in cited][:MAX_REFS]
    subject = str(email.subject or "").strip()[:MAX_SUBJECT] or f"Re: {session.case.get('title', '')}"[:MAX_SUBJECT]
    message = {"subject": subject, "body": body, "term_sheets": [{"terms": s} for s in kept], "refs": refs}
    return Checked(message, tuple(problems))


def reserve(session: CaseSession, th: ThreadState, message: dict) -> dict:
    """Right before the POST, under the case lock: drop arguments another thread cited meanwhile and mark this
    message's IDs as used. Threads prepare their e-mails in parallel; without this two bidders could both cite the
    same index pair in the same second (the arena then answers `duplicate`)."""
    if not message.get("body"):
        return message
    body, cited, _ = clean_body(session, th, message["body"])
    session.burned |= {r for r in cited if not r.startswith("offer:")}
    th.cited_offers |= {r for r in cited if r.startswith("offer:")}
    refs = [r for r in message.get("refs") or [] if r in cited][:MAX_REFS]
    return {**message, "body": body or message["body"], "refs": refs}


def accept_ok(session: CaseSession, th: ThreadState) -> bool:
    """The invariant every accept passes, re-checked at the last moment under the case lock."""
    return policy.acceptable(session, th)


def hard_repair(session: CaseSession, th: ThreadState, message: dict) -> dict:
    """After a 422 (the arena rejected the message, no round used): snap every sheet to the options, drop what
    still fails; with no sheet left the e-mail goes out as a question (still an answer within 60 s)."""
    sheets = []
    for ts in message.get("term_sheets") or []:
        sheet = normalise_sheet(session, th, ts.get("terms", ts), force_snap=True)
        if sheet is not None and policy.sheet_acceptable(session, th, sheet)[0] and sheet not in sheets:
            sheets.append(sheet)
    refs = [r for r in message.get("refs") or [] if session.known(r)][:MAX_REFS]
    return {**message, "term_sheets": [{"terms": s} for s in sheets[:MAX_SHEETS]], "refs": refs}
