import json
from datetime import datetime, timedelta, timezone

import pytest

from cloudmon.alerts import build_webhook_payload, dispatch, format_text
from cloudmon.checker import check_endpoint, classify_latency
from cloudmon.config import ConfigError, load_config, validate, DEFAULTS, _merge
from cloudmon.evaluator import evaluate, fmt_duration
from cloudmon.report import find_incidents, percentile, summarize, generate_reports
from cloudmon.simulator import SimulatedService
from cloudmon.storage import append_results, prune_log, read_log
import monitor


@pytest.fixture()
def svc():
    with SimulatedService() as s:
        yield s


def make_cfg(url, **th):
    cfg = _merge(DEFAULTS, {"endpoints": [{"name": "svc", "url": url + "/health"}],
                            "timeout_seconds": 2, "thresholds": th})
    return validate(cfg)


def rec(ok=True, name="svc", ts="2026-01-01T00:00:00.000Z", latency=50.0, level="ok", code=200, err=None):
    return {"timestamp": ts, "name": name, "url": "http://x", "status_code": code if ok else (None if err else 503),
            "latency_ms": latency, "ok": ok, "error": err, "latency_level": level if ok else "n/a"}


def ts(i):
    return (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ---------------------------------------------------------------- checker
def test_healthy_check_records_status_and_latency_ms(svc):
    cfg = make_cfg(svc.base_url)
    r = check_endpoint(cfg["endpoints"][0], cfg)
    assert r["ok"] and r["status_code"] == 200
    assert isinstance(r["latency_ms"], float) and 0 < r["latency_ms"] < 2000
    assert r["timestamp"].endswith("Z")


def test_latency_is_measured_in_milliseconds(svc):
    svc.set_mode("slow", 300)
    cfg = make_cfg(svc.base_url)
    r = check_endpoint(cfg["endpoints"][0], cfg)
    assert 290 <= r["latency_ms"] < 1500


def test_http_503_is_a_failure(svc):
    svc.set_mode("down")
    cfg = make_cfg(svc.base_url)
    r = check_endpoint(cfg["endpoints"][0], cfg)
    assert not r["ok"] and r["status_code"] == 503 and "503" in r["error"]


def test_connection_refused_is_a_failure(monkeypatch):
    import requests

    def refused(*args, **kwargs):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "request", refused)
    cfg = make_cfg("http://127.0.0.1:1")
    r = check_endpoint(cfg["endpoints"][0], cfg)
    assert not r["ok"] and r["status_code"] is None and "connection" in r["error"]


def test_timeout_is_a_failure(svc):
    svc.set_mode("slow", 1500)
    cfg = make_cfg(svc.base_url)
    cfg["timeout_seconds"] = 0.3
    r = check_endpoint(cfg["endpoints"][0], cfg)
    assert not r["ok"] and "timeout" in r["error"]


def test_body_contains_check(svc):
    cfg = make_cfg(svc.base_url)
    cfg["endpoints"][0]["body_contains"] = "definitely-not-there"
    assert not check_endpoint(cfg["endpoints"][0], cfg)["ok"]
    cfg["endpoints"][0]["body_contains"] = "ok"
    assert check_endpoint(cfg["endpoints"][0], cfg)["ok"]


def test_classify_latency():
    th = {"latency_warn_ms": 100, "latency_critical_ms": 200}
    assert [classify_latency(v, th) for v in (50, 100, 150, 200, 999)] == ["ok", "warn", "warn", "critical", "critical"]


def test_simulator_rejects_bad_delay_without_crashing(svc):
    import requests

    response = requests.get(svc.base_url + "/control?mode=slow&delay_ms=2000%22", timeout=2)
    assert response.status_code == 400
    assert requests.get(svc.base_url + "/health", timeout=2).status_code == 200


def test_invalid_endpoint_timeout_is_rejected():
    with pytest.raises(ConfigError):
        validate(_merge(DEFAULTS, {"endpoints": [{"url": "http://a", "timeout_seconds": 0}]}))


# ---------------------------------------------------------------- evaluator
TH = dict(DEFAULTS["thresholds"])


def feed(results, state, history):
    history.extend(results)
    return evaluate(results, TH, state, history)


def test_down_alert_fires_immediately_and_only_once():
    state, hist = {}, []
    assert feed([rec(ts=ts(0))], state, hist) == []
    ev = feed([rec(False, ts=ts(1), err="connection error")], state, hist)
    assert [e["type"] for e in ev] == ["DOWN"] and ev[0]["severity"] == "critical"
    assert feed([rec(False, ts=ts(2), err="connection error")], state, hist) == []  # no spam


def test_consecutive_failure_threshold():
    th = dict(TH, consecutive_failures=3)
    state, hist = {}, []
    for i in range(2):
        hist.append(rec(False, ts=ts(i), err="e"))
        assert evaluate([hist[-1]], th, state, hist) == []
    hist.append(rec(False, ts=ts(2), err="e"))
    assert [e["type"] for e in evaluate([hist[-1]], th, state, hist)] == ["DOWN"]


def test_recovery_alert_reports_downtime():
    state, hist = {}, []
    feed([rec(False, ts=ts(0), err="e")], state, hist)
    ev = feed([rec(True, ts=ts(5))], state, hist)
    rec_ev = [e for e in ev if e["type"] == "RECOVERED"][0]
    assert rec_ev["downtime_seconds"] == 300 and "5m 00s" in rec_ev["message"]


def test_latency_warn_critical_transitions():
    state, hist = {}, []
    ev = feed([rec(latency=2000, level="warn", ts=ts(0))], state, hist)
    assert [e["type"] for e in ev] == ["LATENCY_WARN"]
    assert feed([rec(latency=2100, level="warn", ts=ts(1))], state, hist) == []
    ev = feed([rec(latency=4000, level="critical", ts=ts(2))], state, hist)
    assert [e["type"] for e in ev] == ["LATENCY_CRITICAL"]
    ev = feed([rec(latency=40, ts=ts(3))], state, hist)
    assert [e["type"] for e in ev] == ["LATENCY_RECOVERED"]


def test_uptime_breach_and_recovery():
    th = dict(TH, uptime_window_checks=20, uptime_min_samples=10, uptime_min_percent=90)
    state, hist, types = {}, [], []
    for i in range(10):
        hist.append(rec(ts=ts(i)))
        types += [e["type"] for e in evaluate([hist[-1]], th, state, hist)]
    for i in range(10, 13):
        hist.append(rec(False, ts=ts(i), err="e"))
        types += [e["type"] for e in evaluate([hist[-1]], th, state, hist)]
    assert "UPTIME_BREACH" in types and types.count("UPTIME_BREACH") == 1
    for i in range(13, 40):
        hist.append(rec(ts=ts(i)))
        types += [e["type"] for e in evaluate([hist[-1]], th, state, hist)]
    assert "UPTIME_RECOVERED" in types


def test_repeat_reminders():
    th = dict(TH, repeat_alert_minutes=10)
    state, hist, types = {}, [], []
    for i in range(0, 25, 5):
        hist.append(rec(False, ts=ts(i), err="e"))
        types += [e["type"] for e in evaluate([hist[-1]], th, state, hist)]
    assert types == ["DOWN", "STILL_DOWN", "STILL_DOWN"]


def test_fmt_duration():
    assert [fmt_duration(s) for s in (5, 65, 3700, 90000)] == ["5s", "1m 05s", "1h 01m", "1d 1h"]


# ---------------------------------------------------------------- end-to-end acceptance
def test_end_to_end_simulated_downtime_triggers_alert_and_logs(svc, tmp_path, capsys):
    cfg = make_cfg(svc.base_url)
    log, state = str(tmp_path / "log.jsonl"), str(tmp_path / "state.json")
    _, ev = monitor.run_round(cfg, log, state, deliver=True)
    assert ev == []
    svc.set_mode("down")
    _, ev = monitor.run_round(cfg, log, state, deliver=True)
    assert [e["type"] for e in ev] == ["DOWN"]
    assert "DOWN" in capsys.readouterr().err       # console warning emitted immediately
    svc.set_mode("up")
    _, ev = monitor.run_round(cfg, log, state, deliver=True)
    assert [e["type"] for e in ev] == ["RECOVERED"]
    records = read_log(log)
    assert [r["ok"] for r in records] == [True, False, True]
    assert all(isinstance(r["latency_ms"], float) for r in records)
    assert json.loads(open(state).read())["svc"]["status"] == "UP"


def test_webhook_delivery(svc, monkeypatch):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading
    got = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204); self.end_headers()
        def log_message(self, *a): pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("WEBHOOK_URL", f"http://127.0.0.1:{srv.server_address[1]}/hook")
    ev = {"type": "DOWN", "severity": "critical", "endpoint": "svc", "url": "u", "timestamp": "t", "message": "svc is DOWN"}
    out = dispatch([ev], {"console": False, "webhook": {"enabled": True, "format": "slack"}})
    srv.shutdown()
    assert out[0]["ok"] and "DOWN" in got[0]["text"]


def test_webhook_missing_secret_does_not_crash(monkeypatch):
    monkeypatch.delenv("WEBHOOK_URL", raising=False)
    ev = {"type": "DOWN", "severity": "critical", "endpoint": "s", "url": "u", "timestamp": "t", "message": "m"}
    out = dispatch([ev], {"console": False, "webhook": {"enabled": True}})
    assert out[0]["ok"] is False


def test_webhook_payload_formats():
    ev = {"type": "DOWN", "severity": "critical", "endpoint": "s", "url": "u", "timestamp": "t", "message": "m"}
    assert "text" in build_webhook_payload(ev, "slack")
    assert "content" in build_webhook_payload(ev, "discord")
    assert build_webhook_payload(ev, "generic")["type"] == "DOWN"
    assert "DOWN" in format_text(ev)


def test_simulate_down_flag_uses_separate_log(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "c.yaml"
    cfg.write_text("alerts: {webhook: {enabled: false}}\nendpoints:\n  - {name: A, url: 'https://example.com'}\n")
    rc = monitor.main(["-c", str(cfg), "--simulate-down", "all", "--fail-on-alert"])
    assert rc == 2
    assert (tmp_path / "logs" / "simulated_log.jsonl").exists()
    assert not (tmp_path / "logs" / "state.json").exists()


# ---------------------------------------------------------------- storage / config / report
def test_corrupt_log_lines_are_skipped(tmp_path):
    p = tmp_path / "l.jsonl"
    append_results(p, [rec(ts=ts(0))])
    with p.open("a") as fh:
        fh.write('{"truncated": \n\n')
    append_results(p, [rec(ts=ts(1))])
    assert len(read_log(p)) == 2


def test_prune_log(tmp_path):
    p = tmp_path / "l.jsonl"
    old = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    new = (datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    append_results(p, [rec(ts=old), rec(ts=new)])
    assert prune_log(p, 30) == 1 and len(read_log(p)) == 1


def test_config_validation(tmp_path):
    with pytest.raises(ConfigError):
        validate(_merge(DEFAULTS, {}))
    with pytest.raises(ConfigError):
        validate(_merge(DEFAULTS, {"endpoints": [{"url": "ftp://x"}]}))
    with pytest.raises(ConfigError):
        validate(_merge(DEFAULTS, {"endpoints": [{"url": "http://a"}], "thresholds": {"latency_warn_ms": 9, "latency_critical_ms": 1}}))
    assert load_config("config.yaml")["endpoints"]


def test_percentile_and_incidents_and_summary():
    assert percentile([1, 2, 3, 4, 100], 95) == 100 and percentile([], 95) is None
    recs = [rec(ts=ts(0)), rec(False, ts=ts(1), err="e"), rec(False, ts=ts(2), err="e"), rec(ts=ts(3)), rec(False, ts=ts(4), err="x")]
    inc = find_incidents(recs, datetime(2026, 1, 1, 1, tzinfo=timezone.utc))
    assert len(inc) == 2 and inc[0]["duration_seconds"] == 120 and inc[1]["ongoing"]
    cfg = validate(_merge(DEFAULTS, {"endpoints": [{"name": "svc", "url": "http://x"}]}))
    s = summarize(recs, cfg, datetime(2026, 1, 1, 1, tzinfo=timezone.utc))["services"][0]
    assert s["uptime_all"] == 40.0 and s["current"] == "DOWN" and s["failed_checks"] == 3


def test_generate_reports(tmp_path):
    cfg = validate(_merge(DEFAULTS, {"endpoints": [{"name": "svc", "url": "http://x"}]}))
    recs = [rec(ts=ts(i)) for i in range(5)] + [rec(False, ts=ts(5), err="boom")]
    paths = generate_reports(recs, cfg, tmp_path, now=datetime(2026, 1, 1, 1, tzinfo=timezone.utc))
    assert "svc" in paths["html"].read_text() and "boom" in paths["markdown"].read_text()
    assert paths["csv"].read_text().startswith("timestamp,name,url")
    generate_reports([], cfg, tmp_path)  # empty log must not crash
