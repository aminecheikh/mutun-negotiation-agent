"""The deterministic e-mail (owner: Jeddou; provenance: docs/hybrid.md).

Every message of the deterministic layer has one shape, line by line:
  intro       thanks and the attached complete packages
  narrative   one paragraph chosen by rule from the case goal, what the supplier's documents and replies signal and
              its latest objection (or, when the decision agent deviates, its own checked paragraph)
  brief       the binding commitment of our lead package, figures from the sheet (planner.commercial_brief)
  evidence    the chosen argument sentences, verbatim
  sign-off
The practice suppliers answer the words as well as the sheets, so the rule wording is kept fixed: same inputs, same
e-mail.
"""

import csv
import re

INTRO = "Thank you for your proposal. Please consider the attached complete commercial packages."
SIGN_OFF = "Best regards, Hollerau Purchasing"
MAX_DOCUMENTS, DOCUMENT_CHARS = 10, 5000  # per supplier: its newest letters and contracts, each cut here
UNITS = {"pc", "kg", "t", "m", "units", "set"}

# ---- what its documents and replies signal ----------------------------------------------------------------------

PRICE_LOCKED_REPLY = re.compile(
    r"\b(?:price|discount|reduction)s?\b.{0,40}\b(?:fixed|frozen|impossible|unavailable|no room)\b"
    r"|\b(?:cannot|can't|unable to)\b.{0,30}\b(?:discount|reduce|lower)\b"
)
PRICE_LOCKED_LETTER = re.compile(  # a letter announcing a binding list price: set centrally or ruled out per customer
    r"\b(?:ruled out|no longer grant|cannot grant|not grant)\b.{0,30}\bprice reductions?\b"
    r"|\bprice list\b.{0,60}\b(?:all customers|without exception)\b"
    r"|\bprices (?:are|will be) set centrally\b"
)
CASH = re.compile(
    r"\b(?:liquidity|cash[ -]?flow|working capital|early payment|payment delay|"
    r"overdue invoice|cash discount|bank is watching|short-time work|prepayment)\b"
    r"|\bpay (?:our|the) invoices within\b"
)
APPROVAL = re.compile(r"\b(?:committee|management|head office|approval)\b")
HEDGED = re.compile(
    r"\b(?:hedged|fixed[ -]?price cover)\b"
    r"|\b(?:bought|purchased|covered|secured)\b.{0,60}\bat fixed prices\b"
)
# its latest reply only: an objection it already resolved does not count
VOLUME_ASKED = re.compile(r"(?:which|what|binding|commit\w*|higher).{0,35}volume|volume.{0,35}commit", re.I)
BREAKDOWN_REFUSED = re.compile(
    r"(?:do not|don't|not).{0,25}share.{0,30}(?:breakdown|detail|calculation)"
    r"|\b(?:do not|don't|cannot|can't|will not|won't)\b.{0,30}"
    r"\b(?:provide|give|send|do)\b.{0,25}\b(?:cost\s+)?(?:breakdowns?|calculations?)\b"
    r"|(?:breakdown|calculation|cost detail).{0,30}(?:confidential|proprietary|internal)",
    re.I,
)
STALLED = re.compile(
    r"\b(?:unchanged|no movement|insufficient improvement|same proposal|token concession|marginal reduction)\b", re.I
)


def _replies(events: list[dict]) -> list[str]:
    return [str(m.get("body") or "") for m in events if m.get("from") != "buyer"]


def signals(documents: str, events: list[dict]) -> dict[str, bool]:
    """What its letters and contracts (`documents`) and its replies so far say about it."""
    replies = "\n".join(_replies(events)).lower()
    text = documents.lower() + "\n" + replies
    return {
        "price_locked": bool(PRICE_LOCKED_REPLY.search(replies) or PRICE_LOCKED_LETTER.search(documents.lower())),
        "cash_sensitive": bool(CASH.search(text)),
        "approval_needed": bool(APPROVAL.search(text)),
        "hedged": bool(HEDGED.search(text)),
    }


def objections(events: list[dict]) -> dict[str, bool]:
    """Its latest reply: does it ask which volume we commit, refuse a cost breakdown, call our offer stalled?"""
    reply = next(iter(reversed(_replies(events))), "")
    return {
        "volume": bool(VOLUME_ASKED.search(reply)),
        "breakdown_refused": bool(BREAKDOWN_REFUSED.search(reply)),
        "stalled": bool(STALLED.search(reply)),
    }


# ---- the narrative ------------------------------------------------------------------------------------------------

NARRATIVES = {
    "confidential": (
        "We acknowledge that your detailed calculation is confidential. Please compare the complete exchanges and "
        "confirm the price you can approve for our stated commitment."
    ),
    "secure_supply": (
        "We offer the stated commitment in exchange for the binding annual allocation in the term sheets. Please "
        "involve production planning and confirm which complete package secures the quantity and delivery schedule."
    ),
    "quality_first": (
        "Please involve your quality manager and provide the corrective-action report. We can discuss a sustainable "
        "price in exchange for a binding defect commitment and fair recovery of complaint costs."
    ),
    "price_locked": (
        "We understand the pricing restriction. Please have finance and management review the overall exchange in "
        "the attached packages and identify which trade would allow approval."
    ),
    "hedged": (
        "Please explain how existing fixed-price material cover affects this quotation. Please separate the "
        "conversion price from the material component so the same movement is not charged twice, and review the "
        "attached packages with finance."
    ),
    "cash": (
        "We recognise the importance of reliable cash flow and our payment history. The early-payment package is a "
        "concrete offer to improve it, in exchange for a corresponding price reduction. Please ask finance to "
        "compare it with the other packages."
    ),
    "approval": (
        "Please submit these complete packages to your pricing committee or authorised decision-maker. Our "
        "commitment should support an improved overall agreement. Please identify the trade that would allow "
        "approval."
    ),
    "continuity": (
        "We can commit to the longer framework in the term sheets. Please price that continuity and the "
        "consolidated business, and confirm the corresponding payment terms."
    ),
    "default": (
        "We value a workable long-term relationship. Please compare these complete "
        "packages and share a cost breakdown so we can resolve the remaining difference."
    ),
}


def narrative_key(case: dict, documents: str, events: list[dict]) -> str:
    """Which paragraph the rules choose: its latest objection first, then the case goal, then what it signals."""
    if objections(events)["breakdown_refused"]:
        return "confidential"
    goal = case["buyer"].get("category_goal")
    if goal in ("secure_supply", "quality_first"):
        return goal
    sig = signals(documents, events)
    if sig["price_locked"]:
        return "price_locked"
    if sig["hedged"]:
        return "hedged"
    if sig["cash_sensitive"] and "payment_days" in case["negotiable"]:
        return "cash"
    ours = sum(m.get("from") == "buyer" for m in events)
    if sig["approval_needed"] or (ours >= 2 and objections(events)["stalled"]):
        return "approval"
    if "contract_months" in case["negotiable"]:
        return "continuity"
    return "default"


def narrative(case: dict, documents: str, events: list[dict]) -> str:
    return NARRATIVES[narrative_key(case, documents, events)]


def compose(narrative_text: str, brief: str, sentences: list[str]) -> str:
    return "\n".join([INTRO, narrative_text, brief, *sentences, SIGN_OFF])


def subject(case: dict) -> str:
    return f"Re: {case['title']}"[:300]


# ---- what the rules read: its documents, the part's unit -----------------------------------------------------------


def _rows(package, table: str) -> list[dict]:
    """A table's rows from the package and the Sunday release (the release's rows are not in the SQLite)."""
    out = []
    for root in (getattr(package, "root", None), getattr(package, "release", None)):
        path = root / "data" / f"{table}.csv" if root is not None else None
        if path is not None and path.is_file():
            with path.open(encoding="utf-8", newline="") as stream:
                out.extend(dict(row) for row in csv.DictReader(stream))
    return out


def _newest(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: r.get("date") or r.get("quarter") or r.get("valid_from") or "", reverse=True)


def supplier_documents(case: dict, package, supplier_id: str) -> str:
    """The text of its newest letters and contracts with us, the case's trigger letter and any letter of the
    release about it, each cut to DOCUMENT_CHARS."""
    if package is None:
        return ""
    documents = [r for r in _rows(package, "documents") if r.get("supplier_id") == supplier_id]
    contracts = [r for r in _rows(package, "contracts") if r.get("supplier_id") == supplier_id]
    refs = [r["id"] for r in _newest(documents)[:MAX_DOCUMENTS] + _newest(contracts)[:MAX_DOCUMENTS]]
    if trigger := case.get("trigger_doc"):
        refs.insert(0, trigger)
    facts = getattr(package, "facts", {}) or {}
    refs += [ref for ref, f in facts.items() if ref.startswith("doc:") and f.get("supplier") == supplier_id]
    known = {r["id"] for r in _rows(package, "documents") + _rows(package, "contracts")}
    known |= {ref for ref in facts if ref.startswith(("doc:", "ctr:"))}
    texts = []
    for ref in dict.fromkeys(refs):
        if ref not in known:
            continue
        try:
            texts.append(package.document(ref)[:DOCUMENT_CHARS])
        except (FileNotFoundError, ValueError):
            continue
    return "\n".join(texts)


def unit_of(case: dict, package) -> str:
    """The part's unit of measure for the brief (a bundle of parts: 'units')."""
    parts = case.get("parts") or []
    if package is None or len(parts) != 1:
        return "units"
    row = next((r for r in _rows(package, "parts") if r.get("id") == parts[0]), {})
    unit = row.get("uom") or "units"
    return unit if unit in UNITS else "units"
