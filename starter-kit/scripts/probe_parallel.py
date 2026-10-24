"""J0: does the arena serialise calls on one session? (owner: Jeddou)

    uv run python scripts/probe_parallel.py [case:D02] [--allow-quota]

Opens a multi-supplier case, sends one question (no term sheet) to every supplier at the same moment, records
when each reply came back, then walks away. If the replies come back one after the other (the second waits for
the first), the server serialises calls per session: set BUYER_SERIALISED_SESSION=1 so the runner orders its
sends by deadline. Writes runs/probe-parallel.json. Sandbox cases cost no quota; anything else needs
--allow-quota (one practice session).
"""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from arena_kit import ArenaClient
from arena_kit.cli import load_env

QUESTION = "Thank you for your quote. Could you tell us your earliest confirmed lead time?"
OUT = Path("runs/probe-parallel.json")


def probe(client: ArenaClient, case_id: str) -> dict:
    opened = client.open_session(case_id)
    sid, suppliers = opened["session_id"], list(opened["threads"])
    if len(suppliers) < 2:
        client.send_message(sid, suppliers[0], {"action": {"type": "walk_away"}})
        raise SystemExit(f"{case_id} has one supplier: pick a multi-supplier case (e.g. case:D02)")
    start = time.monotonic()

    def ask(sup: str) -> dict:
        sent = time.monotonic() - start
        reply = client.send_message(sid, sup, {"subject": "Question", "body": QUESTION, "term_sheets": []})
        return {"supplier": sup, "sent_s": round(sent, 2), "returned_s": round(time.monotonic() - start, 2),
                "status": reply.get("status")}  # fmt: skip

    with ThreadPoolExecutor(max_workers=len(suppliers)) as pool:
        rows = sorted(pool.map(ask, suppliers), key=lambda r: r["returned_s"])
    timeouts = {sup: len(client.thread(sid, sup).get("timeouts") or []) for sup in suppliers}
    client.send_message(sid, suppliers[0], {"action": {"type": "walk_away"}})
    first = rows[0]["returned_s"] - rows[0]["sent_s"]
    gap = rows[-1]["returned_s"] - rows[0]["returned_s"]
    # serialised: the last reply came back about one whole reply-time after the first
    serialised = first > 0.5 and gap >= 0.8 * first
    return {"case_id": case_id, "session_id": sid, "replies": rows, "timeouts": timeouts,
            "first_reply_s": round(first, 2), "gap_s": round(gap, 2), "serialised": serialised}  # fmt: skip


def main() -> None:
    load_env()
    p = argparse.ArgumentParser()
    p.add_argument("case_id", nargs="?", default="case:D02")
    p.add_argument("--allow-quota", action="store_true", help="allow a non-sandbox case (uses 1 practice session)")
    args = p.parse_args()
    client = ArenaClient()
    sandbox = {c["case_id"] for c in client.cases("sandbox")}
    if args.case_id not in sandbox and not args.allow_quota:
        raise SystemExit(f"{args.case_id} is not a sandbox case: it would use quota; pass --allow-quota")
    result = probe(client, args.case_id)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result, indent=1))
    print(f"\nserialised={result['serialised']} -> BUYER_SERIALISED_SESSION={int(result['serialised'])}")


if __name__ == "__main__":
    main()
