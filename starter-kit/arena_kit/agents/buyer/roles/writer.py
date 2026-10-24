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
from .llm import call_json, prompt

log = logging.getLogger("arena")
TIMEOUT_S = 15.0
MAX_WORDS = 180  # the prompt asks for 150; claim sentences with their IDs are long
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
LEAKS = re.compile(
    r"\b(urgent\w*|asap|as soon as possible|immediately|right away|quickly|soon|deadline|budget|"
    r"no (?:other )?alternative|only (?:possible )?supplier|depend(?:ent|s)? on you|"
    r"(?:switch|move|change) (?:to )?(?:another|a different|other) supplier|last chance|or else)\b",
    re.I,
)
# Talk of data we do not cite: the supplier asks for it, we cannot show it, it costs trust (D02, Amine's ask).
# Allowed inside claim sentences and when the supplier used the same words first.
VAGUE_DATA = re.compile(
    r"\b(?:comparable (?:parts?|shafts?|castings?|suppliers?|offers?|quotes?|prices?)|benchmarks?|"
    r"market (?:prices?|levels?|data|rates?)|other suppliers?|competitors?|competing offers?|"
    r"our (?:data|analysis|analyses|records|research|figures|estimates?|calculations?|assessment|cost model)|"
    r"should[- ]cost|cost picture|price indices|index data)\b",
    re.I,
)
# What our private notes know about the supplier; saying it embarrasses the person. Allowed only after the
# supplier raised it itself.
PRIVATE = re.compile(
    r"\b(?:your (?:bank|banks|liquidity|cash (?:situation|position|problems?)|financial (?:situation|position)|"
    r"credit (?:rating|index)|restructuring|spreadsheet|idle|short[- ]time)|bank is watching|short hours|"
    r"idle (?:capacity|shifts?|machines?)|second shift|pricing committee|bought (?:\w+ )?forward|hedg\w*|"
    r"share of your turnover|not strategic)\b",
    re.I,
)
SIGNATURE = "Kind regards\nHollerau Pumpenwerke, Purchasing"

# The template's tone per persona: an opening that acknowledges the supplier the way its persona wants.
OPENINGS = {
    "partner": "thank you for your offer and for the reliable cooperation over many years.",
    "cash_short": "thank you for your offer. We have looked for a solution that also helps your cash flow.",
    "full_order_book": "thank you for your offer. We value a reliable, well-planned supply from your side.",
    "monopolist": "thank you for your offer.",
    "price_fighter": "thank you for your offer. We see good potential for more business between us.",
    "unknown": "thank you for your offer.",
}
ORDINALS = ("The first package", "The second package", "The third package")
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


def package_lines(packages: list[list[str]]) -> list[str]:
    if len(packages) <= 1:
        trades = packages[0] if packages else []
        return [f"It offers {' and '.join(trades)}."] if trades else []
    return [f"{ORDINALS[i]} offers {' and '.join(t)}." for i, t in enumerate(packages[:3]) if t]


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in NUMBER_RE.findall(text)}


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
    opening = OPENINGS.get(persona, OPENINGS["unknown"])
    if moved["supplier_moved"]:
        opening = opening.replace("your offer", "your revised offer and the step you made", 1)
    lines = ["Dear Sir or Madam,", "", opening]
    if claims:
        lines += ["", *claims]
    many = len(plan.term_sheets) > 1
    lines += ["", "Please find our alternative packages attached." if many else "Please find our proposal attached."]
    if moved["we_moved"]:
        lines.append("We have moved towards your position.")
    lines += package_lines(moved["packages"])
    if tactic.question:
        lines += ["", tactic.question]
    lines += ["", SIGNATURE]
    subject = subject_for(ctx, events) if events else f"Re: {ctx.case['title']}"
    return Email(subject=subject, body="\n".join(lines))


# Claims of our own movement; only allowed when the packages really are above our previous offer.
MOVED_RE = re.compile(
    r"\b(?:we|our (?:offer|proposal|position))\b[^.\n]{0,40}\b(?:have |has )?(?:moved|improved|raised|increased|"
    r"come (?:closer|towards)|stepped)\b",
    re.I,
)


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
        reply = call_json(self.complete, self.system, user, TIMEOUT_S)
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
