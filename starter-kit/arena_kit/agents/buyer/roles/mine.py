"""S5 (owner: Saed): mine harvested replays for what moved suppliers and what backfired, per persona.

Each exchange in a trace (our message -> the supplier's answer) becomes a row: which terms our best sheet moved
against its standing sheet, how many claims we cited and how they were judged, then its price step, status and
the reasons it gave in words. Rows are grouped by the persona `profiles.json` gives the supplier. The output
feeds the general rules in prompts/strategist.md ("WHAT MOVED SUPPLIERS IN PRACTICE") - never supplier names.

    uv run python -m arena_kit.agents.buyer.roles.mine [--out runs/s5-mining.md]
"""

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .profiler import load_cache

RUNS = Path("runs/sessions")
TRADE_FIELDS = (
    "payment_days",
    "contract_months",
    "volume_per_year",
    "effective_date",
    "lead_time_days",
    "incoterm",
    "index_clause",
    "ppm_commitment",
    "chargeback_eur_per_complaint",
)
# The supplier's reasons, sorted into what they tell us. First match wins.
REASON_KINDS = (
    ("disarmed by data", r"did not know that|fair point|took it into account|we accept your point"),
    (
        "data did not move it",
        r"does not change our calculation|not our reality|"
        r"already (?:taken|accounted)|already factored",
    ),
    ("trust lost", r"costs trust|do not match|contradict|please provide proof"),
    (
        "wants reciprocity",
        r"without (?:any )?movement|reciprocal|move from your side|"
        r"substantial move|step from your side",
    ),
    ("values commitment", r"commitment|volume|longer term|36-month|24-month|contract term|consolidat"),
    ("values payment", r"payment|cash ?flow|zahlungsziel|liquidity"),
    ("values date", r"valid from|effective|later start"),
    ("hard limit", r"last price|letztes|final|cannot go|please decide|take it or leave it|our cost"),
    ("relationship", r"partnership|relationship|long-standing|cooperation|value"),
)


def _load(path: Path) -> dict:
    raw = path.read_bytes()
    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return json.loads(raw.decode("cp1252"))


def _price(terms) -> float | None:
    try:
        return float(terms["unit_price"]) if terms and terms.get("unit_price") is not None else None
    except (TypeError, ValueError):
        return None


def _sheets(move: dict) -> list[dict]:
    if move.get("term_sheets"):
        return [s.get("terms", s) for s in move["term_sheets"]]
    terms = move.get("terms")
    return terms if isinstance(terms, list) else ([terms] if terms else [])


def kind(reason: str) -> str:
    return next((k for k, pattern in REASON_KINDS if re.search(pattern, reason, re.I)), "other")


def rows(record: dict, personas: dict) -> list[dict]:
    s = record["session"]
    result = s.get("result") or {}
    standing: dict[str, dict] = {}
    pending: dict[str, dict] = {}
    out = []
    for step in result.get("trace") or []:
        actor, move = step.get("actor"), step.get("move") or {}
        if actor == "buyer":
            sid = move.get("supplier_id") or (list(standing)[-1] if standing else None)
            if sid is None or move.get("type") in ("accept", "walk_away"):
                continue
            theirs = standing.get(sid, {})
            sheets = _sheets(move)
            moved = sorted({f for sh in sheets for f in TRADE_FIELDS if f in sh and sh.get(f) != theirs.get(f)})
            text = move.get("text") or move.get("body") or ""
            pending[sid] = {
                "sheets": len(sheets),
                "trades": moved,
                "our_best": min(filter(None, map(_price, sheets)), default=None),
                "ids": len(set(re.findall(r"\b(?:idx|sc|qn|po|cost|quote|offer|doc|ctr|fc|news):[\w.@#/-]+", text))),
            }
            continue
        before = _price(standing.get(actor))
        terms = move.get("terms") or {}
        if terms:
            standing[actor] = terms
        if actor not in pending:
            continue
        ours = pending.pop(actor)
        now = _price(terms)
        step_pct = (before - now) / before * 100 if before and now else None
        reasons = [r for r in step.get("reasons") or [] if isinstance(r, str)]
        claims = [c.get("result") for c in move.get("claim_results") or [] if isinstance(c, dict)]
        out.append(
            {
                "case": s["case_id"],
                "score": result.get("score"),
                "supplier": actor,
                "persona": personas.get(actor, {}).get("persona", "unknown"),
                "round": step.get("round"),
                **ours,
                "claims": claims,
                "step_pct": step_pct,
                "status": move.get("status"),
                "reasons": reasons,
                "kinds": sorted({kind(r) for r in reasons}),
            }
        )
    return out


def report(all_rows: list[dict]) -> str:
    lines = ["# S5 mining - what moved suppliers in practice", ""]
    by_persona = defaultdict(list)
    for r in all_rows:
        by_persona[r["persona"]].append(r)
    lines += [
        "| persona | exchanges | mean step % | step when we traded a term | step on price only "
        "| claims verified/contradicted |",
        "|---|---|---|---|---|---|",
    ]
    for persona, rs in sorted(by_persona.items()):
        steps = [r["step_pct"] for r in rs if r["step_pct"] is not None]
        traded = [r["step_pct"] for r in rs if r["step_pct"] is not None and r["trades"]]
        price_only = [r["step_pct"] for r in rs if r["step_pct"] is not None and not r["trades"]]
        c = Counter(x for r in rs for x in r["claims"])
        mean = lambda xs: f"{sum(xs) / len(xs):.2f} (n={len(xs)})" if xs else "-"  # noqa: E731
        lines.append(
            f"| {persona} | {len(rs)} | {mean(steps)} | {mean(traded)} | {mean(price_only)} | "
            f"{c.get('verified', 0)}/{c.get('contradicted', 0)} |"
        )
    lines += ["", "## Reason kinds per persona", ""]
    for persona, rs in sorted(by_persona.items()):
        k = Counter(x for r in rs for x in r["kinds"])
        lines.append(f"- **{persona}**: " + ", ".join(f"{name} {n}" for name, n in k.most_common()))
    lines += ["", "## Step by trade (all personas)", ""]
    per_trade = defaultdict(list)
    for r in all_rows:
        if r["step_pct"] is None:
            continue
        for t in r["trades"] or ["price only"]:
            per_trade[t].append(r["step_pct"])
    for t, xs in sorted(per_trade.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"- {t}: mean step {sum(xs) / len(xs):.2f} % over {len(xs)} exchanges")
    lines += ["", "## Every exchange", ""]
    for r in all_rows:
        step = f"{r['step_pct']:+.2f}%" if r["step_pct"] is not None else "-"
        lines.append(
            f"- {r['case']} ({r['persona']}, score {r['score']:.2f}) r{r['round']}: "
            f"we traded {r['trades'] or 'price only'}, {r['sheets']} sheets, {r['ids']} IDs, "
            f"claims {r['claims'] or '-'} -> step {step}, {r['status']}"
        )
        for reason in r["reasons"]:
            lines.append(f"  - [{kind(reason)}] {reason[:220]}")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="runs/s5-mining.md")
    args = ap.parse_args()
    personas = load_cache()
    all_rows = [r for path in sorted(RUNS.glob("*.json")) for r in rows(_load(path), personas)]
    text = report(all_rows)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text.split("## Every exchange")[0])
    print(f"{len(all_rows)} exchanges -> {args.out}")


if __name__ == "__main__":
    main()
