"""J4: where the time goes and how often each fallback fired (owner: Jeddou).

    uv run python scripts/timing_report.py [runs/buyer-rounds.jsonl]

Reads the orchestrator's decision trail and prints, per stage, how often the main implementation answered and
how often its rule twin took over (timeout, error, skipped), the p50/p95 time per stage, the p50/p95 of a whole
round (our decision + the supplier's reply), and the claim results the arena returned. The move limit is 60 s
from the supplier's reply; anything near it is a risk for the final.
"""

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def report(rows: list[dict]) -> str:
    used, ms, rounds, claims, kinds = defaultdict(Counter), defaultdict(list), [], Counter(), Counter()
    for r in rows:
        kinds[r.get("kind")] += 1
        for stage, how in (r.get("stages") or {}).items():
            used[stage][how] += 1
        for stage, t in (r.get("timings_ms") or {}).items():
            ms[stage].append(t / 1000)
        if r.get("round_s") is not None:
            rounds.append(r["round_s"])
        for result, n in (r.get("claims") or {}).items():
            claims[result] += n
    lines = [f"decisions: {dict(kinds)}", "", f"{'stage':<8} {'p50 s':>7} {'p95 s':>7}  how it ran"]
    for stage in sorted(set(used) | set(ms)):
        lines.append(f"{stage:<8} {pct(ms[stage], 0.5):>7.2f} {pct(ms[stage], 0.95):>7.2f}  {dict(used[stage])}")
    if rounds:
        lines += ["", f"round (decision + reply): p50 {pct(rounds, 0.5):.1f}s  p95 {pct(rounds, 0.95):.1f}s  "
                  f"max {max(rounds):.1f}s  mean {statistics.mean(rounds):.1f}s"]  # fmt: skip
    lines += ["", f"claim results: {dict(claims) or 'none'}"]
    if claims.get("contradicted"):
        lines.append("WARNING: contradicted claims cost trust - find the sentence in the trail and fix its source")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("path", nargs="?", default="runs/buyer-rounds.jsonl")
    args = p.parse_args()
    path = Path(args.path)
    if not path.exists():
        raise SystemExit(f"no decision trail at {path}: play a case with --agent buyer first")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(report(rows))


if __name__ == "__main__":
    main()
