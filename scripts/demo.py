#!/usr/bin/env python3
"""End-to-end outage drill that needs no internet, accounts or secrets.

It starts two local services and a local webhook receiver, then runs REAL checks while
switching one service healthy -> down (HTTP 503) -> healthy -> slow -> critically slow -> healthy.
Output goes to demo_output/ (log, state, report, captured webhook payloads).

    python scripts/demo.py
"""
import json
import os
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import monitor  # noqa: E402
from cloudmon.alerts import dispatch  # noqa: E402,F401
from cloudmon.config import DEFAULTS, _merge, validate  # noqa: E402
from cloudmon.report import generate_reports  # noqa: E402
from cloudmon.simulator import SimulatedService  # noqa: E402
from cloudmon.storage import read_log  # noqa: E402

OUT = ROOT / "demo_output"


def start_webhook_receiver(sink: Path) -> HTTPServer:
    class Hook(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            with sink.open("a") as fh:
                fh.write(body.decode() + "\n")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def banner(text: str) -> None:
    print(f"\n=== {text} ===", flush=True)


def main() -> None:
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    hooks = OUT / "webhook_payloads.jsonl"
    receiver = start_webhook_receiver(hooks)
    os.environ["WEBHOOK_URL"] = f"http://127.0.0.1:{receiver.server_address[1]}/hook"

    with SimulatedService() as orders, SimulatedService() as auth:
        cfg = validate(_merge(DEFAULTS, {
            "timeout_seconds": 5,
            "thresholds": {"latency_warn_ms": 800, "latency_critical_ms": 2000},
            "alerts": {"webhook": {"enabled": True, "url_env": "WEBHOOK_URL", "format": "slack"}},
            "report": {"title": "Demo: Local Services Uptime"},
            "endpoints": [
                {"name": "Orders API", "url": orders.base_url + "/health"},
                {"name": "Auth API", "url": auth.base_url + "/health"},
            ],
        }))
        log, state = str(OUT / "uptime_log.jsonl"), str(OUT / "state.json")

        def rounds(n: int, pause: float = 0.4) -> None:
            for _ in range(n):
                monitor.run_round(cfg, log, state, deliver=True)
                time.sleep(pause)

        banner("1. Normal operation (3 checks)");               rounds(3)
        banner("2. SIMULATED OUTAGE: Orders API returns 503");  orders.set_mode("down"); rounds(3)
        banner("3. Service restored");                          orders.set_mode("up"); rounds(2)
        banner("4. Degraded: Orders API slow (~1000 ms)");      orders.set_mode("slow", 1000); rounds(2)
        banner("5. Critically slow (~2300 ms)");                orders.set_mode("slow", 2300); rounds(1, 0.2)
        banner("6. Back to normal");                            orders.set_mode("up"); rounds(2)

    records = read_log(log)
    generate_reports(records, cfg, OUT / "report")
    n_hooks = sum(1 for _ in hooks.open()) if hooks.exists() else 0
    banner("Summary")
    rel = lambda p: Path(p).relative_to(ROOT)  # noqa: E731
    print(f"{len(records)} checks logged -> {rel(log)}")
    print(f"{n_hooks} webhook notifications captured -> {rel(hooks)}")
    print(f"Report -> {rel(OUT / 'report' / 'index.html')}")


if __name__ == "__main__":
    main()
