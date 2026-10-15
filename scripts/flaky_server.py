#!/usr/bin/env python3
"""Run the local simulated service for manual outage drills (see cloudmon/simulator.py)."""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cloudmon.simulator import SimulatedService  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8099)
    args = ap.parse_args()
    with SimulatedService(args.port) as svc:
        print(f"Simulated service on {svc.base_url}/health  (toggle: {svc.base_url}/control?mode=up|down|slow&delay_ms=N)")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
