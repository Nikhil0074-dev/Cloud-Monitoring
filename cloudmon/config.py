"""Configuration loading and validation."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "interval_seconds": 60,
    "timeout_seconds": 10,
    "user_agent": "cloudmon/1.0 (+https://github.com/your-user/cloud-monitor)",
    "thresholds": {
        "latency_warn_ms": 1500,
        "latency_critical_ms": 3000,
        "consecutive_failures": 1,   # alert after N failed checks in a row (1 = immediate)
        "uptime_min_percent": 99.0,  # rolling-window availability floor
        "uptime_window_checks": 100,  # size of the rolling window
        "uptime_min_samples": 10,    # don't judge uptime before this many checks exist
        "repeat_alert_minutes": 0,   # re-send "still down" reminders (0 = off)
    },
    "alerts": {
        "console": True,
        "webhook": {"enabled": False, "url_env": "WEBHOOK_URL", "format": "generic"},
        "email": {"enabled": False, "use_ssl": False},
    },
    "storage": {
        "log_file": "logs/uptime_log.jsonl",
        "state_file": "logs/state.json",
        "retention_days": 30,
    },
    "report": {"output_dir": "docs", "title": "Service Uptime Report", "chart_points": 288},
}

ENDPOINT_DEFAULTS: dict[str, Any] = {
    "method": "GET",
    "expected_status": [200],
    "timeout_seconds": None,
    "body_contains": None,
    "headers": {},
}


class ConfigError(ValueError):
    """Raised when the configuration file is invalid."""


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def validate(cfg: dict) -> dict:
    endpoints = cfg.get("endpoints")
    if not isinstance(endpoints, list) or not endpoints:
        raise ConfigError("config must define a non-empty 'endpoints' list")

    seen: set[str] = set()
    clean: list[dict] = []
    for i, raw in enumerate(endpoints):
        if not isinstance(raw, dict) or "url" not in raw:
            raise ConfigError(f"endpoints[{i}] must be a mapping with a 'url'")
        ep = _merge(ENDPOINT_DEFAULTS, raw)
        ep.setdefault("name", ep["url"])
        if ep["name"] in seen:
            raise ConfigError(f"duplicate endpoint name: {ep['name']!r}")
        seen.add(ep["name"])
        clean_ep = dict(ep)
        if not isinstance(clean_ep["url"], str) or not clean_ep["url"].startswith(("http://", "https://")):
            raise ConfigError(f"endpoints[{i}].url must start with http:// or https://")
        clean_ep["method"] = str(clean_ep["method"]).upper()
        if clean_ep["method"] not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
            raise ConfigError(f"endpoints[{i}].method is not supported")
        statuses = clean_ep["expected_status"]
        if isinstance(statuses, int):
            statuses = [statuses]
        if not isinstance(statuses, list) or not statuses:
            raise ConfigError(f"endpoints[{i}].expected_status must be a non-empty list")
        try:
            clean_ep["expected_status"] = [int(s) for s in statuses]
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"endpoints[{i}].expected_status must contain integers") from exc
        if any(s < 100 or s > 599 for s in clean_ep["expected_status"]):
            raise ConfigError(f"endpoints[{i}].expected_status values must be between 100 and 599")
        endpoint_timeout = clean_ep.get("timeout_seconds")
        if endpoint_timeout is not None:
            if not isinstance(endpoint_timeout, (int, float)) or isinstance(endpoint_timeout, bool) or endpoint_timeout <= 0:
                raise ConfigError(f"endpoints[{i}].timeout_seconds must be positive")
        clean.append(clean_ep)
    cfg["endpoints"] = clean

    th = cfg["thresholds"]
    if th["latency_warn_ms"] > th["latency_critical_ms"]:
        raise ConfigError("thresholds.latency_warn_ms must be <= latency_critical_ms")
    if th["consecutive_failures"] < 1:
        raise ConfigError("thresholds.consecutive_failures must be >= 1")
    if not 0 <= th["uptime_min_percent"] <= 100:
        raise ConfigError("thresholds.uptime_min_percent must be between 0 and 100")
    if cfg["interval_seconds"] <= 0 or cfg["timeout_seconds"] <= 0:
        raise ConfigError("interval_seconds and timeout_seconds must be positive")
    return cfg


def load_config(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")
    return validate(_merge(DEFAULTS, raw))
