"""A tiny local HTTP service whose health can be flipped at runtime.

Used by the tests, the demo and for manual outage drills:
    python scripts/flaky_server.py --port 8099
    curl "http://127.0.0.1:8099/control?mode=down"            # /health now returns 503
    curl "http://127.0.0.1:8099/control?mode=slow&delay_ms=2000"
    curl "http://127.0.0.1:8099/control?mode=up"
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class ServiceState:
    def __init__(self) -> None:
        self.mode = "up"        # up | down | slow
        self.delay_ms = 0
        self.lock = threading.Lock()


def make_handler(state: ServiceState):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            if url.path == "/control":
                q = parse_qs(url.query)
                mode = q.get("mode", [state.mode])[0]
                delay_value = q.get("delay_ms", [str(state.delay_ms)])[0]
                if mode not in {"up", "down", "slow"}:
                    self._send(400, {"error": "mode must be up, down, or slow"})
                    return
                try:
                    delay_ms = int(delay_value)
                except (TypeError, ValueError):
                    self._send(400, {"error": "delay_ms must be an integer"})
                    return
                if delay_ms < 0:
                    self._send(400, {"error": "delay_ms must be non-negative"})
                    return
                with state.lock:
                    state.mode = mode
                    state.delay_ms = delay_ms
                self._send(200, {"mode": state.mode, "delay_ms": state.delay_ms})
            elif url.path == "/health":
                with state.lock:
                    mode, delay = state.mode, state.delay_ms
                if mode == "down":
                    self._send(503, {"status": "unavailable"})
                    return
                if mode == "slow":
                    time.sleep(delay / 1000)
                self._send(200, {"status": "ok"})
            else:
                self._send(404, {"error": "not found"})

        def log_message(self, *args) -> None:  # silence request logging
            pass

    return Handler


class SimulatedService:
    """Context manager running the flaky server on a free port in a background thread."""

    def __init__(self, port: int = 0) -> None:
        self.state = ServiceState()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(self.state))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def set_mode(self, mode: str, delay_ms: int = 0) -> None:
        with self.state.lock:
            self.state.mode, self.state.delay_ms = mode, delay_ms

    def start(self) -> "SimulatedService":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self) -> "SimulatedService":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
