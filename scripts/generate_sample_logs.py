#!/usr/bin/env python3
"""Generate deterministic sample uptime logs (48 h, one check every 5 min, 4 services).

The data contains a realistic mix of normal traffic, a latency degradation, a hard
outage (HTTP 503) and a connection timeout. The alert engine is replayed over it so
`sample_logs/alerts.jsonl` shows exactly which alerts the thresholds in config.yaml
would have produced.

    python scripts/generate_sample_logs.py
"""
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cloudmon.checker import classify_latency  # noqa: E402
from cloudmon.config import load_config  # noqa: E402
from cloudmon.evaluator import evaluate  # noqa: E402
from cloudmon.report import generate_reports  # noqa: E402

ANCHOR = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)   # "now" for the sample data
STEP = timedelta(minutes=5)
CHECKS = 48 * 12                                                  # 576 checks per service
BASE_LATENCY = {"GitHub API": 135, "Example.com": 95, "HTTPBin health": 420, "Python.org": 180}


def iso(dt: datetime, ms: int) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms:03d}Z"


def build(cfg: dict) -> list[dict]:
    rnd = random.Random(20261014)
    th = cfg["thresholds"]
    start = ANCHOR - STEP * (CHECKS - 1)
    records = []
    for i in range(CHECKS):
        t = start + STEP * i
        for ep in cfg["endpoints"]:
            name, base = ep["name"], BASE_LATENCY[ep["name"]]
            latency = base * rnd.lognormvariate(0, 0.18) + rnd.uniform(0, 12)
            ok, code, err = True, 200, None

            if name == "HTTPBin health" and 300 <= i < 305:          # 25 min hard outage (503)
                ok, code, err, latency = False, 503, "unexpected status 503 (expected [200])", rnd.uniform(60, 110)
            elif name == "Python.org" and 410 <= i < 413:            # 15 min unreachable (timeout)
                ok, code, err, latency = False, None, "timeout after 10s", 10000 + rnd.uniform(0, 40)
            elif name == "GitHub API" and 480 <= i < 490:            # latency degradation
                latency = rnd.uniform(1650, 2300) if i < 487 else rnd.uniform(3100, 3500)
            elif rnd.random() < 0.004:                               # rare single slow response
                latency *= rnd.uniform(5, 7)
            latency = round(latency, 2)
            records.append({
                "timestamp": iso(t, rnd.randint(0, 999)), "name": name, "url": ep["url"],
                "status_code": code, "latency_ms": latency, "ok": ok, "error": err,
                "latency_level": classify_latency(latency, th) if ok else "n/a",
            })
    return records


def main() -> None:
    cfg = load_config(ROOT / "config.yaml")
    records = build(cfg)
    out = ROOT / "sample_logs"
    out.mkdir(exist_ok=True)
    with (out / "uptime_log.jsonl").open("w") as fh:
        for r in records:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")

    # Replay the alert engine chronologically, one round (all endpoints) at a time.
    state, history, events = {}, [], []
    n = len(cfg["endpoints"])
    for k in range(0, len(records), n):
        round_ = records[k:k + n]
        history.extend(round_)
        events.extend(evaluate(round_, cfg["thresholds"], state, history))
    with (out / "alerts.jsonl").open("w") as fh:
        for e in events:
            fh.write(json.dumps(e, separators=(",", ":")) + "\n")

    generate_reports(records, cfg, ROOT / "sample_reports", now=ANCHOR)
    print(f"{len(records)} sample checks, {len(events)} alerts -> sample_logs/, sample_reports/")
    for e in events:
        print(f"  {e['timestamp']}  {e['severity']:<8} {e['type']:<17} {e['endpoint']}")


if __name__ == "__main__":
    main()
