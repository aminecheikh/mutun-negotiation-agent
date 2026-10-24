"""Writer: the e-mail around the chosen claims and term sheets (owner: Saed).

v1: one LLM call with prompts/writer.md (tone per persona), then `check` before anything leaves: every claim
sentence verbatim, no number or data ID of the model's own, no leak of urgency, budget or a missing
alternative. A failed check, a late model or no model gives the template, which follows the same rules.
Replays showed why: one wrong scorecard figure "costs trust" for the rest of the case (P01, P05).
"""

import json
import logging
import re

from ....play import latest
from ...llm import ID_RE
from ..contracts import CaseContext, Email, OfferPlan, Tactic
from ..observe import detect_language
from .llm import call_json, prompt

log = logging.getLogger("arena")
TIMEOUT_S = 15.0
MAX_WORDS = 180  # the prompt asks for 150; claim sentences with their IDs are long
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
LEAKS = re.compile(
    r"\b(urgent\w*|asap|as soon as possible|immediately|right away|quickly|soon|deadline|budget|"
    r"no (?:other )?alternative|only (?:possible )?supplier|depend(?:ent|s)? on you|"
    r"(?:switch|move|change) (?:to )?(?:another|a different|other) supplier|last chance|or else|"
    r"dringend\w*|eilig\w*|so schnell wie möglich|sofort\w*|umgehend\w*|zeitnah\w*|frist\w*|"
    r"keine (?:andere )?alternative|einzige[nr]? (?:lieferant\w*|anbieter\w*)|(?:auf sie )?angewiesen|"
    r"(?:zu )?einem anderen (?:lieferanten|anbieter) wechseln|letzte chance)\b",
    re.I,
)
# Talk of data we do not cite: the supplier asks for it, we cannot show it, it costs trust (D02, Amine's ask).
# Allowed inside claim sentences and when the supplier used the same words first.
VAGUE_DATA = re.compile(
    r"\b(?:comparable (?:parts?|shafts?|castings?|suppliers?|offers?|quotes?|prices?)|benchmarks?|"
    r"market (?:prices?|levels?|data|rates?)|other suppliers?|competitors?|competing offers?|"
    r"our (?:data|analysis|analyses|records|research|figures|estimates?|calculations?|assessment|cost model)|"
    r"should[- ]cost|cost picture|price indices|index data|"
    r"vergleichbare[nr]? (?:teile\w*|wellen|gussteile\w*|lieferanten|angebote\w*|preise\w*)|marktpreis\w*|"
    r"marktniveau\w*|andere[nr]? (?:lieferanten|anbieter\w*)|wettbewerber\w*|konkurrenz\w*|"
    r"unsere[rnm]? (?:daten|analysen?|unterlagen|berechnung\w*|kalkulation\w*|einschätzung\w*|kostenmodell\w*)|"
    r"preisindizes|indexdaten)\b",
    re.I,
)
# What our private notes know about the supplier; saying it embarrasses the person. Allowed only after the
# supplier raised it itself.
PRIVATE = re.compile(
    r"\b(?:your (?:bank|banks|liquidity|cash (?:situation|position|problems?)|financial (?:situation|position)|"
    r"credit (?:rating|index)|restructuring|spreadsheet|idle|short[- ]time)|bank is watching|short hours|"
    r"idle (?:capacity|shifts?|machines?)|second shift|pricing committee|bought (?:\w+ )?forward|hedg\w*|"
    r"share of your turnover|not strategic|"
    r"ihre[rnm]? (?:bank\w*|liquidität|finanzlage|finanziellen? (?:lage|situation)|bonität|umstrukturierung)|"
    r"kurzarbeit|leerstehende\w* maschinen|zweite\w* schicht|preiskomitee|absicherung\w*|"
    r"anteil (?:an )?ihre[ms] umsatz\w*|nicht strategisch)\b",
    re.I,
)
SIGNATURES = {
    "en": "Kind regards\nHollerau Pumpenwerke, Purchasing",
    "de": "Mit freundlichen Grüßen\nHollerau Pumpenwerke, Einkauf",
}

# The template's tone per persona: an opening that acknowledges the supplier the way its persona wants.
OPENINGS = {
    "partner": "thank you for your offer and for the reliable cooperation over many years.",
    "cash_short": "thank you for your offer. We have looked for a solution that also helps your cash flow.",
    "full_order_book": "thank you for your offer. We value a reliable, well-planned supply from your side.",
    "monopolist": "thank you for your offer.",
    "price_fighter": "thank you for your offer. We see good potential for more business between us.",
    "unknown": "thank you for your offer.",
}
# The German template: same structure, formal "Sie" (issues.md 9: answer in the supplier's language).
OPENINGS_DE = {
    "partner": "vielen Dank für Ihr Angebot und die verlässliche Zusammenarbeit über viele Jahre.",
    "cash_short": "vielen Dank für Ihr Angebot. Wir haben eine Lösung gesucht, die auch Ihrem Zahlungseingang hilft.",
    "full_order_book": "vielen Dank für Ihr Angebot. Uns ist eine verlässliche, gut geplante Versorgung wichtig.",
    "monopolist": "vielen Dank für Ihr Angebot.",
    "price_fighter": "vielen Dank für Ihr Angebot. Wir sehen gutes Potenzial für mehr gemeinsames Geschäft.",
    "unknown": "vielen Dank für Ihr Angebot.",
}
TEXT = {
    "en": {
        "salutation": "Dear Sir or Madam,",
        "revised": ("your offer", "your revised offer and the step you made"),
        "many": "We cannot accept your offer as it stands. Please find our counter-proposal attached, in packages.",
        "one": "We cannot accept your offer as it stands. Please find our counter-proposal attached.",
        "moved": "We have moved towards your position.",
        "and": " and ",
        "single": "It offers {}.",
        "ordinal": ("The first package", "The second package", "The third package"),
        "offers": "offers",
    },
    "de": {
        "salutation": "Sehr geehrte Damen und Herren,",
        "revised": ("Ihr Angebot", "Ihr überarbeitetes Angebot und Ihren Schritt auf uns zu"),
        "many": "Ihr Angebot können wir so nicht annehmen. Anbei finden Sie unseren Gegenvorschlag in Paketen.",
        "one": "Ihr Angebot können wir so nicht annehmen. Anbei finden Sie unseren Gegenvorschlag.",
        "moved": "Wir sind Ihnen entgegengekommen.",
        "and": " und ",
        "single": "Es bietet {}.",
        "ordinal": ("Das erste Paket", "Das zweite Paket", "Das dritte Paket"),
        "offers": "bietet",
        "question": "Wir freuen uns auf Ihre Rückmeldung zu unserem Vorschlag.",
    },
}
# (field, more, less): how a package differs from the supplier's standing sheet, in words without figures.
DIFFS = (
    ("payment_days", "a longer payment term", "faster payment for you"),
    ("contract_months", "a longer commitment", "a shorter term"),
    ("volume_per_year", "more volume for you", "a smaller volume"),
    ("effective_date", "a later start of the new price", "an earlier start of the new price"),
    ("lead_time_days", "more lead time for you", "a shorter lead time"),
    ("tooling_eur", "a higher tooling contribution", "a lower tooling contribution"),
    ("ppm_commitment", "a looser defect commitment", "a tighter defect commitment"),
    ("chargeback_eur_per_complaint", "a higher chargeback", "a lower chargeback"),
)
GERMAN_TRADES = {
    "a longer payment term": "ein längeres Zahlungsziel",
    "faster payment for you": "schnellere Zahlung für Sie",
    "a longer commitment": "eine längere Bindung",
    "a shorter term": "eine kürzere Laufzeit",
    "more volume for you": "mehr Volumen für Sie",
    "a smaller volume": "ein geringeres Volumen",
    "a later start of the new price": "einen späteren Beginn des neuen Preises",
    "an earlier start of the new price": "einen früheren Beginn des neuen Preises",
    "more lead time for you": "mehr Lieferzeit für Sie",
    "a shorter lead time": "eine kürzere Lieferzeit",
    "a higher tooling contribution": "einen höheren Werkzeugkostenanteil",
    "a lower tooling contribution": "einen niedrigeren Werkzeugkostenanteil",
    "a looser defect commitment": "eine weniger strenge Fehlerquote",
    "a tighter defect commitment": "eine strengere Fehlerquote",
    "a higher chargeback": "eine höhere Belastung je Reklamation",
    "a lower chargeback": "eine niedrigere Belastung je Reklamation",
    "different delivery terms": "andere Lieferbedingungen",
    "a price linked to the index": "einen an den Index gekoppelten Preis",
}


def language_of(events: list[dict]) -> str:
    """'de' when the supplier's last e-mail is German, else 'en' (mixed e-mails get English)."""
    return "de" if detect_language(str(latest(events).get("body") or "")) == "de" else "en"


def describe(sheet: dict, theirs: dict) -> list[str]:
    """What a package trades against the supplier's standing sheet - only what is really in it."""
    out = []
    for field, more, less in DIFFS:
        a, b = sheet.get(field), theirs.get(field)
        if a is None or b is None or a == b:
            continue
        try:
            out.append(more if a > b else less)
        except TypeError:
            continue
    if sheet.get("incoterm") and theirs.get("incoterm") and sheet["incoterm"] != theirs["incoterm"]:
        out.append("different delivery terms")
    if sheet.get("index_clause") and not theirs.get("index_clause"):
        out.append("a price linked to the index")
    return out


def _price(sheet: dict | None) -> float | None:
    try:
        return float(sheet["unit_price"]) if sheet and sheet.get("unit_price") is not None else None
    except (TypeError, ValueError):
        return None


def movement(plan: OfferPlan, events: list[dict]) -> dict:
    """Facts about who moved, for the e-mail: did the supplier come down, did we come up since our last offer,
    and what each package trades. No figures - the term sheets carry them."""
    theirs = [m for m in events if m.get("from") != "buyer" and m.get("term_sheets")]
    ours = [m for m in events if m.get("from") == "buyer" and m.get("term_sheets")]
    standing = (theirs[-1]["term_sheets"][0].get("terms") or {}) if theirs else {}
    prev = _price(theirs[-2]["term_sheets"][0].get("terms")) if len(theirs) >= 2 else None
    now = _price(standing)
    ours_before = [_price(t.get("terms", t)) for t in ours[-1]["term_sheets"]] if ours else []
    ours_before = [p for p in ours_before if p is not None]
    ours_now = [p for p in (_price(s) for s in plan.term_sheets) if p is not None]
    return {
        "supplier_moved": prev is not None and now is not None and now < prev,
        "we_moved": bool(ours_before and ours_now and min(ours_now) > max(ours_before)),
        "packages": [describe(s, standing) for s in plan.term_sheets],
    }


def package_lines(packages: list[list[str]], language: str = "en") -> list[str]:
    text = TEXT[language]
    words = [[GERMAN_TRADES.get(t, t) for t in p] if language == "de" else p for p in packages]
    if len(words) <= 1:
        trades = words[0] if words else []
        return [text["single"].format(text["and"].join(trades))] if trades else []
    return [f"{text['ordinal'][i]} {text['offers']} {text['and'].join(t)}." for i, t in enumerate(words[:3]) if t]


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in NUMBER_RE.findall(text)}


# Asking for its cost breakdown or calculation (Saeb: never - every e-mail is a logical counter-offer instead).
ASKS_COSTS = re.compile(
    r"(?:send|share|provide|show|walk us through|explain)[^.?!\n]{0,40}"
    r"(?:cost ?breakdown|break ?down|calculation|cost structure)|"
    r"(?:kostenaufschl.sselung|kalkulation)[^.?!\n]{0,40}\?",
    re.I,
)


def check(body: str, sentences: list[str], allowed_refs: set[str], supplier_text: str, title: str) -> list[str]:
    """Problems that must stop an e-mail; empty means it may be sent. Claim sentences are the only source
    of our figures and IDs; a figure the supplier wrote itself may be repeated."""
    problems = [f"claim sentence changed or missing: {s[:60]}" for s in sentences if s not in body]
    rest = body
    for s in sentences:
        rest = rest.replace(s, " ")
    extra = _numbers(rest) - _numbers(supplier_text) - _numbers(title)
    if extra:
        problems.append(f"numbers not from claims or the supplier: {sorted(extra)[:5]}")
    ids = {m.group(0) for m in ID_RE.finditer(rest)} - allowed_refs - set(ID_RE.findall(supplier_text))
    if ids:
        problems.append(f"data IDs not from the chosen claims: {sorted(ids)[:5]}")
    if leak := LEAKS.search(rest):
        problems.append(f"leak: {leak.group(0)!r}")
    vague = {m.group(0).lower() for m in VAGUE_DATA.finditer(rest)} - {
        m.group(0).lower() for m in VAGUE_DATA.finditer(supplier_text)
    }
    if vague:
        problems.append(f"data talk without a claim: {sorted(vague)[:3]}")
    private = {m.group(0).lower() for m in PRIVATE.finditer(rest)} - {
        m.group(0).lower() for m in PRIVATE.finditer(supplier_text)
    }
    if private:
        problems.append(f"reveals our notes about the supplier: {sorted(private)[:3]}")
    if len(body.split()) > MAX_WORDS:
        problems.append(f"too long: {len(body.split())} words")
    if ask := ASKS_COSTS.search(body):
        problems.append(f"asks for its costs: {ask.group(0)!r}")
    return problems


def subject_for(ctx: CaseContext, events: list[dict]) -> str:
    last = str(latest(events).get("subject") or "").strip()
    while last.lower().startswith("re:"):
        last = last[3:].strip()
    return f"Re: {last or ctx.case['title']}"[:300]


def template(ctx: CaseContext, tactic: Tactic, plan: OfferPlan, claims: list[str], events=None) -> Email:
    """No model, a late model or a failed check: a safe e-mail that still fits the persona."""
    dossier = ctx.dossiers.get(tactic.supplier_id)
    persona = dossier.persona if dossier else "unknown"
    moved = movement(plan, events or [])
    language = language_of(events or [])
    text, openings = TEXT[language], (OPENINGS_DE if language == "de" else OPENINGS)
    opening = openings.get(persona, openings["unknown"])
    if moved["supplier_moved"]:
        opening = opening.replace(*text["revised"], 1)
    lines = [text["salutation"], "", opening]
    if claims:
        lines += ["", *claims]
    lines += ["", text["many"] if len(plan.term_sheets) > 1 else text["one"]]
    if moved["we_moved"]:
        lines.append(text["moved"])
    lines += package_lines(moved["packages"], language)
    question = tactic.question
    if question and language == "de" and detect_language(question) != "de":
        question = text["question"]  # the strategist asks in English; no mixed-language e-mail
    if question:
        lines += ["", question]
    lines += ["", SIGNATURES[language]]
    subject = subject_for(ctx, events) if events else f"Re: {ctx.case['title']}"
    return Email(subject=subject, body="\n".join(lines))


# Claims of our own movement; only allowed when the packages really are above our previous offer.
_AUX = r"(?: have| has| are| is)?(?: now| also| further| again| already)?"
MOVED_RE = re.compile(
    rf"\b(?:we|our (?:offer|proposal|position|price)){_AUX}"
    r" (?:moved|improved|stepped|coming|come (?:closer|towards))\b|"
    rf"\bour (?:offer|proposal|position|price){_AUX} (?:raised|increased)\b|"
    rf"\bwe{_AUX} (?:raised|increased) (?:our|the) (?:offer|price|proposal|bid)\b|"  # not "the points we raised"
    r"\bwir sind (?:ihnen )?(?:entgegengekommen|nachgekommen)|"
    r"\bwir haben (?:unser angebot|unseren vorschlag|unseren preis) (?:verbessert|erhöht|angehoben)",
    re.I,
)  # "we have moved towards you" - not "we appreciate that you have moved"


SUBJECT_LINE = re.compile(r"^\W*(?:subject|betreff)\W*:\W*(.+?)\W*$", re.I | re.M)


def email_from_text(text: str) -> dict | None:
    """A reply written as an e-mail instead of JSON ("**Subject:** ..." then the body): the same e-mail as JSON.
    It still goes through `check` like any model answer."""
    m = SUBJECT_LINE.search(text)
    if not m:
        return None
    body = text[m.end() :].strip().strip("`").strip()
    body = re.sub(r"^\W*(?:body|text)\W*:\W*", "", body, flags=re.I).strip()
    return {"subject": m.group(1).strip(), "body": body} if body else None


class LLMWriter:
    def __init__(self, complete=None):
        self.complete = complete
        self.system = prompt("writer")
        self.last_problems: list[str] = []  # why the last model e-mail was rejected (logs, bench)
        self.last_used_model = False

    def write(self, ctx: CaseContext, tactic: Tactic, plan: OfferPlan, events: list[dict]) -> Email:
        sid = tactic.supplier_id
        available = ctx.claims.get(sid, ())
        chosen = [available[i] for i in tactic.claims if 0 <= i < len(available)][:3]
        sentences = [c.sentence for c in chosen]
        refs = tuple(dict.fromkeys(ref for c in chosen for ref in c.refs))
        fallback = template(ctx, tactic, plan, sentences, events)
        fallback = Email(fallback.subject, fallback.body, refs)

        last = latest(events)
        supplier_text = str(last.get("body", ""))[:1500]
        dossier = ctx.dossiers.get(sid)
        moved = movement(plan, events)
        user = json.dumps(
            {
                "case_title": ctx.case["title"],
                "briefing": ctx.case.get("briefing", ""),
                "round": sum(m.get("from") == "buyer" for m in events) + 1,
                "round_limit": ctx.case.get("round_limit", 10),
                "language": language_of(events),
                "supplier": {
                    "name": dossier.name if dossier else sid,
                    "persona": dossier.persona if dossier else "unknown",
                    "levers": list(dossier.levers) if dossier else [],
                    "evidence": list(dossier.evidence)[:6] if dossier else [],
                },
                "supplier_status": last.get("status"),
                "supplier_last_email": supplier_text,
                "tactic": {"question": tactic.question, "rationale": tactic.rationale},
                "movement": moved,
                "claim_sentences": sentences,
                "term_sheets": plan.term_sheets,
            },
            ensure_ascii=False,
            default=str,
        )
        self.last_problems, self.last_used_model = [], False
        reply = call_json(self.complete, self.system, user, TIMEOUT_S, salvage=email_from_text)
        if not isinstance(reply, dict) or not isinstance(reply.get("body"), str) or not reply["body"].strip():
            return fallback
        body = reply["body"].strip()
        # Years from our own notes may be recalled ("you held your price in 2022"); other figures there are not
        # arena-checked claims and stay out.
        years = " ".join(YEAR_RE.findall(" ".join(dossier.evidence))) if dossier else ""
        allowed_text = f"{supplier_text}\n{tactic.question or ''}\n{years}"
        problems = check(body, sentences, set(refs), allowed_text, ctx.case["title"])
        if not moved["we_moved"] and (claim := MOVED_RE.search(body)):
            problems.append(f"claims a move we did not make: {claim.group(0)!r}")
        if problems:
            self.last_problems = problems
            log.warning("%s: writer e-mail rejected (%s) - using the template", sid, "; ".join(problems))
            return fallback
        self.last_used_model = True
        subject = str(reply.get("subject") or "").strip() or fallback.subject
        return Email(subject=subject[:300], body=body, refs=refs)
