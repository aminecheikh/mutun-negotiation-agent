"""The decision agent's tools: what it may call while it decides (owner: Saed; wiring: Jeddou).

Every tool is read-only and deterministic: it prices or looks up, it never sends anything and never changes the
thread. Numbers come from the same code the orchestrator uses (cost.total_cost with our supplier estimate, the
data package), so what the model sees is what will happen. A tool that gets a bad argument raises;
llm.call_json_with_tools turns that into an error result the model can read.

  price_terms   C of any package with this supplier: its standing terms with the changes the model names
  read_thread   the whole thread (the model's input holds only the supplier's last three e-mails)
  look_up_data  the data row behind a citable ID (dossier evidence, an argument's source)
"""

import json
import math

from ....cost import complete_offer, must_haves_ok, total_cost
from ....docs import standard_terms
from ....play import standing
from ..contracts import CaseContext
from ..tools import estimates

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
            "name. Returns the package, its C, the standing C, the difference, and whether it keeps every "
            "must-have and beats our alternative. C includes the supplier-health charge in stabilise and "
            "secure_supply cases. Use it to judge an accept or a push - you never set the price yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "supplier_id": SUPPLIER_ID,
                "changes": {
                    "type": "object",
                    "description": 'negotiable fields and values, e.g. {"unit_price": 4.9, "payment_days": 60}',
                },
            },
            "required": ["supplier_id", "changes"],
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
            "The data row behind a citable ID from the dossier evidence or an argument (e.g. 'sc:70270@2026Q3'). "
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
    """Tools bound to one decision: this case, these threads. Without a data package look_up_data is absent and C
    leaves out the supplier-health charge."""

    def __init__(self, ctx: CaseContext, threads: dict[str, list[dict]], package=None):
        self.ctx, self.threads, self.package = ctx, threads, package
        names = {"price_terms", "read_thread"} | ({"look_up_data"} if package is not None else set())
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
        supplier = estimates.supplier_estimate(self.package, case, supplier_id, std) if self.package else None
        c, c_standing = total_cost(case, sheet, std, supplier), math.inf
        if theirs.get("unit_price") is not None:
            c_standing = total_cost(case, complete_offer(case, {**stated, **theirs}), std, supplier)
        alt = self.ctx.c_alternative
        return {
            "terms": sheet,
            "c": _c(c),
            "c_standing": _c(c_standing),
            "saves_vs_standing_eur": None if math.isinf(c) or math.isinf(c_standing) else round(c_standing - c),
            "must_haves_ok": must_haves_ok(case, sheet, std),
            "beats_alternative": None if alt is None else c < alt,
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
