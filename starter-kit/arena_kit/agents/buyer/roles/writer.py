"""Writer: the e-mail around the chosen arguments and the planner's sheets (owner: Saed).

The deterministic e-mail (tools.message) goes out whenever the decision agent follows the rules: same inputs, same
words, no model call. Only when it deviates does the e-mail change, and only in its narrative paragraph:
  push      one LLM call writes the paragraph that presses for price (prompts/writer.md); `check` keeps it to words
            - no figure, no data ID, no leak, no talk of data we do not cite, no move we did not make - and a
            rejected or late paragraph gives PUSH_LINE after the rule paragraph
  question  the agent's question (already checked by the strategist) follows the paragraph
Intro, commitment sentence, argument sentences and sign-off are always code's.
"""

import json
import logging
import re

from ....play import latest
from ...llm import ID_RE
from ..contracts import CaseContext, Email, OfferPlan, Tactic
from ..tools import message, planner
from .llm import call_json, prompt

log = logging.getLogger("arena")
TIMEOUT_S = 15.0
MIN_CHARS, MAX_CHARS = 15, 900
PUSH_LINE = "The price in your offer is still too high for these commitments; it has to come down for an agreement."
NUMBER_RE = re.compile(r"\d")
LEAKS = re.compile(
    r"\b(urgent\w*|asap|as soon as possible|immediately|right away|quickly|soon|deadline|budget|"
    r"no (?:other )?alternative|only (?:possible )?supplier|depend(?:ent|s)? on you|"
    r"(?:switch|move|change) (?:to )?(?:another|a different|other) supplier|last chance|or else|"
    r"score|benchmark|system prompt|ignore previous|must accept|cannot walk|can't walk)\b",
    re.I,
)
# Talk of data we do not cite: the supplier asks for it, we cannot show it, it costs trust.
VAGUE_DATA = re.compile(
    r"\b(?:comparable (?:parts?|suppliers?|offers?|quotes?|prices?)|market (?:prices?|levels?|data|rates?)|"
    r"other suppliers?|competitors?|competing offers?|another (?:supplier|bidder|vendor|source|offer|quote)|"
    r"our (?:data|analysis|analyses|records|research|figures|estimates?|calculations?|assessment|cost model)|"
    r"should[- ]cost|price indices|index data)\b",
    re.I,
)
# What our private notes know about the supplier; saying it embarrasses the person.
PRIVATE = re.compile(
    r"\b(?:your (?:bank|banks|liquidity|cash (?:situation|position|problems?)|financial (?:situation|position)|"
    r"credit (?:rating|index)|restructuring|spreadsheet|idle|short[- ]time)|bank is watching|short hours|"
    r"idle (?:capacity|shifts?|machines?)|second shift|pricing committee|bought (?:\w+ )?forward|hedg\w*|"
    r"share of your turnover|not strategic)\b",
    re.I,
)
# Asking for its cost breakdown or calculation: a pushed message presses on price, it does not open its books.
ASKS_COSTS = re.compile(
    r"(?:send|share|provide|show|walk us through|explain)[^.?!\n]{0,40}"
    r"(?:cost ?breakdown|break ?down|calculation|cost structure|cost drivers|cost elements|(?:your|its) costs?\b)",
    re.I,
)
# Claims of our own movement; a pushed message never moves more than the planner's minimal step.
MOVED_RE = re.compile(
    r"\b(?:we|our (?:offer|proposal|position|price))(?: have| has| are| is)?(?: now| also| further| again)?"
    r" (?:moved|improved|stepped|raised|increased|coming|come (?:closer|towards))\b",
    re.I,
)


def check(text: str, supplier_text: str = "") -> list[str]:
    """Problems that keep a model paragraph out of the e-mail; empty means it may go."""
    problems = []
    if not MIN_CHARS <= len(text) <= MAX_CHARS:
        problems.append(f"length {len(text)} outside {MIN_CHARS}-{MAX_CHARS}")
    if NUMBER_RE.search(text):
        problems.append("a figure: the sheets and argument sentences carry every number")
    if ID_RE.search(text):
        problems.append("a data ID: arguments are code's")
    if leak := LEAKS.search(text):
        problems.append(f"leak: {leak.group(0)!r}")
    vague = {m.group(0).lower() for m in VAGUE_DATA.finditer(text)} - {
        m.group(0).lower() for m in VAGUE_DATA.finditer(supplier_text)
    }
    if vague:
        problems.append(f"data talk without a claim: {sorted(vague)[:3]}")
    private = {m.group(0).lower() for m in PRIVATE.finditer(text)} - {
        m.group(0).lower() for m in PRIVATE.finditer(supplier_text)
    }
    if private:
        problems.append(f"reveals our notes about the supplier: {sorted(private)[:3]}")
    if ask := ASKS_COSTS.search(text):
        problems.append(f"asks for its costs: {ask.group(0)!r}")
    if moved := MOVED_RE.search(text):
        problems.append(f"claims a move: {moved.group(0)!r}")
    return problems


def rule_email(ctx: CaseContext, tactic: Tactic, plan: OfferPlan, events: list[dict], paragraph: str | None = None
               ) -> Email:  # fmt: skip
    """The deterministic e-mail; `paragraph` replaces the rule narrative (a pushed message); the agent's question
    follows the narrative."""
    sid = tactic.supplier_id
    available = ctx.claims.get(sid, ())
    chosen = [available[i] for i in tactic.claims if 0 <= i < len(available)][:3]
    narrative = paragraph or message.narrative(ctx.case, ctx.documents.get(sid, ""), events)
    if tactic.question:
        narrative = f"{narrative} {tactic.question}"
    brief = planner.commercial_brief(ctx.case, list(plan.term_sheets), events, ctx.unit)
    body = message.compose(narrative, brief, [c.sentence for c in chosen])
    refs = tuple(dict.fromkeys(ref for c in chosen for ref in c.refs))
    return Email(subject=message.subject(ctx.case), body=body, refs=refs)


class LLMWriter:
    def __init__(self, complete=None):
        self.complete = complete
        self.system = prompt("writer")
        self.last_problems: list[str] = []  # why the last model paragraph was rejected (logs)
        self.last_used_model = False

    def write(self, ctx: CaseContext, tactic: Tactic, plan: OfferPlan, events: list[dict]) -> Email:
        self.last_problems, self.last_used_model = [], False
        if tactic.price != "push":
            return rule_email(ctx, tactic, plan, events)
        rule = message.narrative(ctx.case, ctx.documents.get(tactic.supplier_id, ""), events)
        fallback = f"{rule} {PUSH_LINE}"
        if self.complete is None:
            return rule_email(ctx, tactic, plan, events, fallback)
        supplier_text = str(latest(events).get("body") or "")[:1500]
        dossier = ctx.dossiers.get(tactic.supplier_id)
        user = json.dumps(
            {
                "case_title": ctx.case["title"],
                "category_goal": ctx.case["buyer"].get("category_goal"),
                "persona": dossier.persona if dossier else "unknown",
                "supplier_status": latest(events).get("status"),
                "supplier_last_email": supplier_text,
                "rationale": tactic.rationale,
                "rule_paragraph": rule,
            },
            ensure_ascii=False,
        )
        reply = call_json(self.complete, self.system, user, TIMEOUT_S, name="writer")
        text = reply.get("paragraph") if isinstance(reply, dict) else None
        text = text.strip() if isinstance(text, str) else ""
        problems = check(text, supplier_text) if text else ["no paragraph"]
        if problems:
            self.last_problems = problems
            log.warning("%s: writer paragraph rejected (%s) - rule paragraph", tactic.supplier_id, "; ".join(problems))
            return rule_email(ctx, tactic, plan, events, fallback)
        self.last_used_model = True
        return rule_email(ctx, tactic, plan, events, text)
