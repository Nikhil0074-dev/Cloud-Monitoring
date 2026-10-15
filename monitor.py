#!/usr/bin/env python3
"""cloudmon - health checks, latency/uptime tracking, threshold alerts and uptime reports.

Examples
--------
    python monitor.py                         # one round of checks + report (what CI runs)
    python monitor.py --loop                  # run forever, every interval_seconds
    python monitor.py --count 5 --interval 10 # 5 rounds, 10 s apart
    python monitor.py --simulate-down all     # prove alerting works (separate log, no state)
    python monitor.py --report-only           # rebuild the report from the existing log
Exit codes: 0 ok | 1 configuration/runtime error | 2 critical alert raised (with --fail-on-alert)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from cloudmon import __version__
from cloudmon.alerts import dispatch
from cloudmon.checker import run_checks
from cloudmon.config import ConfigError, load_config
from cloudmon.evaluator import evaluate
from cloudmon.report import generate_reports
from cloudmon.storage import append_results, load_state, prune_log, read_log, save_state

SIMULATED_URL = "http://127.0.0.1:1/cloudmon-simulated-outage"  # port 1: connection refused
CRITICAL_TYPES = {"DOWN", "STILL_DOWN", "LATENCY_CRITICAL"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Endpoint health monitor with alerting and uptime reports.")
    p.add_argument("-c", "--config", default="config.yaml", help="path to config file (default: config.yaml)")
    p.add_argument("--count", type=int, default=1, help="number of check rounds to run (default: 1)")
    p.add_argument("--interval", type=float, default=None, help="seconds between rounds (default: config interval_seconds)")
    p.add_argument("--loop", action="store_true", help="run forever until Ctrl+C")
    p.add_argument("--log-file", help="override storage.log_file")
    p.add_argument("--state-file", help="override storage.state_file")
    p.add_argument("--output-dir", help="override report.output_dir")
    p.add_argument("--no-report", action="store_true", help="skip report generation")
    p.add_argument("--report-only", action="store_true", help="only rebuild reports from the existing log")
    p.add_argument("--no-alerts", action="store_true", help="evaluate thresholds but do not deliver alerts")
    p.add_argument("--simulate-down", metavar="NAME|all",
                   help="point NAME (or all) at a dead port to simulate an outage; uses a separate log and no saved state")
    p.add_argument("--fail-on-alert", action="store_true", help="exit with code 2 if a critical alert fired")
    p.add_argument("--version", action="version", version=f"cloudmon {__version__}")
    return p.parse_args(argv)


def apply_simulation(cfg: dict, target: str) -> None:
    names = {e["name"] for e in cfg["endpoints"]}
    if target != "all" and target not in names:
        raise ConfigError(f"--simulate-down: unknown endpoint {target!r}; choose from {sorted(names)} or 'all'")
    for ep in cfg["endpoints"]:
        if target in ("all", ep["name"]):
            ep["url"], ep["expected_status"] = SIMULATED_URL, [200]
            ep["timeout_seconds"] = 3


def print_result(r: dict) -> None:
    state = "UP  " if r["ok"] else "DOWN"
    code = r["status_code"] if r["status_code"] is not None else "---"
    extra = "" if r["ok"] else f"  ({r['error']})"
    print(f"{r['timestamp']} | {state} | {r['name']:<24} | HTTP {code!s:<3} | {r['latency_ms']:>9.2f} ms{extra}", flush=True)


def run_round(cfg: dict, log_file: str, state_file: str | None, deliver: bool) -> tuple[list[dict], list[dict]]:
    results = run_checks(cfg)
    for r in results:
        print_result(r)
    append_results(log_file, results)
    history = read_log(log_file)
    state = load_state(state_file) if state_file else {}
    events = evaluate(results, cfg["thresholds"], state, history)
    if state_file:
        save_state(state_file, state)
    if deliver:
        dispatch(events, cfg["alerts"])
    prune_log(log_file, cfg["storage"]["retention_days"])
    return results, events


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        cfg = load_config(args.config)
        simulate = bool(args.simulate_down)
        if simulate:
            apply_simulation(cfg, args.simulate_down)

        log_file = args.log_file or ("logs/simulated_log.jsonl" if simulate else cfg["storage"]["log_file"])
        state_file = None if simulate else (args.state_file or cfg["storage"]["state_file"])
        out_dir = args.output_dir or cfg["report"]["output_dir"]
        interval = args.interval if args.interval is not None else cfg["interval_seconds"]

        if args.report_only:
            paths = generate_reports(read_log(log_file), cfg, out_dir)
            print("Reports written:", ", ".join(str(p) for p in paths.values()))
            return 0

        critical_fired = False
        rounds = 0
        try:
            while True:
                rounds += 1
                _, events = run_round(cfg, log_file, state_file, deliver=not args.no_alerts)
                critical_fired |= any(e["type"] in CRITICAL_TYPES for e in events)
                if not args.no_report and not simulate:
                    generate_reports(read_log(log_file), cfg, out_dir)
                if not args.loop and rounds >= args.count:
                    break
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopped by user.", file=sys.stderr)

        if args.fail_on_alert and critical_fired:
            return 2
        return 0
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
