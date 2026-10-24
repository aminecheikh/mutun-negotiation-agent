"""Save every ended session (result, trace, back office) under runs/sessions/ and print a score table.

    uv run python scripts/harvest.py [--set practice|sandbox] [--digest]

--digest also writes runs/digest-<set>.md: per session, every move with the supplier's reasons in words,
the input for reading replays in bulk (by you or by an LLM building the playbook).
"""

import argparse
import json
from collections.abc import Iterator
from pathlib import Path

from arena_kit import ArenaClient
from arena_kit.cli import load_env

RUNS = Path("runs/sessions")


def fetch(client: ArenaClient, summary: dict) -> dict:
    """The full session (with trace) and its back office, cached on disk once the session has ended."""
    path = RUNS / f"{summary['case_id'].replace(':', '_')}_{summary['session_id']}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    record = {"session": client.session(summary["session_id"]), "backoffice": client.backoffice(summary["session_id"])}
    path.write_text(json.dumps(record, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return record


def digest_lines(record: dict) -> Iterator[str]:
    s, result = record["session"], record["session"].get("result") or {}
    yield f"## {s['case_id']} {s['session_id']} score={result.get('score')} end={s.get('end_reason')}\n"
    for step in result.get("trace") or []:
        move = step.get("move") or {}
        terms = move.get("terms") or [t.get("terms") for t in move.get("term_sheets") or []]
        text = (move.get("text") or move.get("body") or "").strip().replace("\n", " ")
        yield f"- r{step['round']} **{step['actor']}** {move.get('status') or move.get('type', '')} {json.dumps(terms)}"
        if move.get("claim_results"):
            yield f"  - claims: {json.dumps(move['claim_results'])}"
        yield f"  - text: {text[:700]}"
        for reason in step.get("reasons") or []:
            yield f"  - reason: {reason}"
    yield ""


def main() -> None:
    load_env()
    p = argparse.ArgumentParser()
    p.add_argument("--set", default="practice", choices=["practice", "sandbox"])
    p.add_argument("--digest", action="store_true")
    args = p.parse_args()
    RUNS.mkdir(parents=True, exist_ok=True)
    client = ArenaClient()
    ended = [s for s in client.sessions(args.set) if s["status"] == "ended"]
    records = [fetch(client, s) for s in sorted(ended, key=lambda s: (s["case_id"], s["created_at"]))]
    header = ("case", "session", "score", "rounds", "end", "c_open", "c_deal", "c_best")
    print("{:<10} {:<18} {:>6} {:>6} {:<12} {:>12} {:>12} {:>12}".format(*header))
    for r in records:
        s, res = r["session"], r["session"].get("result") or {}
        score = res.get("score")
        print(
            f"{s['case_id']:<10} {s['session_id']:<18} {score if score is None else round(score, 3)!s:>6} "
            f"{s.get('rounds')!s:>6} {s.get('end_reason')!s:<12} {res.get('c_open')!s:>12} "
            f"{res.get('c_deal')!s:>12} {res.get('c_best')!s:>12}"
        )
    if args.digest:
        out = Path(f"runs/digest-{args.set}.md")
        out.write_text("\n".join(line for r in records for line in digest_lines(r)), encoding="utf-8")
        print(f"\ndigest: {out}")


if __name__ == "__main__":
    main()
