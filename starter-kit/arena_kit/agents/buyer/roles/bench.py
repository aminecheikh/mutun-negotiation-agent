"""Offline bench for the LLM roles (owner: Saed; S6): replay recorded one-on-one negotiations through the strategist
and the writer with the real model, N in parallel like the Live Final, and report latency, how often the model's
answer was used (not a fallback) and why the writer's check rejected e-mails. No arena session is used.

    uv run python -m arena_kit.agents.buyer.roles.bench [--parallel 10] [--points 3] [--show 2]

Needs runs/sessions/ (scripts/harvest.py) and LLM_* in .env. Case files come from GET /v1/cases (no quota).
"""

import argparse
import json
import logging
import os
import statistics
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from ....cli import load_env
from ....client import ArenaClient
from ....data import DataPackage
from ...llm import llm_from_env
from ..tools import DataTools
from .analyst import RuleAnalyst
from .profiler import LLMProfiler
from .strategist import LLMStrategist
from .writer import YEAR_RE, LLMWriter, check

RUNS = Path("runs/sessions")


def _load(path: Path) -> dict:
    raw = path.read_bytes()
    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return json.loads(raw.decode("cp1252"))


def threads_from_trace(trace: list[dict]) -> dict[str, list[dict]]:
    """The arena's trace as play.py-style threads: supplier messages with term sheets, our messages in between."""
    threads: dict[str, list[dict]] = {}
    last_supplier = None
    for step in trace:
        move, actor = step.get("move") or {}, step.get("actor")
        body = move.get("text") or move.get("body") or ""
        if actor == "buyer":
            if last_supplier is None or move.get("type") in ("accept", "walk_away"):
                continue
            sheets = move.get("term_sheets") or [{"terms": t} for t in (move.get("terms") or [])]
            sid = move.get("supplier_id") or last_supplier
            threads.setdefault(sid, []).append({"from": "buyer", "body": body, "term_sheets": sheets})
        else:
            last_supplier = actor
            sheet = {"term_sheet_id": move.get("offer_id"), "terms": move.get("terms") or {}}
            threads.setdefault(actor, []).append(
                {
                    "from": f"sales@{actor}",
                    "status": move.get("status"),
                    "body": body,
                    "term_sheets": [sheet] if move.get("terms") else [],
                    "claim_results": move.get("claim_results"),
                    "missing_fields": move.get("missing_fields"),
                    "attachments": [],
                }
            )
    return threads


def points(threads: dict[str, list[dict]], n: int) -> list[dict[str, list[dict]]]:
    """Up to n moments where it is our turn: the supplier's thread cut right after one of its messages. One-on-one,
    as the orchestrator calls the roles: only that supplier's thread, nothing from any other."""
    cuts = []
    for sid, events in threads.items():
        for i, m in enumerate(events):
            if m.get("from") != "buyer" and m.get("status") not in ("walked_away", "no_bid"):
                cuts.append({sid: events[: i + 1]})
    if len(cuts) <= n:
        return cuts
    step = (len(cuts) - 1) / (n - 1) if n > 1 else 0
    return [cuts[round(i * step)] for i in range(n)]


DATA_LOCK = threading.Lock()  # --lock: serialise data-package access to measure the roles alone
PROFILER: dict = {}  # one profiler (cache + live results) for the whole bench, as one process in the final


def one(case, cut, tools, complete, lock: bool) -> dict:
    opening = {sid: ev[:1] for sid, ev in cut.items()}
    if lock:
        with DATA_LOCK:
            ctx = tools.prepare(case, opening)
    else:
        ctx = tools.prepare(case, opening)
    profiler = PROFILER.setdefault("p", LLMProfiler(complete))
    ctx = replace(ctx, dossiers={sid: profiler.refine(case, d) for sid, d in ctx.dossiers.items()})
    live = {sid: ev for sid, ev in cut.items() if ev[-1].get("status") not in ("walked_away", "no_bid")}
    reads = {sid: RuleAnalyst().read(ctx, sid, ev, tools.fit_concessions(ctx, sid, ev)) for sid, ev in live.items()}
    t0 = time.monotonic()
    tactic = LLMStrategist(complete).choose(ctx, live, reads)
    t_strat = time.monotonic() - t0
    out = {"case": case["case_id"], "t_strategist": t_strat, "strategist_llm": not tactic.rationale.startswith("rule:")}
    out["tactic"] = {"sid": tactic.supplier_id, "action": tactic.action, "levers": tactic.levers,
                     "claims": tactic.claims, "question": tactic.question, "why": tactic.rationale}  # fmt: skip
    if tactic.action != "send":
        return out
    events = live[tactic.supplier_id]
    if lock:
        with DATA_LOCK:
            plan = tools.plan_offers(ctx, tactic.supplier_id, events, tactic, reads[tactic.supplier_id])
    else:
        plan = tools.plan_offers(ctx, tactic.supplier_id, events, tactic, reads[tactic.supplier_id])
    sentences = [ctx.claims[tactic.supplier_id][i].sentence for i in tactic.claims]
    writer = LLMWriter(complete)
    t0 = time.monotonic()
    email = writer.write(ctx, tactic, plan, events)
    out["t_writer"] = time.monotonic() - t0
    out["writer_llm"] = writer.last_used_model
    out["writer_rejected"] = writer.last_problems
    out["email"] = email.body
    years = " ".join(YEAR_RE.findall(" ".join(ctx.dossiers[tactic.supplier_id].evidence)))
    allowed = f"{events[-1].get('body') or ''}\n{tactic.question or ''}\n{years}"
    out["final_check"] = check(email.body, sentences, set(email.refs), allowed, case["title"])
    out["claims_sent"] = len(sentences)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parallel", type=int, default=10)
    ap.add_argument("--points", type=int, default=3, help="decision points per recorded session")
    ap.add_argument("--show", type=int, default=2, help="e-mails to print")
    ap.add_argument("--lock", action="store_true", help="serialise data-package access (measure the roles alone)")
    args = ap.parse_args()
    load_env()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    client = ArenaClient()
    cases = {c["case_id"]: c for s in ("practice", "sandbox") for c in client.cases(s)}
    package = DataPackage(os.environ["ARENA_DATA"])
    package.lookup("")  # build the ID index once before threads share it, as cli.py does
    tools = DataTools(package)
    complete = llm_from_env()
    jobs = []
    for path in sorted(RUNS.glob("*.json")):
        record = _load(path)
        s = record["session"]
        case = cases.get(s["case_id"])
        trace = (s.get("result") or {}).get("trace") or []
        if case and trace and len(case.get("suppliers") or []) == 1:  # one-on-one cases only (team decision)
            jobs += [(case, cut) for cut in points(threads_from_trace(trace), args.points)]
    print(f"{len(jobs)} decision points from {len(list(RUNS.glob('*.json')))} sessions, {args.parallel} in parallel")
    t0 = time.monotonic()
    with ThreadPoolExecutor(args.parallel) as pool:
        results = list(pool.map(lambda j: _safe(one, *j, tools, complete, args.lock), jobs))
    wall = time.monotonic() - t0
    ok = [r for r in results if "error" not in r]
    errors = [r for r in results if "error" in r]
    for r in errors[:2]:
        print("ERROR", r["case"], r["error"])
    if errors:
        kinds = [r["error"].split("(")[0] for r in errors]
        print(f"errors: {len(errors)}/{len(results)}", {k: kinds.count(k) for k in set(kinds)})
    ts, tw = [r["t_strategist"] for r in ok], [r["t_writer"] for r in ok if "t_writer" in r]
    print(f"\nwall {wall:.0f}s for {len(jobs)} points")
    for name, xs in (("strategist", ts), ("writer", tw)):
        if xs:
            q = statistics.quantiles(xs, n=10) if len(xs) > 1 else [xs[0]] * 9
            print(
                f"{name:10} n={len(xs):3} median {statistics.median(xs):5.1f}s  p90 {q[8]:5.1f}s  max {max(xs):5.1f}s"
            )
    if ok:
        print(f"strategist answer used: {sum(r['strategist_llm'] for r in ok)}/{len(ok)}")
    sent = [r for r in ok if "t_writer" in r]
    if sent:
        print(f"writer answer used:     {sum(r['writer_llm'] for r in sent)}/{len(sent)}")
        reasons = [p.split(":")[0] for r in sent for p in r["writer_rejected"]]
        print("writer rejections:", {k: reasons.count(k) for k in set(reasons)} or "none")
        print("unsafe e-mails sent (must be 0):", sum(bool(r["final_check"]) for r in sent))
        for r in [r for r in sent if r["final_check"]][:3]:
            print("  unsafe:", r["case"], r["final_check"])
        print(f"claims cited: {sum(r['claims_sent'] for r in sent)} in {len(sent)} e-mails")
    actions = [r["tactic"]["action"] for r in ok]
    print("actions:", {a: actions.count(a) for a in set(actions)})
    for r in sent[: args.show]:
        print(f"\n--- {r['case']} {r['tactic']}\n{r['email']}")


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:  # report, don't stop the bench
        return {"case": args[0]["case_id"], "error": f"{e!r}\n{traceback.format_exc(limit=-4)}"}


if __name__ == "__main__":
    main()
