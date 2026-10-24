"""J5: run a labelled sandbox/practice sweep and compare it with a saved sweep.

    uv run python scripts/sweep.py --set sandbox --label smoke
    uv run python scripts/sweep.py --set practice --label v1 --max-sessions 10 --execute

Practice is a dry run unless --execute is supplied; always prints the session count first.
Each result and its session ID is recorded, including failed cases. No final-set support.
"""

import argparse
import json
import logging
import os
import re
import subprocess
import threading
from pathlib import Path

from arena_kit import ArenaClient
from arena_kit.agents.buyer.agent import buyer_factory
from arena_kit.agents.buyer.runner import play_set
from arena_kit.cli import load_env
from arena_kit.data import DataPackage
from arena_kit.play import RemoteTable, hosted


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", choices=["sandbox", "practice"], default="sandbox")
    parser.add_argument("--label", required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--data", default=os.environ.get("ARENA_DATA", "../data"))
    parser.add_argument("--max-sessions", type=int, default=10)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.label):
        parser.error("label must contain only letters, numbers, underscores and hyphens")
    out = Path("runs") / f"sweep-{args.label}"
    if out.exists():
        parser.error(f"{out} already exists; choose a new label to preserve the comparison")
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else {}
    client = ArenaClient()
    try:
        cases = hosted(client.cases(args.set))
        quota = len(cases) if args.set == "practice" else 0
        print(f"{len(cases)} cases; {quota} hosted practice sessions.", flush=True)
        if len(cases) > args.max_sessions:
            parser.error("case count exceeds --max-sessions")
        if quota:
            left = client.me().get("practice", {}).get("left", 0)
            if quota > left:
                parser.error(f"need {quota} sessions, only {left} remain")
            if not args.execute:
                print("Dry run. Add --execute to run these practice cases.")
                return
        out.mkdir(parents=True)
        os.environ["BUYER_LOG_DIR"] = str(out)
        logging.basicConfig(filename=out / "run.log", level=logging.INFO, encoding="utf-8")
        tables, lock = [], threading.Lock()

        def table():
            item = RemoteTable(client)
            with lock:
                tables.append(item)
            return item

        repo = Path(__file__).resolve().parents[2]
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
            ).stdout.strip()
        )
        results = play_set(table, [c["case_id"] for c in cases], buyer_factory(DataPackage(args.data)), parallel=10)
        sessions = []
        for item in tables:
            if item.session_id:
                try:
                    sessions.append(client.session(item.session_id))
                except Exception as exc:
                    sessions.append({"session_id": item.session_id, "error": str(exc)})
        safe_results = {cid: {"error": str(r)} if isinstance(r, Exception) else r for cid, r in results.items()}
        report = {
            "commit": commit,
            "dirty": dirty,
            "set": args.set,
            "label": args.label,
            "sessions_requested": len(cases),
            "results": safe_results,
            "sessions": sessions,
        }
        (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print("case          score    delta")
        for cid, result in sorted(safe_results.items()):
            score = result.get("score")
            old = baseline.get("results", {}).get(cid, {}).get("score")
            delta = f"{100 * (score - old):+7.2f}" if score is not None and old is not None else "      -"
            display = f"{100 * score:7.2f}" if score is not None else "  ERROR"
            print(f"{cid:<12} {display} {delta}")
        print(f"Evidence: {out / 'report.json'}")
        raise SystemExit(1 if any("error" in r for r in safe_results.values()) else 0)
    finally:
        client.http.close()


if __name__ == "__main__":
    main()
