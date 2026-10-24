"""Compare the bands with the arena's truth on harvested sessions (owner: Amine).

    uv run python -m arena_kit.agents.buyer.tools.calibrate [runs/sessions] [--data ../data]

Per session: our floor, fair, target and ceiling on its opening quotes, our C at target against the arena's
C_best (the best package any supplier would accept) and the deal we got. target/best < 1 means we aim lower
than any supplier goes (too aggressive), > 1 that we leave money on the table.
"""

import argparse
import json
from pathlib import Path

from ....data import DataPackage
from .bands import price_bands


def opening_threads(session: dict) -> dict[str, list[dict]]:
    return {
        sid: [{"from": "supplier", "status": "open", "attachments": [],
               "term_sheets": [{"term_sheet_id": o["offer_id"], "terms": o["terms"]}]}]
        for sid, o in session["opening"].items()
    }  # fmt: skip


def rows(sessions: Path, package) -> list[tuple]:
    out = []
    for path in sorted(sessions.glob("case_*.json")):
        session = json.loads(path.read_text())["session"]
        result = session.get("result") or {}
        if not result.get("c_best") or not session.get("opening"):
            continue
        case = _case_from_fixture(session["case_id"])
        if case is None:  # e.g. sandbox cases: no case file saved
            continue
        for sid, band in price_bands(case, opening_threads(session), package).items():
            ratio = band.c_target / result["c_best"]
            out.append((session["case_id"], sid, band.floor, band.fair, band.target, band.ceiling, ratio, result))
    return out


def _case_from_fixture(case_id: str) -> dict | None:
    fixture = Path(__file__).parents[4] / "tests/fixtures/practice_cases.json"
    if not fixture.exists():
        return None
    return next((c for c in json.loads(fixture.read_text()) if c["case_id"] == case_id), None)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("sessions", nargs="?", default="runs/sessions")
    p.add_argument("--data", default="../data")
    args = p.parse_args()
    table = rows(Path(args.sessions), DataPackage(args.data))
    head = ("floor", "fair", "target", "ceil", "tgt/best")
    print(f"{'case':<10} {'supplier':<10} {' '.join(f'{h:>8}' for h in head)} {'deal/best':>9}")
    for case_id, sid, floor, fair, target, ceiling, ratio, result in table:
        deal = result["c_deal"] / result["c_best"] if result.get("c_deal") else float("nan")
        cells = [f"{v:>8.2f}" if v is not None else f"{'-':>8}" for v in (floor, fair, target, ceiling)]
        print(f"{case_id:<10} {sid:<10} {' '.join(cells)} {ratio:>8.3f} {deal:>9.3f}")


if __name__ == "__main__":
    main()
