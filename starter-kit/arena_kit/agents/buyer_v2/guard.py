"""The hard rules (deterministic): nothing reaches a supplier unless it passes here.

Counter offers - every sheet we send must be a deal we would sign if the supplier says "agreed":
  1. complete and valid: exactly the negotiable fields, values the arena accepts
  2. never above the supplier's offer: unit_price <= its standing unit_price
  3. always less than its offer: C(sheet) < C(its standing sheet) - a counter asks for something
  4. credible: unit_price >= the lowest credible price for this supplier (tools.credibility), so we never lowball
  5. never take back: C(sheet) >= C of our latest offer - each offer at least as good for the supplier
     as the last one (reneging costs trust)
  6. signable: keeps every must-have, not dearer than the best opening or our alternative
A price outside 2/4/5 is moved to the nearest price that satisfies them; a sheet that cannot be fixed is dropped.

E-mails - verified arguments verbatim (inserted from their tokens), no data ID that is not part of one, no
figure that is neither in our sheets, our arguments nor the supplier's own e-mails, no leak of urgency, budget or
a missing alternative, no word about other suppliers, bounded length.
"""

import math
import re
from dataclasses import dataclass

from ...cost import must_haves_ok, snap
from ..llm import ID_RE
from . import facts as F
from .facts import CaseState, ThreadFacts

LEAKS = re.compile(
    r"\b(urgent\w*|asap|as soon as possible|immediately|right away|deadline|our budget|budget|"
    r"no (?:other )?alternative|no other (?:supplier|source)|only (?:possible )?supplier|depend(?:ent|s)? on you|"
    r"last chance|or else|competitor\w*|other suppliers?|another supplier|rival\w*|other bidders?|"
    r"dringend\w*|eilig\w*|so schnell wie m[öo]glich|sofort\w*|umgehend\w*|frist\w*|keine (?:andere )?alternative|"
    r"einzige[nr]? (?:lieferant\w*|anbieter\w*)|angewiesen|letzte chance|wettbewerber\w*|"
    r"andere[nrm]? (?:lieferant\w*|anbieter\w*))\b",
    re.I,
)
NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*(?![\w])")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ\"'(\[])")
TOKEN = re.compile(r"\[\[\s*(A\d+)\s*\]\]|\{\{\s*(A\d+)\s*\}\}|\[(A\d+)\]")
MIN_WORDS = 20


@dataclass(frozen=True)
class Checked:
    sheets: list[dict]
    notes: list[str]


# ---- counter offers ------------------------------------------------------------------------------------------


def normalise(state: CaseState, sheet: dict, f: ThreadFacts) -> dict | None:
    """Exactly the negotiable fields with values the arena accepts (an option or the supplier's standard)."""
    case, standing = state.case, (f.standing.terms if f.standing else {})
    options = case.get("term_options") or {}
    plan = F.complete(state, {"unit_price": 1.0})  # the buyer's plan for a field nobody has stated
    out = {}
    for field in state.negotiable:
        if field == "unit_price":
            price = sheet.get("unit_price")
            if not isinstance(price, int | float) or isinstance(price, bool) or not math.isfinite(price) or price <= 0:
                return None
            out[field] = round(float(price), 2)
            continue
        if field in sheet:
            value = sheet[field]
        elif field in standing:
            value = standing[field]
        elif field in f.std:
            value = f.std[field]
        else:
            value = plan.get(field)
        opts = options.get(field)
        if opts and value not in opts and value != f.std.get(field):
            value = snap(case, field, value) if value is not None else opts[0]
        out[field] = value
    return out


def price_for_cost(state: CaseState, sid: str, sheet: dict, std: dict, target: float, lo: float, hi: float):
    """The lowest unit price in [lo, hi] at which C(sheet) >= target (C rises with the price), or None."""

    def c_at(p):
        return F.cost(state, sid, {**sheet, "unit_price": round(p, 2)}, std)

    if c_at(hi) < target:
        return None
    if c_at(lo) >= target:
        return round(lo, 2)
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if c_at(mid) < target else (lo, mid)
    return round(math.ceil(hi * 100 - 1e-9) / 100, 2)


def check_counter(state: CaseState, f: ThreadFacts, proposed: list[dict]) -> Checked:
    sid, cfg = f.supplier_id, state.config
    notes: list[str] = []
    kept: list[dict] = []
    cap = f.standing.price if f.standing else None
    cap_c = f.standing.c if f.standing else math.inf
    cred = getattr(state.ctx, "credibility", {}).get(sid)
    floor = cred.lowest_price if cred is not None and cred.lowest_price else None
    for n, raw in enumerate(proposed[:6], 1):
        sheet = normalise(state, raw, f)
        if sheet is None:
            notes.append(f"sheet {n} dropped: no valid unit price")
            continue
        p = sheet["unit_price"]
        if cap is not None and p > cap:
            notes.append(f"sheet {n}: price {p} above its offer {cap}, set to {cap}")
            p = cap
        if floor is not None and cap is not None and p < floor <= cap:
            notes.append(f"sheet {n}: price {p} under the credible floor {floor}, raised")
            p = floor
        sheet["unit_price"] = round(p, 2)
        if not math.isinf(f.our_last_c) and cap is not None:
            c_now = F.cost(state, sid, sheet, f.std)
            if c_now < f.our_last_c * (1 - cfg.same_cost_rel):
                fixed = price_for_cost(state, sid, sheet, f.std, f.our_last_c, p, cap)
                if fixed is None:
                    notes.append(f"sheet {n} dropped: it would take back our earlier offer")
                    continue
                notes.append(f"sheet {n}: price {p} would take back our earlier offer, set to {fixed}")
                sheet["unit_price"] = fixed
        c = F.cost(state, sid, sheet, f.std)
        if math.isinf(c):
            notes.append(f"sheet {n} dropped: cannot be priced")
            continue
        if not math.isinf(cap_c) and c >= cap_c * (1 - 1e-9):
            notes.append(f"sheet {n} dropped: costs us {c:,.0f}, not less than its own offer ({cap_c:,.0f})")
            continue
        if not must_haves_ok(state.case, sheet, f.std):
            notes.append(f"sheet {n} dropped: breaks a must-have")
            continue
        if not math.isinf(state.c_open) and c > state.c_open:
            notes.append(f"sheet {n} dropped: dearer than the best opening quote")
            continue
        if state.c_alt is not None and c > state.c_alt:
            notes.append(f"sheet {n} dropped: dearer than our alternative")
            continue
        if any(F.same_terms(state.case, sheet, k, f.std, cfg.same_price_eps) for k in kept):
            continue
        kept.append(sheet)
        if len(kept) == 3:
            break
    return Checked(kept, notes)


def accept_ok(state: CaseState, f: ThreadFacts) -> tuple[bool, str]:
    """An accept is sent only for a signable standing sheet, never for the opening quote before our first message
    (it is the score's zero line) unless the supplier agreed to our terms."""
    if not f.acceptable:
        return False, f.why_not or "not acceptable"
    if f.our_rounds == 0 and f.status != "agreed":
        return False, "never accept the opening quote before our first answer"
    return True, ""


def accept_ours_ok(state: CaseState, f: ThreadFacts) -> tuple[bool, str]:
    """The supplier accepted our own offer: we take it. We sent that sheet as one we would sign, so only what can
    have changed since is checked - a refused ID, the must-haves on its closing terms, our alternative. (Its C
    against the best opening may drift with our estimate of the supplier; the offer was signable when we made it.)"""
    s = f.standing
    if s is None or not s.id or not f.accepted_ours:
        return False, "it did not accept our offer"
    if s.id in state.refused:
        return False, "the arena refused an accept of this sheet"
    if math.isinf(s.c) or not must_haves_ok(state.case, s.terms, f.std):
        return False, "its sheet breaks a must-have or cannot be priced"
    if state.c_alt is not None and s.c > state.c_alt:
        return False, "dearer than our alternative"
    return True, ""


def closable(state: CaseState, f: ThreadFacts) -> bool:
    """May the coordinator accept this thread's standing sheet: signable, or our own offer accepted by it."""
    return accept_ok(state, f)[0] or accept_ours_ok(state, f)[0]


# ---- e-mails ------------------------------------------------------------------------------------------------


def _numbers(text: str) -> set[float]:
    out = set()
    for m in NUMBER.findall(text or ""):
        raw = m.replace(",", "") if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", m) else m.replace(",", ".")
        for part in re.split(r"[^\d.]", raw):
            try:
                out.add(round(float(part), 2))
            except ValueError:
                continue
    return out


def _allowed_numbers(sheets: list[dict], claims: list[str], supplier_text: str) -> set[float]:
    text = " ".join(str(v) for s in sheets for v in s.values() if not isinstance(v, dict))
    clauses = " ".join(f"{v.get('material_share', 0) * 100} {v.get('deadband_pct')}" for s in sheets for v in s.values()
                       if isinstance(v, dict))  # fmt: skip
    allowed = _numbers(text) | _numbers(clauses) | _numbers(" ".join(claims)) | _numbers(supplier_text)
    return allowed | {float(n) for n in range(0, 11)}  # small counts ("two packages", "3 options") are words


def clean_email(
    state: CaseState,
    f: ThreadFacts,
    events: list[dict],
    body: str,
    tokens: list[str],
    sheets: list[dict],
    other_names: list[str],
) -> tuple[str | None, list[str], list[str]]:
    """(body or None, refs, notes). None: too little left after cleaning - the caller uses the template."""
    notes: list[str] = []
    usable = {a.token: a for a in F.arguments(state, f.supplier_id)}
    chosen = [t for t in dict.fromkeys(tokens or ()) if t in usable][: state.config.max_claims]
    if len(chosen) < len(set(tokens or ())):
        notes.append("unknown or already cited arguments dropped")
    sentences = {t: usable[t].sentence for t in chosen}
    placed: set[str] = set()

    def put(m: re.Match) -> str:
        token = next(g for g in m.groups() if g)
        if token in sentences and token not in placed:
            placed.add(token)
            return f"\n{sentences[token]}\n"
        return ""

    text = TOKEN.sub(put, str(body or "").replace("\r\n", "\n"))
    missing = [sentences[t] for t in chosen if t not in placed]
    supplier_text = "\n".join(str(m.get("body") or "") for m in events if m.get("from") != "buyer")
    claim_texts = list(sentences.values())
    allowed = _allowed_numbers(sheets, claim_texts, supplier_text)
    lowered_names = [n.lower() for n in other_names if n and len(n) > 3]
    lines = []
    for line in text.split("\n"):
        kept = []
        for sentence in SENTENCE_END.split(line):
            s = sentence.strip()
            if not s:
                continue
            if s in claim_texts:
                kept.append(s)
                continue
            if ID_RE.search(s):
                notes.append(f"removed (data ID outside a verified argument): {s[:60]}")
                continue
            if leak := LEAKS.search(s):
                notes.append(f"removed (leak {leak.group(0)!r}): {s[:60]}")
                continue
            if any(name in s.lower() for name in lowered_names):
                notes.append(f"removed (names another supplier): {s[:60]}")
                continue
            extra = _numbers(s) - allowed
            if extra:
                notes.append(f"removed (figures {sorted(extra)[:3]} not in our sheets or arguments): {s[:60]}")
                continue
            kept.append(s)
        lines.append(" ".join(kept) if kept else "" if line.strip() else "")
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if missing:
        parts = cleaned.rsplit("\n\n", 1)
        block = "\n".join(missing)
        cleaned = f"{parts[0]}\n\n{block}\n\n{parts[1]}" if len(parts) == 2 else f"{cleaned}\n\n{block}"
    words = cleaned.split()
    if len(words) > state.config.max_email_words + sum(len(c.split()) for c in claim_texts):
        notes.append("shortened")
        cleaned = _shorten(cleaned, state.config.max_email_words + sum(len(c.split()) for c in claim_texts))
    if len(cleaned.split()) < MIN_WORDS:
        return None, [], notes + ["too little left after the checks"]
    refs = [r for t in chosen for r in usable[t].refs]
    return cleaned[:7900], list(dict.fromkeys(refs)), notes


def _shorten(text: str, max_words: int) -> str:
    paragraphs = text.split("\n\n")
    sign = paragraphs[-1] if len(paragraphs) > 1 and len(paragraphs[-1].split()) <= 12 else ""
    body = paragraphs[:-1] if sign else paragraphs
    out, count = [], len(sign.split())
    for p in body:
        n = len(p.split())
        if count + n > max_words:
            break
        out.append(p)
        count += n
    return "\n\n".join([*out, sign] if sign else out)


def reserve(state: CaseState, message: dict, tokens_refs: list[str]) -> dict:
    """Under the case lock, right before the POST: drop arguments another thread cited meanwhile, mark ours used."""
    with state.lock:
        clash = [r for r in tokens_refs if r in state.burned]
        if clash:
            kept_lines = [ln for ln in message.get("body", "").split("\n") if not any(r in ln for r in clash)]
            message = {
                **message,
                "body": "\n".join(kept_lines),
                "refs": [r for r in message.get("refs", []) if r not in clash],
            }
        state.burned |= set(message.get("refs") or ())
        state.burned |= set(ID_RE.findall(message.get("body") or ""))
    return message
