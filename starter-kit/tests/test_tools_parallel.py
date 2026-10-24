"""The Live Final plays up to 10 cases at once on one shared data package: the tools must be thread-safe."""

import json
import threading
from pathlib import Path

from arena_kit.agents.buyer.tools import DataTools
from arena_kit.cost import complete_offer
from arena_kit.data import DataPackage

DATA = Path(__file__).parents[2] / "data"
CASES = json.loads((Path(__file__).parent / "fixtures/practice_cases.json").read_text())


def opening_threads(case: dict) -> dict[str, list[dict]]:
    base = case["buyer"].get("current_unit_price") or case["buyer"]["fallback_unit_price"]
    terms = complete_offer(case, {"unit_price": round(base * 1.06, 2)})
    sheet = {"term_sheet_id": "offer:x-0", "terms": terms}
    return {
        s: [{"from": "s", "status": "open", "attachments": [], "body": "", "term_sheets": [sheet]}]
        for s in case["suppliers"]
    }


def test_data_tools_when_every_case_prepares_in_parallel_on_one_package_never_fail():
    tools, errors = DataTools(DataPackage(DATA)), []

    def work(case: dict) -> None:
        try:
            for _ in range(3):
                tools.prepare(case, opening_threads(case))
        except Exception as e:  # collect, the assertion reports them all
            errors.append(f"{case['case_id']}: {type(e).__name__}: {e}")

    workers = [threading.Thread(target=work, args=(case,)) for case in CASES]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert errors == []
