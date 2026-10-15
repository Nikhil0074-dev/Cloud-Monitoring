"""Threshold evaluation: turns check results + persisted state into alert events.

Alerts are edge-triggered (they fire when a condition *starts* or *ends*) so a long
outage produces one DOWN alert and one RECOVERED alert, not one per check.
"""
from __future__ import annotations

from collections import defaultdict

from .storage import parse_ts

CRITICAL, WARNING, INFO = "critical", "warning", "info"


def fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def _new_state() -> dict:
    return {
        "status": "UP",
        "consecutive_failures": 0,
        "first_failure_ts": None,
        "down_since": None,
        "last_alert_ts": None,
        "latency_level": "ok",
        "uptime_breached": False,
    }


def _event(kind: str, severity: str, result: dict, message: str, **extra) -> dict:
    return {
        "type": kind,
        "severity": severity,
        "endpoint": result["name"],
        "url": result["url"],
        "timestamp": result["timestamp"],
        "message": message,
        "status_code": result.get("status_code"),
        "latency_ms": result.get("latency_ms"),
        **extra,
    }


def rolling_uptime(history: list[dict], name: str, window: int) -> tuple[float, int]:
    """Uptime % over the last `window` checks of one endpoint -> (percent, sample_count)."""
    recent = [r for r in history if r["name"] == name][-window:]
    if not recent:
        return 100.0, 0
    return round(100.0 * sum(1 for r in recent if r["ok"]) / len(recent), 2), len(recent)


def evaluate(results: list[dict], thresholds: dict, state: dict, history: list[dict]) -> list[dict]:
    """Update `state` in place and return the alert events triggered by `results`.

    `history` must already include `results` (the full log, oldest first).
    """
    events: list[dict] = []
    by_name: dict[str, list[dict]] = defaultdict(list)
    for r in history:
        by_name[r["name"]].append(r)

    for r in results:
        st = state.setdefault(r["name"], _new_state())
        if not isinstance(st, dict):
            st = _new_state()
            state[r["name"]] = st
        for key, default in _new_state().items():
            st.setdefault(key, default)

        if not r["ok"]:
            st["consecutive_failures"] += 1
            if st["consecutive_failures"] == 1:
                st["first_failure_ts"] = r["timestamp"]
            reason = r.get("error") or "check failed"
            if st["status"] != "DOWN" and st["consecutive_failures"] >= thresholds["consecutive_failures"]:
                st["status"] = "DOWN"
                st["down_since"] = st["first_failure_ts"]
                st["last_alert_ts"] = r["timestamp"]
                events.append(_event(
                    "DOWN", CRITICAL, r,
                    f"{r['name']} is DOWN: {reason} "
                    f"({st['consecutive_failures']} consecutive failed check(s))",
                    down_since=st["down_since"], reason=reason,
                ))
            elif st["status"] == "DOWN" and thresholds["repeat_alert_minutes"] > 0:
                since_last = (parse_ts(r["timestamp"]) - parse_ts(st["last_alert_ts"])).total_seconds()
                if since_last >= thresholds["repeat_alert_minutes"] * 60:
                    st["last_alert_ts"] = r["timestamp"]
                    down_for = (parse_ts(r["timestamp"]) - parse_ts(st["down_since"])).total_seconds()
                    events.append(_event(
                        "STILL_DOWN", CRITICAL, r,
                        f"{r['name']} is STILL DOWN after {fmt_duration(down_for)}: {reason}",
                        down_since=st["down_since"], reason=reason,
                    ))
        else:
            if st["status"] == "DOWN":
                down_for = (parse_ts(r["timestamp"]) - parse_ts(st["down_since"])).total_seconds()
                events.append(_event(
                    "RECOVERED", INFO, r,
                    f"{r['name']} has RECOVERED after {fmt_duration(down_for)} of downtime "
                    f"(HTTP {r['status_code']}, {r['latency_ms']:.0f} ms)",
                    down_since=st["down_since"], downtime_seconds=round(down_for, 1),
                ))
            st["status"] = "UP"
            st["consecutive_failures"] = 0
            st["first_failure_ts"] = None
            st["down_since"] = None

            level = r["latency_level"]
            if level != st["latency_level"]:
                if level == "critical":
                    events.append(_event(
                        "LATENCY_CRITICAL", CRITICAL, r,
                        f"{r['name']} latency {r['latency_ms']:.0f} ms exceeds critical threshold "
                        f"{thresholds['latency_critical_ms']} ms",
                        threshold_ms=thresholds["latency_critical_ms"]))
                elif level == "warn":
                    events.append(_event(
                        "LATENCY_WARN", WARNING, r,
                        f"{r['name']} latency {r['latency_ms']:.0f} ms exceeds warning threshold "
                        f"{thresholds['latency_warn_ms']} ms",
                        threshold_ms=thresholds["latency_warn_ms"]))
                elif st["latency_level"] in ("warn", "critical"):
                    events.append(_event(
                        "LATENCY_RECOVERED", INFO, r,
                        f"{r['name']} latency back to normal ({r['latency_ms']:.0f} ms)"))
                st["latency_level"] = level

        # Rolling availability SLO
        pct, n = rolling_uptime(by_name[r["name"]], r["name"], thresholds["uptime_window_checks"])
        if n >= min(thresholds["uptime_min_samples"], thresholds["uptime_window_checks"]):
            breached = pct < thresholds["uptime_min_percent"]
            if breached and not st["uptime_breached"]:
                events.append(_event(
                    "UPTIME_BREACH", WARNING, r,
                    f"{r['name']} rolling uptime {pct:.2f}% is below the {thresholds['uptime_min_percent']}% "
                    f"target (last {n} checks)", uptime_percent=pct))
            elif not breached and st["uptime_breached"]:
                events.append(_event(
                    "UPTIME_RECOVERED", INFO, r,
                    f"{r['name']} rolling uptime is back to {pct:.2f}% "
                    f"(target {thresholds['uptime_min_percent']}%)", uptime_percent=pct))
            st["uptime_breached"] = breached
    return events
