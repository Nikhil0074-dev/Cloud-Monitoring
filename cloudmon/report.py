"""Historical availability reporting: stats, incident detection, Markdown/CSV/HTML output."""
from __future__ import annotations

import csv
import html
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .evaluator import fmt_duration
from .storage import parse_ts

CSV_FIELDS = ["timestamp", "name", "url", "status_code", "latency_ms", "ok", "error", "latency_level"]


# --------------------------------------------------------------------------- statistics
def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def uptime_pct(records: list[dict]) -> float | None:
    if not records:
        return None
    return round(100.0 * sum(1 for r in records if r["ok"]) / len(records), 3)


def find_incidents(records: list[dict], now: datetime) -> list[dict]:
    """Group consecutive failed checks of one endpoint into incidents."""
    incidents: list[dict] = []
    current: dict | None = None
    for r in records:
        if not r["ok"]:
            if current is None:
                current = {"start": r["timestamp"], "failed_checks": 0, "errors": Counter(), "end": None}
            current["failed_checks"] += 1
            current["errors"][r.get("error") or "check failed"] += 1
        elif current is not None:
            current["end"] = r["timestamp"]
            incidents.append(current)
            current = None
    if current is not None:
        incidents.append(current)  # still ongoing
    for inc in incidents:
        start = parse_ts(inc["start"])
        end = parse_ts(inc["end"]) if inc["end"] else now
        inc["duration_seconds"] = max(0.0, (end - start).total_seconds())
        inc["ongoing"] = inc["end"] is None
        inc["reason"] = inc["errors"].most_common(1)[0][0]
    return incidents


def summarize(records: list[dict], cfg: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    by_name: dict[str, list[dict]] = {}
    for r in records:
        by_name.setdefault(r["name"], []).append(r)

    services = []
    for ep in cfg["endpoints"]:
        recs = by_name.get(ep["name"], [])
        recs = sorted(recs, key=lambda r: r["timestamp"])
        in_window = lambda delta: [r for r in recs if parse_ts(r["timestamp"]) >= now - delta]  # noqa: E731
        lat_ok = [r["latency_ms"] for r in recs if r["ok"]]
        codes = Counter(str(r["status_code"]) if r["status_code"] is not None else "no response" for r in recs)
        last = recs[-1] if recs else None
        services.append({
            "name": ep["name"],
            "url": ep["url"],
            "total_checks": len(recs),
            "failed_checks": sum(1 for r in recs if not r["ok"]),
            "uptime_24h": uptime_pct(in_window(timedelta(hours=24))),
            "uptime_7d": uptime_pct(in_window(timedelta(days=7))),
            "uptime_all": uptime_pct(recs),
            "avg_latency_ms": round(sum(lat_ok) / len(lat_ok), 1) if lat_ok else None,
            "p95_latency_ms": percentile(lat_ok, 95),
            "max_latency_ms": max(lat_ok) if lat_ok else None,
            "min_latency_ms": min(lat_ok) if lat_ok else None,
            "status_codes": dict(sorted(codes.items())),
            "current": ("UNKNOWN" if last is None else ("UP" if last["ok"] else "DOWN")),
            "last_check": last["timestamp"] if last else None,
            "last_latency_ms": last["latency_ms"] if last else None,
            "last_status_code": last["status_code"] if last else None,
            "last_error": last.get("error") if last else None,
            "incidents": find_incidents(recs, now),
            "records": recs,
        })
    return {"generated": now, "services": services,
            "total_checks": sum(s["total_checks"] for s in services)}


# --------------------------------------------------------------------------- outputs
def _fmt_pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.2f}%"


def _fmt_ms(v: float | None) -> str:
    return "n/a" if v is None else f"{v:,.0f} ms"


def write_csv(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in sorted(records, key=lambda r: (r["timestamp"], r["name"])):
            w.writerow(r)


def render_markdown(summary: dict, title: str) -> str:
    gen = summary["generated"].strftime("%Y-%m-%d %H:%M UTC")
    out = [f"# {title}", "", f"_Generated {gen} from {summary['total_checks']} recorded checks._", "",
           "## Availability summary", "",
           "| Service | Now | Uptime 24h | Uptime 7d | Uptime all | Avg latency | p95 latency | Max latency | Checks (failed) |",
           "|---|---|---|---|---|---|---|---|---|"]
    for s in summary["services"]:
        badge = {"UP": "🟢 UP", "DOWN": "🔴 DOWN"}.get(s["current"], "⚪ n/a")
        out.append(f"| {s['name']} | {badge} | {_fmt_pct(s['uptime_24h'])} | {_fmt_pct(s['uptime_7d'])} | "
                   f"{_fmt_pct(s['uptime_all'])} | {_fmt_ms(s['avg_latency_ms'])} | {_fmt_ms(s['p95_latency_ms'])} | "
                   f"{_fmt_ms(s['max_latency_ms'])} | {s['total_checks']} ({s['failed_checks']}) |")
    out += ["", "## Incidents", ""]
    rows = [(s["name"], i) for s in summary["services"] for i in s["incidents"]]
    if not rows:
        out.append("No incidents recorded. 🎉")
    else:
        out += ["| Service | Started (UTC) | Resolved (UTC) | Duration | Failed checks | Cause |", "|---|---|---|---|---|---|"]
        for name, i in sorted(rows, key=lambda x: x[1]["start"], reverse=True):
            end = "**ongoing**" if i["ongoing"] else i["end"]
            out.append(f"| {name} | {i['start']} | {end} | {fmt_duration(i['duration_seconds'])} | "
                       f"{i['failed_checks']} | {i['reason']} |")
    out += ["", "## Status codes observed", ""]
    for s in summary["services"]:
        codes = ", ".join(f"`{c}` × {n}" for c, n in s["status_codes"].items()) or "none"
        out.append(f"- **{s['name']}**: {codes}")
    out += ["", "## Latest checks", "", "| Time (UTC) | Service | Status | Latency | Result |", "|---|---|---|---|---|"]
    latest = sorted((r for s in summary["services"] for r in s["records"][-5:]),
                    key=lambda r: r["timestamp"], reverse=True)[:15]
    for r in latest:
        code = r["status_code"] if r["status_code"] is not None else "—"
        res = "OK" if r["ok"] else f"FAIL: {r.get('error')}"
        out.append(f"| {r['timestamp']} | {r['name']} | {code} | {r['latency_ms']:.0f} ms | {res} |")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- HTML dashboard
def _strip(records: list[dict], n: int = 90) -> str:
    """Availability strip: the whole history split into at most n ticks, oldest on the left.

    A tick is red if any check in its bucket failed, amber if any was slow, otherwise green.
    """
    if not records:
        return '<div class="strip" role="img" aria-label="No checks recorded">' + '<i class="none"></i>' * n + "</div>"
    size = max(1, math.ceil(len(records) / n))
    buckets = [records[i:i + size] for i in range(0, len(records), size)]
    ticks = []
    for b in buckets:
        failed = [r for r in b if not r["ok"]]
        slow = [r for r in b if r["ok"] and r["latency_level"] in ("warn", "critical")]
        cls = "bad" if failed else ("slow" if slow else "good")
        first, last = b[0]["timestamp"], b[-1]["timestamp"]
        when = first if first == last else f"{first} to {last}"
        tip = f"{when}: {len(b)} check(s), {len(failed)} failed, {len(slow)} slow"
        if failed:
            tip += f" - {failed[0].get('error')}"
        ticks.append(f'<i class="{cls}" title="{html.escape(tip)}"></i>')
    ticks = ['<i class="none"></i>'] * (n - len(ticks)) + ticks
    return (f'<div class="strip" role="img" aria-label="Availability over {len(records)} checks">'
            f'{"".join(ticks)}</div>')


def _latency_chart(records: list[dict], th: dict, points: int) -> str:
    recent = records[-points:]
    if len(recent) < 2:
        return ""
    W, H, PL, PB, PT = 720, 120, 44, 16, 8
    ok_vals = [r["latency_ms"] for r in recent if r["ok"]] or [0]
    ymax = max(max(ok_vals) * 1.15, th["latency_warn_ms"] * 1.1, 100)
    x = lambda i: PL + (W - PL - 6) * i / (len(recent) - 1)  # noqa: E731
    y = lambda v: PT + (H - PT - PB) * (1 - min(v, ymax) / ymax)  # noqa: E731

    segs, cur = [], []
    for i, r in enumerate(recent):
        if r["ok"]:
            cur.append(f"{x(i):.1f},{y(r['latency_ms']):.1f}")
        elif cur:
            segs.append(cur)
            cur = []
    if cur:
        segs.append(cur)
    lines = "".join(f'<polyline points="{" ".join(s)}" class="lat"/>' for s in segs if len(s) > 1)
    dots = "".join(f'<circle cx="{s[0].split(",")[0]}" cy="{s[0].split(",")[1]}" r="2" class="latdot"/>'
                   for s in segs if len(s) == 1)
    fails = "".join(f'<rect x="{x(i) - 1.5:.1f}" y="{PT}" width="3" height="{H - PT - PB}" class="failband"/>'
                    for i, r in enumerate(recent) if not r["ok"])
    guides = ""
    for key, cls in (("latency_warn_ms", "warnline"), ("latency_critical_ms", "critline")):
        v = th[key]
        if v <= ymax:
            guides += f'<line x1="{PL}" x2="{W - 6}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="{cls}"/>' \
                      f'<text x="{PL - 6}" y="{y(v) + 3:.1f}" text-anchor="end" class="axis">{v:,}</text>'
    base = f'<text x="{PL - 6}" y="{y(0) + 3:.1f}" text-anchor="end" class="axis">0</text>' \
           f'<line x1="{PL}" x2="{W - 6}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="baseline"/>'
    return (f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Latency in milliseconds, last {len(recent)} checks">'
            f'{fails}{base}{guides}{lines}{dots}</svg>')


def render_html(summary: dict, cfg: dict) -> str:
    title = html.escape(cfg["report"]["title"])
    th = cfg["thresholds"]
    services = summary["services"]
    down = [s for s in services if s["current"] == "DOWN"]
    if not services or summary["total_checks"] == 0:
        headline, state = "No checks recorded yet", "idle"
    elif down:
        names = ", ".join(s["name"] for s in down)
        headline = f"{len(down)} of {len(services)} services down: {names}"
        state = "down"
    else:
        headline, state = f"All {len(services)} services operating normally", "up"
    gen = summary["generated"].strftime("%d %b %Y, %H:%M UTC")

    rows = []
    for s in services:
        pill = {"UP": ("up", "Up"), "DOWN": ("down", "Down")}.get(s["current"], ("idle", "No data"))
        codes = " ".join(f'<span class="code">{html.escape(c)}<b>{n}</b></span>' for c, n in s["status_codes"].items())
        last = ""
        if s["last_check"]:
            last = (f'Last check {html.escape(s["last_check"].replace("T", " ").replace("Z", " UTC"))}: '
                    f'{s["last_status_code"] if s["last_status_code"] is not None else "no response"}, '
                    f'{s["last_latency_ms"]:.0f} ms')
            if s["last_error"]:
                last += f' — {html.escape(s["last_error"])}'
        rows.append(f'''
    <section class="svc">
      <header>
        <div><h2>{html.escape(s["name"])}</h2><a href="{html.escape(s["url"])}">{html.escape(s["url"])}</a></div>
        <span class="pill {pill[0]}">{pill[1]}</span>
      </header>
      {_strip(s["records"])}
      <dl class="metrics">
        <div><dt>Uptime, 24 h</dt><dd>{_fmt_pct(s["uptime_24h"])}</dd></div>
        <div><dt>Uptime, 7 d</dt><dd>{_fmt_pct(s["uptime_7d"])}</dd></div>
        <div><dt>Uptime, all time</dt><dd>{_fmt_pct(s["uptime_all"])}</dd></div>
        <div><dt>Avg latency</dt><dd>{_fmt_ms(s["avg_latency_ms"])}</dd></div>
        <div><dt>p95 latency</dt><dd>{_fmt_ms(s["p95_latency_ms"])}</dd></div>
        <div><dt>Slowest</dt><dd>{_fmt_ms(s["max_latency_ms"])}</dd></div>
      </dl>
      {_latency_chart(s["records"], th, cfg["report"]["chart_points"])}
      <p class="foot">{last}</p>
      <p class="codes">Status codes: {codes or "none"}</p>
    </section>''')

    inc_rows = [(s["name"], i) for s in services for i in s["incidents"]]
    if inc_rows:
        body = "".join(
            f'<tr><td>{html.escape(n)}</td><td>{html.escape(i["start"])}</td>'
            f'<td>{"<b class=ongoing>ongoing</b>" if i["ongoing"] else html.escape(i["end"])}</td>'
            f'<td>{fmt_duration(i["duration_seconds"])}</td><td>{i["failed_checks"]}</td>'
            f'<td>{html.escape(i["reason"])}</td></tr>'
            for n, i in sorted(inc_rows, key=lambda x: x[1]["start"], reverse=True))
        incidents = ('<div class="scroll"><table><thead><tr><th>Service</th><th>Started (UTC)</th><th>Resolved (UTC)</th>'
                     f'<th>Duration</th><th>Failed checks</th><th>Cause</th></tr></thead><tbody>{body}</tbody></table></div>')
    else:
        incidents = '<p class="empty">No incidents in the recorded history.</p>'

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{
  --bg:#eef1f4; --panel:#ffffff; --ink:#16202a; --muted:#5a6876; --line:#d5dce3;
  --up:#17806a; --slow:#d19a1f; --down:#c4372d; --none:#dfe5ea; --accent:#1c4e80;
}}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#0f161d; --panel:#16202a; --ink:#e6ebf0; --muted:#97a5b3; --line:#2a3745;
          --up:#3bb89a; --slow:#e3b341; --down:#f0645a; --none:#233140; --accent:#7fb2e5; }}
}}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:16px/1.5 "Source Sans 3","Segoe UI",system-ui,-apple-system,Roboto,"Helvetica Neue",sans-serif; }}
main {{ max-width: 980px; margin: 0 auto; padding: 40px 20px 64px; }}
h1 {{ font-size: clamp(1.7rem, 4vw, 2.4rem); line-height:1.15; margin:0 0 6px; letter-spacing:-0.01em; }}
.banner {{ display:flex; gap:14px; align-items:center; padding:18px 20px; border-radius:6px; margin:22px 0 34px;
  border:1px solid var(--line); border-left-width:8px; background:var(--panel); font-size:1.15rem; font-weight:600; }}
.banner.up {{ border-left-color:var(--up); }} .banner.down {{ border-left-color:var(--down); }} .banner.idle {{ border-left-color:var(--none); }}
.sub {{ color:var(--muted); margin:0; }}
.svc {{ background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:20px 22px; margin-bottom:18px; }}
.svc header {{ display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:14px; }}
.svc h2 {{ font-size:1.2rem; margin:0; }}
.svc header a {{ color:var(--muted); font-size:.88rem; word-break:break-all; text-decoration:none; border-bottom:1px dotted var(--muted); }}
.pill {{ font-weight:700; font-size:.85rem; padding:3px 12px; border-radius:99px; color:#fff; white-space:nowrap; }}
.pill.up {{ background:var(--up); }} .pill.down {{ background:var(--down); }} .pill.idle {{ background:var(--muted); }}
.strip {{ display:grid; grid-template-columns:repeat(90,1fr); gap:2px; height:34px; }}
.strip i {{ border-radius:2px; background:var(--none); }}
.strip i.good {{ background:var(--up); }} .strip i.slow {{ background:var(--slow); }} .strip i.bad {{ background:var(--down); }}
.metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:14px 20px; margin:18px 0 6px; }}
.metrics dt {{ color:var(--muted); font-size:.82rem; }}
.metrics dd {{ margin:0; font-size:1.3rem; font-weight:600; font-variant-numeric:tabular-nums; }}
.chart {{ width:100%; height:auto; margin-top:10px; display:block; }}
.chart .lat {{ fill:none; stroke:var(--accent); stroke-width:1.6; stroke-linejoin:round; }}
.chart .latdot {{ fill:var(--accent); }}
.chart .failband {{ fill:var(--down); opacity:.28; }}
.chart .warnline {{ stroke:var(--slow); stroke-dasharray:4 4; }} .chart .critline {{ stroke:var(--down); stroke-dasharray:4 4; }}
.chart .baseline {{ stroke:var(--line); }}
.chart .axis {{ fill:var(--muted); font-size:9px; font-variant-numeric:tabular-nums; }}
.foot, .codes {{ color:var(--muted); font-size:.85rem; margin:8px 0 0; }}
.code {{ display:inline-block; margin-right:10px; font-variant-numeric:tabular-nums; }}
.code b {{ margin-left:4px; color:var(--ink); }}
h3 {{ font-size:1.2rem; margin:38px 0 10px; }}
.scroll {{ overflow-x:auto; background:var(--panel); border:1px solid var(--line); border-radius:6px; }}
table {{ border-collapse:collapse; width:100%; font-size:.9rem; }}
th, td {{ text-align:left; padding:10px 14px; border-bottom:1px solid var(--line); white-space:nowrap; font-variant-numeric:tabular-nums; }}
td:last-child {{ white-space:normal; }}
th {{ color:var(--muted); font-weight:600; font-size:.82rem; }}
tr:last-child td {{ border-bottom:0; }}
.ongoing {{ color:var(--down); }}
.empty {{ color:var(--muted); }}
.legend {{ display:flex; gap:18px; flex-wrap:wrap; color:var(--muted); font-size:.85rem; margin:0 0 14px; }}
.legend span::before {{ content:""; display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:6px; vertical-align:-1px; background:var(--c); }}
footer {{ margin-top:40px; color:var(--muted); font-size:.85rem; }}
@media (max-width:560px) {{ .strip {{ grid-template-columns:repeat(45,1fr); }} .strip i:nth-child(-n+45) {{ display:none; }} }}
</style>
</head>
<body>
<main>
  <h1>{title}</h1>
  <p class="sub">Updated {gen} · {summary["total_checks"]:,} checks recorded · alert thresholds: latency warn {th["latency_warn_ms"]:,} ms / critical {th["latency_critical_ms"]:,} ms, uptime target {th["uptime_min_percent"]}%</p>
  <div class="banner {state}" role="status">{html.escape(headline)}</div>
  <p class="legend"><span style="--c:var(--up)">Healthy</span><span style="--c:var(--slow)">Slow (above warning threshold)</span><span style="--c:var(--down)">Failed</span><span style="--c:var(--none)">No data</span></p>
  {"".join(rows)}
  <h3>Incident history</h3>
  {incidents}
  <footer>Generated by cloudmon. Each tick in the strip is one check, or a group of consecutive checks once the history is longer; a tick is red if any check in it failed. Oldest on the left. Hover a tick for details.</footer>
</main>
</body>
</html>
'''


def generate_reports(records: list[dict], cfg: dict, out_dir: str | Path | None = None,
                     now: datetime | None = None) -> dict[str, Path]:
    out = Path(out_dir or cfg["report"]["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    summary = summarize(records, cfg, now)
    paths = {
        "markdown": out / "uptime_report.md",
        "html": out / "index.html",
        "csv": out / "uptime_log.csv",
    }
    paths["markdown"].write_text(render_markdown(summary, cfg["report"]["title"]), encoding="utf-8")
    paths["html"].write_text(render_html(summary, cfg), encoding="utf-8")
    write_csv(records, paths["csv"])
    return paths
