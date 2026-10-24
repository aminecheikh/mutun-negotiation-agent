"""J6: self-contained, local two-wave rehearsal with real data and no hosted quota or model calls.

    uv run python scripts/rehearse.py --data ../data --wave-seconds 20

Starts a loopback simulator on a free port, plays two waves, checks closure/timeouts/claims,
and saves sessions plus a machine-readable report under runs/rehearsal-<timestamp>/.
The scripted supplier checks plumbing, not real negotiation quality.
"""

import argparse
import json
import logging
import secrets
import socket
import subprocess
import threading
import time
from collections import Counter
from pathlib import Path

import uvicorn

from arena_kit import ArenaClient
from arena_kit.agents.buyer.agent import BuyerAgent
from arena_kit.agents.buyer.config import OrchestratorConfig
from arena_kit.agents.buyer.roles import LLMWriter, RuleAnalyst, RuleStrategist
from arena_kit.agents.buyer.runner import play_final
from arena_kit.agents.buyer.telemetry import Telemetry
from arena_kit.agents.buyer.tools import DataTools
from arena_kit.agents.buyer.tools.safe_package import thread_safe
from arena_kit.data import DataPackage
from arena_kit.sim import Sim, create_app, load_cases


def rehearse(data: Path, out: Path, wave_seconds: float = 20) -> dict:
    package = thread_safe(DataPackage(data))
    state = Sim(load_cases(), package.facts)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(state), log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    limit = time.monotonic() + 10
    try:
        while not server.started:
            if not thread.is_alive() or time.monotonic() > limit:
                raise RuntimeError("local simulator did not start")
            time.sleep(0.01)
        url = f"http://127.0.0.1:{port}"
        registration = ArenaClient(url, api_key="local-registration")
        try:
            key = registration.register(f"review-{secrets.token_hex(4)}", "sim")["api_key"]
        finally:
            registration.http.close()
        client = ArenaClient(url, key)  # real 9 calls/s pacing, shared by all cases
        out.mkdir(parents=True, exist_ok=True)
        telemetry = Telemetry(out)
        cfg = OrchestratorConfig(
            close_margin_s=wave_seconds / 3, freeze_s=wave_seconds / 2, last_call_s=2, round_margin_s=1
        )

        def agent():
            return BuyerAgent(
                DataTools(package), None, RuleAnalyst(), RuleStrategist(), LLMWriter(None), package, cfg, telemetry
            )

        state.rehearse(wait=1, cap=wave_seconds, brk=1)
        try:
            results = play_final(client, agent, waves=2, poll=0.2)
        finally:
            client.http.close()
        timeouts = sum(len(t["late"]) for s in state.sessions.values() for t in s["threads"].values())
        claims = Counter()
        for s in state.sessions.values():
            for t in s["threads"].values():
                for m in t["messages"]:
                    claims.update(r["result"] for r in m.get("claim_results") or [])
        failures = [cid for cid, r in results.items() if not isinstance(r, dict) or not r.get("ended")]
        capped = [s["case"]["case_id"] for s in state.sessions.values() if s["end_reason"] == "wave_cap"]
        repo = Path(__file__).resolve().parents[2]
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
            ).stdout.strip()
        )
        report = {
            "commit": commit,
            "dirty": dirty,
            "mode": "local scripted simulator, deterministic roles",
            "wave_seconds": wave_seconds,
            "cases": len(results),
            "expected_cases": len(state.final),
            "timeouts": timeouts,
            "claims": dict(claims),
            "failed_cases": failures,
            "capped_cases": capped,
            "results": {k: str(v) if isinstance(v, Exception) else v for k, v in results.items()},
        }
        report["passed"] = (
            len(results) == len(state.final)
            and not failures
            and not capped
            and timeouts == 0
            and not claims["contradicted"]
            and not claims["duplicate"]
        )
        (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        (out / "sessions.json").write_text(
            json.dumps(state.sessions, default=str, ensure_ascii=False), encoding="utf-8"
        )
        return report
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("../data"))
    parser.add_argument("--wave-seconds", type=float, default=20)
    parser.add_argument("--out", type=Path, default=Path("runs") / time.strftime("rehearsal-%Y%m%d-%H%M%S"))
    args = parser.parse_args()
    if args.wave_seconds < 10:
        parser.error("--wave-seconds must be at least 10 to exercise paced concurrent requests")
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
    report = rehearse(args.data, args.out, args.wave_seconds)
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
    print(f"Evidence: {args.out / 'report.json'}")
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
