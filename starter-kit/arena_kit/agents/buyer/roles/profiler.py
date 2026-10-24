"""Profiler: judges a supplier's evidence once per case - persona, levers, traits (owner: Saed). S4.

Tools' `dossier.build` scores a persona with transparent rules and collects evidence lines with their IDs. The
profiler is the LLM judge on top: it confirms or corrects the persona, orders the levers and names traits the
rules cannot express (inputs hedged until a date, an owner who does not follow the market, a pricing
committee, a price freeze, an open book, "not strategic"). The traits go to the front of `Dossier.evidence`
as `profile:` lines, where the strategist and the writer read them first.

The clock is running when the orchestrator calls `refine`, so the base profile of every supplier in the data
package is precomputed into `profiles.json` (`--precompute`, no arena session): a lookup, no LLM call. Only a
case that brings its own facts about a supplier (new letters in hidden cases) triggers a live call, bounded by
TIMEOUT_S, with the cached profile or the rules' dossier as the fallback.

    uv run python -m arena_kit.agents.buyer.roles.profiler --precompute [--parallel 10]
"""

import hashlib
import json
import logging
import re
import typing
from dataclasses import replace
from pathlib import Path

from ..contracts import Dossier, Persona
from .llm import call_json, prompt
from .strategist import TERMS

log = logging.getLogger("arena")
TIMEOUT_S = 6.0  # live only for case facts; the strategist and writer still need their time in round 1
CACHE = Path(__file__).parent / "profiles.json"
PERSONAS = set(typing.get_args(Persona))
TRAITS = {
    "hedged", "uninformed_owner", "open_book", "pricing_committee", "price_freeze", "volume_discounts_only",
    "not_strategic", "partnership_memory", "cash_need", "idle_capacity", "capacity_squeeze", "quality_problem",
    "new_owner", "succession",
}  # fmt: skip
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
MIN_CONFIDENCE = 0.4  # below this the rules' persona stands
MAX_EVIDENCE = 18
# category -> goal (data/company.md "Category strategies"), for the neutral case of the precompute
CATEGORY_GOAL = {
    "castings": "stabilise", "machined parts": "leverage", "sheet metal": "leverage", "seals": "secure_supply",
    "electrical": "stabilise", "C-parts": "routine", "raw material": "leverage",
}  # fmt: skip


def evidence_key(dossier: Dossier) -> str:
    return hashlib.sha1("\n".join(dossier.evidence).encode()).hexdigest()[:12]


def validated(raw, dossier: Dossier) -> dict | None:
    """The model's profile, kept only where the evidence backs it: known personas, levers and traits, trait IDs
    and decisive lines that really appear in the evidence."""
    if not isinstance(raw, dict):
        return None
    text = "\n".join(dossier.evidence)
    persona = raw.get("persona") if raw.get("persona") in PERSONAS else dossier.persona
    levers = [lever for lever in raw.get("levers") or [] if isinstance(lever, str) and lever in TERMS]
    traits = []
    for t in raw.get("traits") or []:
        if not isinstance(t, dict) or t.get("trait") not in TRAITS:
            continue
        ref = t.get("id") if isinstance(t.get("id"), str) and t.get("id") and t["id"] in text else None
        if ref is None:  # a trait we cannot point to in the evidence is a guess
            continue
        until = t.get("until") if isinstance(t.get("until"), str) and MONTH_RE.match(t["until"]) else None
        why = " ".join(str(t.get("why") or "").split())[:120]
        traits.append({"trait": t["trait"], "until": until, "why": why, "id": ref})
    decisive = [line for line in raw.get("decisive") or [] if isinstance(line, str) and line in dossier.evidence][:3]
    try:
        confidence = min(1.0, max(0.0, float(raw.get("confidence"))))
    except (TypeError, ValueError):
        confidence = 0.5
    return {"persona": persona, "levers": levers, "traits": traits, "decisive": decisive, "confidence": confidence}


def apply(dossier: Dossier, profile: dict) -> Dossier:
    """The dossier with the profile's persona (when confident), levers first and trait lines at the front."""
    persona = dossier.persona
    if profile["persona"] != "unknown" and (profile["confidence"] >= MIN_CONFIDENCE or dossier.persona == "unknown"):
        persona = profile["persona"]
    levers = tuple(dict.fromkeys([*profile["levers"], *dossier.levers]))
    lines = []
    for t in profile["traits"]:
        until = f" until {t['until']}" if t["until"] else ""
        lines.append(f"profile: {t['trait']}{until} - {t['why']} ({t['id']})")
    evidence = tuple(dict.fromkeys([*lines, *profile["decisive"], *dossier.evidence]))[:MAX_EVIDENCE]
    return replace(dossier, persona=persona, levers=levers, evidence=evidence)


def _user(dossier: Dossier, base: dict | None) -> str:
    return json.dumps(
        {
            "supplier": {"id": dossier.supplier_id, "name": dossier.name, "country": dossier.country},
            "rules_persona": dossier.persona,
            "rules_levers": list(dossier.levers),
            "second_source": dossier.second_source,
            "history_give_pct": dossier.history_give_pct,
            "evidence": list(dossier.evidence),
            "profile_without_case_facts": base,
        },
        ensure_ascii=False,
        default=str,
    )


def load_cache(path: Path = CACHE) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class LLMProfiler:
    def __init__(self, complete=None, cache: dict | None = None, timeout_s: float = TIMEOUT_S):
        self.complete, self.timeout_s = complete, timeout_s
        self.cache = load_cache() if cache is None else cache
        self.system = prompt("profiler")
        self.live: dict[str, dict] = {}  # (supplier, evidence) -> profile, for this process

    def profile(self, dossier: Dossier, base: dict | None = None) -> dict | None:
        raw = call_json(self.complete, self.system, _user(dossier, base), self.timeout_s)
        return validated(raw, dossier)

    def refine(self, case: dict, dossier: Dossier) -> Dossier:
        try:
            base = self.cache.get(dossier.supplier_id)
            has_case_facts = any(line.startswith(("case fact", "release fact")) for line in dossier.evidence)
            if base and not has_case_facts:
                return apply(dossier, base)
            key = f"{dossier.supplier_id}:{evidence_key(dossier)}"
            if key not in self.live and self.complete is not None:
                if fresh := self.profile(dossier, base):
                    self.live[key] = fresh
            # New evidence can invalidate yesterday's persona. On failure retain today's rule dossier.
            chosen = self.live.get(key) or (base if not has_case_facts else None)
            return apply(dossier, chosen) if chosen else dossier
        except Exception as e:  # the rules' dossier is always a valid answer
            log.warning("%s: profiler failed (%r) - keeping the rules' dossier", dossier.supplier_id, e)
            return dossier


class PassthroughProfiler:
    def refine(self, case: dict, dossier: Dossier) -> Dossier:
        return dossier


# ---- precompute (offline, no arena session) ------------------------------------------------------


def neutral_case(package, supplier_id: str, category: str) -> dict:
    """A case with no facts, so the dossier holds only what the data package says about the supplier."""
    rows = package.query("SELECT part_id FROM sourcing WHERE supplier_id = ? ORDER BY quota_pct DESC", (supplier_id,))
    parts = [r["part_id"] for r in rows][:1]
    return {
        "case_id": f"profile:{supplier_id}",
        "title": "supplier profile",
        "suppliers": [supplier_id],
        "parts": parts,
        "negotiable": ["unit_price"],
        "buyer": {"category_goal": CATEGORY_GOAL.get(category, "leverage")},
        "facts": {},
    }


def main() -> None:
    import argparse
    import os
    import time
    from concurrent.futures import ThreadPoolExecutor

    from ....cli import load_env
    from ....data import DataPackage
    from ...llm import llm_from_env
    from ..tools import dossier as dossier_tool

    ap = argparse.ArgumentParser()
    ap.add_argument("--precompute", action="store_true", help="profile every detailed supplier into profiles.json")
    ap.add_argument("--parallel", type=int, default=10)
    args = ap.parse_args()
    if not args.precompute:
        ap.print_help()
        return
    load_env()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    package = DataPackage(os.environ["ARENA_DATA"])
    package.lookup("")
    suppliers = package.query("SELECT id, category FROM suppliers WHERE detail = 'full' ORDER BY id")
    dossiers = {}
    for s in suppliers:  # sequential: the data package is not safe across threads
        try:
            dossiers[s["id"]] = dossier_tool.build(neutral_case(package, s["id"], s["category"]), package, s["id"])
        except Exception as e:
            print(f"{s['id']}: no dossier ({e!r})")
    profiler = LLMProfiler(llm_from_env(), cache={}, timeout_s=60.0)
    t0 = time.monotonic()
    with ThreadPoolExecutor(args.parallel) as pool:
        profiles = dict(zip(dossiers, pool.map(profiler.profile, dossiers.values()), strict=True))
    out = {sid: p for sid, p in profiles.items() if p}
    CACHE.write_text(json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"{len(out)}/{len(dossiers)} suppliers profiled in {time.monotonic() - t0:.0f}s -> {CACHE}")
    changed = [
        (sid, dossiers[sid].persona, p["persona"]) for sid, p in out.items() if p["persona"] != dossiers[sid].persona
    ]
    for sid, before, after in changed:
        print(f"  {sid}: rules {before} -> profiler {after} ({out[sid]['confidence']:.1f})")
    traits = [t["trait"] for p in out.values() for t in p["traits"]]
    print("traits:", {t: traits.count(t) for t in sorted(set(traits))})


if __name__ == "__main__":
    main()
