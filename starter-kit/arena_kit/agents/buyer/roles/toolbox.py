"""The strategist's tools: what it may call while it decides (owner: Saed; wiring: Jeddou).

Every tool is read-only and deterministic: it prices, previews or looks up, it never sends anything and never
changes the thread. Numbers come from the same code the orchestrator uses afterwards (cost.total_cost with our
supplier estimate, the offer engine, the data package), so what the model sees is what will happen. A tool that
gets a bad argument raises; llm.call_json_with_tools turns that into an error result the model can read.

  price_terms     C of any package with this supplier: its standing terms with the changes the model names
  preview_offer   the 1-3 term sheets the offer engine would send for a set of levers, and their C
  read_thread     the whole thread (the model's input holds only the supplier's last three e-mails)
  look_up_data    the data row behind a citable ID (dossier evidence, a claim's source)
"""

import json
import math

from ....cost import complete_offer, must_haves_ok, total_cost
from ....docs import standard_terms
from ....play import standing
from ..contracts import BehaviourRead, CaseContext, Tactic

BODY_CHARS = 1500
ATTACHMENT_CHARS = 3000
THREAD_CHARS = 15000
ROW_CHARS = 1500


SUPPLIER_ID = {"type": "string", "description": "the supplier_id of the thread"}


SCHEMAS = [
    {
        "name": "price_terms",
        "description": (
            "Total cost C in EUR of a package with this supplier: its standing term sheet with the changes you "
            "name (values are snapped to the case's allowed options). Returns the package, its C, the standing C, "
            "the difference, whether it keeps every must-have and beats our alternative, and the band's target "
            "C. Use it to compare packages before you choose levers; C includes the supplier-health charge."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "supplier_id": SUPPLIER_ID,
                "changes": {
                    "type": "object",
                    "description": 'negotiable fields and values, e.g. {"payment_days": 14, "contract_months": 24}',
                },
            },
            "required": ["supplier_id", "changes"],
        },
    },
    {
        "name": "preview_offer",
        "description": (
            "The 1-3 term sheets the offer engine would send this round if you chose these levers, each with "
            "its C. You do not set the price; this shows what your lever choice turns into."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "supplier_id": SUPPLIER_ID,
                "levers": {"type": "array", "items": {"type": "string"}, "description": "negotiable fields"},
            },
            "required": ["supplier_id", "levers"],
        },
    },
    {
        "name": "read_thread",
        "description": (
            "The whole thread with this supplier, oldest first: every e-mail (ours and its), its term sheets, "
            "the claim results and longer attachment text. Your input holds only its last three e-mails."
        ),
        "input_schema": {"type": "object", "properties": {"supplier_id": SUPPLIER_ID}, "required": ["supplier_id"]},
    },
    {
        "name": "look_up_data",
        "description": (
            "The data row behind a citable ID from the dossier evidence or a claim (e.g. 'sc:70270@2026Q3'). "
            "Reading it is free; only IDs cited in an e-mail are checked by the supplier."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ref": {"type": "string", "description": "a citable data ID"}},
            "required": ["ref"],
        },
    },
]


def _c(value: float) -> float | None:
    return None if value is None or math.isinf(value) else round(value)


class StrategistToolbox:
    """Tools bound to one decision: this case, these threads. `plan_offers` and `supplier_of` are the
    orchestrator's DataTools methods; without them preview_offer is absent and C leaves out the health charge."""

    def __init__(
        self,
        ctx: CaseContext,
        threads: dict[str, list[dict]],
        reads: dict[str, BehaviourRead],
        plan_offers=None,
        supplier_of=None,
        package=None,
    ):
        self.ctx, self.threads, self.reads = ctx, threads, reads
        self.plan_offers, self.supplier_of, self.package = plan_offers, supplier_of, package
        names = {"price_terms", "read_thread"}
        names |= {"preview_offer"} if plan_offers is not None else set()
        names |= {"look_up_data"} if package is not None else set()
        self.schemas = [s for s in SCHEMAS if s["name"] in names]
        self.calls: list[str] = []  # tool names in call order, for the log

    def run(self, name: str, args: dict):
        if name not in {s["name"] for s in self.schemas}:
            raise ValueError(f"unknown tool {name!r}")
        self.calls.append(name)
        return getattr(self, name)(**args)

    def _events(self, supplier_id: str) -> list[dict]:
        if supplier_id not in self.threads:
            raise ValueError(f"no open thread {supplier_id!r}; threads: {sorted(self.threads)}")
        return self.threads[supplier_id]

    def _supplier(self, supplier_id: str, events: list[dict]):
        return self.supplier_of(self.ctx.case, supplier_id, events) if self.supplier_of else None

    # ---- tools ---------------------------------------------------------------------------------

    def price_terms(self, supplier_id: str, changes: dict) -> dict:
        case, events = self.ctx.case, self._events(supplier_id)
        if not isinstance(changes, dict):
            raise ValueError("changes must be an object of field: value")
        unknown = sorted(set(changes) - set(case["negotiable"]))
        if unknown:
            raise ValueError(f"not negotiable here: {unknown}; negotiable: {case['negotiable']}")
        std = standard_terms(events)
        _, theirs = standing(events)
        stated = {f: std[f] for f in case["negotiable"] if f in std}
        sheet = complete_offer(case, {**stated, **theirs, **changes})
        supplier = self._supplier(supplier_id, events)
        c, c_standing = total_cost(case, sheet, std, supplier), math.inf
        if theirs.get("unit_price") is not None:
            c_standing = total_cost(case, complete_offer(case, {**stated, **theirs}), std, supplier)
        band, alt = self.ctx.bands.get(supplier_id), self.ctx.c_alternative
        return {
            "terms": sheet,
            "c": _c(c),
            "c_standing": _c(c_standing),
            "saves_vs_standing_eur": None if math.isinf(c) or math.isinf(c_standing) else round(c_standing - c),
            "must_haves_ok": must_haves_ok(case, sheet, std),
            "beats_alternative": None if alt is None else c < alt,
            "c_target": _c(band.c_target) if band else None,
        }

    def preview_offer(self, supplier_id: str, levers: list) -> dict:
        events = self._events(supplier_id)
        allowed = set(self.ctx.case["negotiable"])
        chosen = tuple(dict.fromkeys(str(x) for x in (levers or ()) if str(x) in allowed)) or ("unit_price",)
        read = self.reads.get(supplier_id) or BehaviourRead(supplier_id, 0.5, 0.2, (), ())
        plan = self.plan_offers(self.ctx, supplier_id, events, Tactic(supplier_id, "send", chosen), read)
        return {
            "levers": list(chosen),
            "sheets": [{"terms": s, "c": _c(c)} for s, c in zip(plan.term_sheets, plan.c_values, strict=False)],
            "rationale": plan.rationale,
        }

    def read_thread(self, supplier_id: str) -> dict:
        out, budget = [], THREAD_CHARS
        for m in self._events(supplier_id):
            entry = {
                "from": "us" if m.get("from") == "buyer" else "supplier",
                "status": m.get("status"),
                "body": str(m.get("body") or "")[:BODY_CHARS],
                "term_sheets": [t.get("terms", t) for t in (m.get("term_sheets") or [])],
                "claim_results": m.get("claim_results"),
                "attachments": [
                    {"name": a.get("name"), "text": str(a.get("text") or "")[:ATTACHMENT_CHARS]}
                    for a in (m.get("attachments") or [])
                ],
            }
            size = len(json.dumps(entry, ensure_ascii=False, default=str))
            if size > budget:
                out.append({"truncated": "thread longer than the tool returns"})
                break
            out.append(entry)
            budget -= size
        return {"messages": out}

    def look_up_data(self, ref: str) -> dict:
        row = self.package.lookup(str(ref))
        if row is None:
            raise ValueError(f"unknown ID {ref!r}")
        text = json.dumps(row, ensure_ascii=False, default=str)
        return {"ref": ref, "row": text[:ROW_CHARS]}
