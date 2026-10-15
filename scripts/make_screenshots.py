#!/usr/bin/env python3
"""Render the README screenshots from real artefacts (needs `pip install playwright && playwright install chromium`).

  docs/screenshots/console_alerts.png         <- docs/demo_transcript.txt (real console output of scripts/demo.py)
  docs/screenshots/webhook_notifications.png  <- demo_output/webhook_payloads.jsonl (payloads the webhook receiver captured)
  docs/screenshots/dashboard.png              <- sample_reports/index.html

Run `python scripts/demo.py | tee docs/demo_transcript.txt` first. Replace these with captures from your own
Slack/Discord channel once you have configured a real webhook.
"""
import html
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "docs" / "screenshots"


def console_page(text: str) -> str:
    out = []
    for line in text.splitlines():
        esc = html.escape(line)
        if "[CRITICAL]" in line:
            cls = "crit"
        elif "[WARNING]" in line:
            cls = "warn"
        elif "[INFO]" in line:
            cls = "info"
        elif line.startswith("==="):
            cls = "hdr"
        elif "| DOWN |" in line:
            cls = "down"
        else:
            cls = ""
        out.append(f'<div class="{cls}">{esc or "&nbsp;"}</div>')
    return f"""<html><body style="margin:0;background:#12181f"><div class="win">
<div class="bar"><b></b><b></b><b></b><span>python scripts/demo.py</span></div>
<pre>{''.join(out)}</pre></div>
<style>
.win{{width:1220px;margin:0;background:#12181f;font:13px/1.5 'DejaVu Sans Mono',monospace;color:#c9d4df}}
.bar{{background:#1d2630;padding:9px 14px;display:flex;gap:7px;align-items:center;color:#8a99a8}}
.bar b{{width:11px;height:11px;border-radius:50%;background:#3a4755;display:inline-block}} .bar span{{margin-left:12px}}
pre{{margin:0;padding:14px 18px 18px}} .hdr{{color:#7fb2e5;font-weight:bold;margin-top:6px}}
.crit{{color:#ff6b61;font-weight:bold;background:#3a1a1a}} .warn{{color:#f0b84a;font-weight:bold}}
.info{{color:#4fd1a5;font-weight:bold}} .down{{color:#ff9d96}}
</style></body></html>"""


def webhook_page(payloads: list[dict]) -> str:
    colors = {"🔴": "#e01e5a", "🟠": "#ecb22e", "🟢": "#2eb67d"}
    items = []
    for p in payloads:
        text = p["text"]
        bar = colors.get(text[:1], "#888")
        icon, rest = text[0], text[1:].strip()
        m = re.match(r"\[(\w+)\] (\w+) \| (.*?) \| (\S+)$", rest)
        sev, kind, msg, ts = m.groups() if m else ("", "", rest, "")
        items.append(f"""<div class="msg"><div class="av">cm</div><div class="body">
<div class="who"><b>cloudmon</b> <span class="app">APP</span> <span class="t">{html.escape(ts)}</span></div>
<div class="card" style="border-left-color:{bar}"><div class="h">{icon} {html.escape(kind.replace('_', ' ').title())}</div>
<div>{html.escape(msg)}</div><div class="meta">Severity: {html.escape(sev.lower())}</div></div></div></div>""")
    return f"""<html><body style="margin:0;background:#fff"><div class="ch">
<div class="top"># alerts<span>Notifications received by the webhook</span></div>{''.join(items)}
<div class="note">Rendered from the Slack-format JSON payloads captured by the demo's local webhook receiver (demo_output/webhook_payloads.jsonl).</div></div>
<style>
.ch{{width:820px;font:15px/1.45 'DejaVu Sans',Arial,sans-serif;color:#1d1c1d;background:#fff}}
.top{{padding:14px 20px;border-bottom:1px solid #ddd;font-weight:700;font-size:17px}} .top span{{font-weight:400;font-size:13px;color:#696969;margin-left:14px}}
.msg{{display:flex;gap:12px;padding:10px 20px}} .av{{width:38px;height:38px;border-radius:6px;background:#1c4e80;color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;flex:none}}
.who b{{font-size:15px}} .app{{background:#e8e8e8;font-size:10px;padding:1px 4px;border-radius:3px;color:#555;margin-left:4px}} .t{{color:#696969;font-size:12px;margin-left:6px}}
.body{{flex:1}} .card{{border-left:4px solid;padding:3px 0 3px 12px;margin-top:4px}} .h{{font-weight:700;margin-bottom:2px}} .meta{{font-size:12px;color:#696969;margin-top:2px}}
.note{{padding:12px 20px;font-size:12px;color:#696969;border-top:1px solid #ddd;margin-top:8px}}
</style></body></html>"""


def main() -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    transcript = (ROOT / "docs" / "demo_transcript.txt").read_text()
    payloads = [json.loads(l) for l in (ROOT / "demo_output" / "webhook_payloads.jsonl").read_text().splitlines() if l.strip()]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1220, "height": 400})
        page.set_content(console_page(transcript))
        page.locator(".win").screenshot(path=str(SHOTS / "console_alerts.png"))

        page = browser.new_page(viewport={"width": 820, "height": 400}, device_scale_factor=2)
        page.set_content(webhook_page(payloads))
        page.locator(".ch").screenshot(path=str(SHOTS / "webhook_notifications.png"))

        page = browser.new_page(viewport={"width": 1040, "height": 900}, device_scale_factor=1.5)
        page.goto((ROOT / "sample_reports" / "index.html").as_uri())
        page.screenshot(path=str(SHOTS / "dashboard.png"), full_page=True)
        browser.close()
    print("Screenshots written to", SHOTS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
