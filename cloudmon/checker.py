"""Perform a single HTTP health check and return a structured result."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def classify_latency(latency_ms: float, thresholds: dict) -> str:
    """Return 'ok', 'warn' or 'critical' for a latency value in milliseconds."""
    if latency_ms >= thresholds["latency_critical_ms"]:
        return "critical"
    if latency_ms >= thresholds["latency_warn_ms"]:
        return "warn"
    return "ok"


def check_endpoint(endpoint: dict, cfg: dict) -> dict:
    """Ping one endpoint. Never raises: failures are captured in the result."""
    timeout = endpoint.get("timeout_seconds")
    if timeout is None:
        timeout = cfg["timeout_seconds"]
    headers = {"User-Agent": cfg["user_agent"], **(endpoint.get("headers") or {})}

    status_code: int | None = None
    error: str | None = None
    ok = False

    started = time.perf_counter()
    try:
        resp = requests.request(
            endpoint["method"], endpoint["url"], headers=headers,
            timeout=timeout, allow_redirects=True,
        )
        status_code = resp.status_code
        if status_code not in endpoint["expected_status"]:
            error = f"unexpected status {status_code} (expected {endpoint['expected_status']})"
        elif endpoint.get("body_contains") and endpoint["body_contains"] not in resp.text:
            error = f"response body missing expected text {endpoint['body_contains']!r}"
        else:
            ok = True
    except requests.exceptions.Timeout:
        error = f"timeout after {timeout}s"
    except requests.exceptions.SSLError as exc:
        error = f"SSL error: {exc.__class__.__name__}"
    except requests.exceptions.ConnectionError:
        error = "connection error (DNS failure or connection refused)"
    except requests.exceptions.RequestException as exc:
        error = f"request error: {exc.__class__.__name__}"
    latency_ms = round((time.perf_counter() - started) * 1000, 2)

    return {
        "timestamp": utc_now_iso(),
        "name": endpoint["name"],
        "url": endpoint["url"],
        "status_code": status_code,
        "latency_ms": latency_ms,
        "ok": ok,
        "error": error,
        "latency_level": classify_latency(latency_ms, cfg["thresholds"]) if ok else "n/a",
    }


def run_checks(cfg: dict) -> list[dict]:
    """Check all configured endpoints concurrently, preserving config order."""
    endpoints = cfg["endpoints"]
    with ThreadPoolExecutor(max_workers=min(10, len(endpoints))) as pool:
        return list(pool.map(lambda ep: check_endpoint(ep, cfg), endpoints))
