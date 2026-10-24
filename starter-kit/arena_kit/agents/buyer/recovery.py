"""Reconcile uncertain moves from our own session/thread endpoints before deciding again.

HTTP 409 is also a recoverable timeout, and a lost POST response may already have used a round.
Replace local history with the server history; never retry a business move from stale state.
"""

import copy
import time

from ...play import MOVE_LIMIT_S
from . import observe, policy
from .state import CaseSession, ThreadState


def ended(result: dict | None) -> bool:
    return bool(result and (result.get("ended") or result.get("status") == "ended"))


def refresh_thread(session: CaseSession, th: ThreadState, error=None) -> None:
    table = session.table
    sampled = time.monotonic()
    if not hasattr(table, "client"):
        # Table adapters can expose the kit's missed() contract instead of HTTP endpoints.
        reply = table.missed(th.supplier_id, error)
        with session.lock:
            if "result" in reply:
                session.finish(reply["result"], "arena ended the case during recovery")
            else:
                th.events.append(reply)
                th.received_at = sampled
                observe.rebuild(session, th)
                session.c_open = policy.opening_cost(session)
            th.needs_refresh = False
        session.notify()
        return

    state = table.client.session(table.session_id)
    if ended(state):
        session.finish(state.get("result") or state, "arena ended the case during recovery")
        th.needs_refresh = False
        return
    remote = table.client.thread(table.session_id, th.supplier_id)
    events = copy.deepcopy(remote["messages"])
    # Keep already parsed documents, but do not wait on downloads to recover a lost move.
    texts = {a.get("attachment_id"): a["text"] for m in th.events for a in m.get("attachments") or [] if "text" in a}
    for message in events:
        for attachment in message.get("attachments") or []:
            if attachment.get("attachment_id") in texts:
                attachment["text"] = texts[attachment["attachment_id"]]
    last = observe.latest(events)
    status = "walked_away" if remote.get("closed") else remote.get("status", last.get("status"))
    sheets = last.get("term_sheets") or []
    if remote.get("offer_id") and (not sheets or sheets[0].get("term_sheet_id") != remote["offer_id"]):
        timeout = next(
            (t for t in reversed(remote.get("timeouts") or []) if t.get("offer_id") == remote["offer_id"]), None
        )
        if timeout is None:
            raise ValueError("standing offer is missing from the recovered thread")
        sheets = [{"term_sheet_id": remote["offer_id"], "terms": timeout["terms"]}]
    if status != last.get("status") or sheets != last.get("term_sheets"):
        events.append(
            {
                "from": "arena",
                "status": status,
                "term_sheets": sheets,
                "body": "Recovered thread state.",
                "attachments": [],
            }
        )
    remaining = (state.get("threads", {}).get(th.supplier_id) or {}).get("seconds_left")
    with session.lock:
        th.events = events
        th.timeout_rounds = len(remote.get("timeouts") or [])
        # GET does not reset the supplier's clock. Missing timing information is treated conservatively.
        age = max(0.0, MOVE_LIMIT_S - remaining) if remaining is not None else MOVE_LIMIT_S
        th.received_at = sampled - age
        th.rule_only_once = True
        observe.rebuild(session, th)
        session.c_open = policy.opening_cost(session)
        for message in events:
            if message.get("from") == "buyer":
                session.burned |= {r for r in observe.ids_in(message) if not r.startswith("offer:")}
            else:
                session.offers |= {
                    ts["term_sheet_id"] for ts in message.get("term_sheets") or [] if ts.get("term_sheet_id")
                }
        th.needs_refresh = False
    session.notify()
