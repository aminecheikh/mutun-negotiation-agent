"""E-mails as e-mails: the thread rendered as the plain text a buyer reads, plus the template e-mails of the rule
fallback. The agents get the supplier's words exactly as written (German, English or mixed) - never a JSON digest.
"""

import re

from ...play import ENDED

BODY_CHARS = 6000  # one e-mail; the arena allows 8,000 for ours
ATTACHMENT_CHARS = 5000
DE_WORDS = re.compile(
    r"\b(der|die|das|und|wir|sie|ihnen|ihr|nicht|für|mit|angebot|preis|zahlungsziel|lieferzeit|grüße|"
    r"sehr geehrte|guten tag|freundlichen|vielen dank|bitte)\b",
    re.I,
)
EN_WORDS = re.compile(r"\b(the|we|our|your|price|offer|payment|regards|dear|thank|please|with)\b", re.I)


def language(text: str) -> str:
    """'de' or 'en' by function words (mixed e-mails count as the language that dominates)."""
    de, en = len(DE_WORDS.findall(text or "")), len(EN_WORDS.findall(text or ""))
    return "de" if de > en else "en"


def _terms_line(terms: dict) -> str:
    def value(v):
        if isinstance(v, dict):  # an index clause
            return (
                f"{v.get('series')} {round(100 * (v.get('material_share') or 0))}% material, "
                f"deadband {v.get('deadband_pct')}%, base {v.get('base_month')}"
            )
        return "none" if v is None else str(v)

    return ", ".join(f"{k} {value(v)}" for k, v in terms.items())


def render_message(m: dict, n: int) -> str:
    who = "US (Hollerau Pumpenwerke, purchasing)" if m.get("from") == "buyer" else f"SUPPLIER ({m.get('from')})"
    head = f"--- message {n}: from {who}"
    if m.get("from") != "buyer":
        head += f", status: {m.get('status') or 'open'}"
    lines = [head + " ---"]
    if m.get("subject"):
        lines.append(f"Subject: {m['subject']}")
    body = str(m.get("body") or "").strip()
    if body:
        lines.append(body[:BODY_CHARS])
    for i, ts in enumerate(m.get("term_sheets") or []):
        terms = ts.get("terms", ts) or {}
        if m.get("from") == "buyer":
            lines.append(f"[our term sheet {i + 1}: {_terms_line(terms)}]")
        else:
            label = "standing term sheet" if i == 0 else f"alternative sheet {i}"
            lines.append(f"[{label} {ts.get('term_sheet_id')}: {_terms_line(terms)}]")
    if m.get("missing_fields"):
        lines.append(f"[fields its sheet leaves out (they close on its standard terms): {m['missing_fields']}]")
    if m.get("claim_results"):
        results = ", ".join(f"argument {r.get('index')}: {r.get('result')}" for r in m["claim_results"])
        lines.append(f"[how the data we cited checked out: {results}]")
    if m.get("signals"):
        lines.append(f"[signals: {m['signals']}]")
    for att in m.get("attachments") or []:
        text = str(att.get("text") or "").strip()
        lines.append(
            f"[attachment {att.get('name')}]" + (f"\n{text[:ATTACHMENT_CHARS]}" if text else " (not readable)")
        )
    if m.get("from") == "buyer" and m.get("refs"):
        lines.append(f"[data IDs we cited: {', '.join(m['refs'])}]")
    return "\n".join(lines)


def transcript(events: list[dict], last_n: int | None = None) -> str:
    """The whole thread as plain text, oldest first (or only its last `last_n` messages)."""
    numbered = list(enumerate(events, 1))
    if last_n is not None:
        numbered = numbered[-last_n:]
    return "\n\n".join(render_message(m, n) for n, m in numbered)


def latest_supplier_email(events: list[dict]) -> str:
    for n in range(len(events), 0, -1):
        if events[n - 1].get("from") != "buyer":
            return render_message(events[n - 1], n)
    return ""


def ended(events: list[dict]) -> bool:
    last = next((m for m in reversed(events) if m.get("from") != "buyer"), {})
    return last.get("status") in ENDED


# ---- templates (rule fallback; the guard checks them like any other e-mail) ----------------------------------

GREETING = {"en": "Dear Sir or Madam,", "de": "Sehr geehrte Damen und Herren,"}
SIGN = {
    "en": "Kind regards\nHollerau Pumpenwerke, Purchasing",
    "de": "Mit freundlichen Grüßen\nHollerau Pumpenwerke, Einkauf",
}
TEXT = {
    "en": {
        "first": "thank you for your offer. We have reviewed it carefully and cannot accept it as it stands.",
        "next": "thank you for your reply and for the step you have taken.",
        "final": "thank you for your final offer. We have considered it carefully.",
        "packages": "Attached are our proposals; each one balances price against terms that may suit you better.",
        "one": "Attached is our proposal.",
        "question": "Could you send us a breakdown of the cost elements behind your price?",
        "confirm": "thank you - we confirm the package as stated in your alternative offer, attached once more as "
        "our proposal. We look forward to your confirmation.",
    },
    "de": {
        "first": "vielen Dank für Ihr Angebot. Wir haben es sorgfältig geprüft und können es so nicht annehmen.",
        "next": "vielen Dank für Ihre Antwort und für Ihren Schritt auf uns zu.",
        "final": "vielen Dank für Ihr letztes Angebot. Wir haben es sorgfältig geprüft.",
        "packages": "Anbei unsere Vorschläge; jeder verbindet den Preis mit Konditionen, die Ihnen entgegenkommen.",
        "one": "Anbei unser Vorschlag.",
        "question": "Könnten Sie uns die Kostenbestandteile hinter Ihrem Preis aufschlüsseln?",
        "confirm": "vielen Dank - wir bestätigen das Paket aus Ihrem Alternativangebot, nochmals als unser "
        "Vorschlag beigefügt. Wir freuen uns auf Ihre Bestätigung.",
    },
}


def subject_for(events: list[dict], title: str) -> str:
    last = next((m for m in reversed(events) if m.get("from") != "buyer"), {})
    subject = str(last.get("subject") or "").strip() or title or "Your offer"
    return (subject if subject.lower().startswith(("re:", "aw:")) else f"Re: {subject}")[:300]


def template_body(lang: str, kind: str, n_sheets: int, claims: list[str], ask_breakdown: bool) -> str:
    t = TEXT.get(lang, TEXT["en"])
    parts = [GREETING.get(lang, GREETING["en"]), "", t.get(kind, t["next"])]
    if claims:
        parts += ["", *claims]
    if kind != "confirm" and n_sheets:
        parts += ["", t["packages"] if n_sheets > 1 else t["one"]]
    if ask_breakdown or not n_sheets:
        parts += ["", t["question"]]
    parts += ["", SIGN.get(lang, SIGN["en"])]
    return "\n".join(parts)
