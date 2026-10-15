"""Alert delivery: console, webhook (Slack / Discord / generic JSON) and e-mail."""
from __future__ import annotations

import json
import os
import smtplib
import sys
import time
from email.message import EmailMessage

import requests

ICONS = {"critical": "🔴", "warning": "🟠", "info": "🟢"}
ANSI = {"critical": "\033[1;31m", "warning": "\033[1;33m", "info": "\033[1;32m"}
RESET = "\033[0m"


def format_text(event: dict) -> str:
    icon = ICONS.get(event["severity"], "")
    return f"{icon} [{event['severity'].upper()}] {event['type']} | {event['message']} | {event['timestamp']}"


def send_console(event: dict, stream=None) -> None:
    stream = stream or sys.stderr
    line = format_text(event)
    if hasattr(stream, "isatty") and stream.isatty():
        line = f"{ANSI.get(event['severity'], '')}{line}{RESET}"
    print(line, file=stream, flush=True)
    # GitHub Actions turns these workflow commands into annotations on the run summary.
    if os.environ.get("GITHUB_ACTIONS") == "true":
        cmd = {"critical": "error", "warning": "warning"}.get(event["severity"], "notice")
        print(f"::{cmd} title={event['type']} {event['endpoint']}::{event['message']}", flush=True)


def build_webhook_payload(event: dict, fmt: str) -> dict:
    text = format_text(event)
    if fmt == "slack":
        return {"text": text, "username": "cloudmon"}
    if fmt == "discord":
        return {"content": text[:1990], "username": "cloudmon"}
    if fmt == "teams":
        return {"text": text}
    return {"source": "cloudmon", "text": text, **event}


def send_webhook(event: dict, webhook_cfg: dict, attempts: int = 3) -> tuple[bool, str]:
    url = os.environ.get(webhook_cfg.get("url_env", "WEBHOOK_URL"), "").strip()
    if not url:
        return False, f"env var {webhook_cfg.get('url_env', 'WEBHOOK_URL')} is not set"
    payload = build_webhook_payload(event, webhook_cfg.get("format", "generic"))
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.post(url, data=json.dumps(payload),
                                 headers={"Content-Type": "application/json"}, timeout=10)
            if resp.status_code < 300:
                return True, f"HTTP {resp.status_code}"
            last = f"HTTP {resp.status_code}"
        except requests.exceptions.RequestException as exc:
            last = exc.__class__.__name__
        if attempt < attempts:
            time.sleep(min(2 ** attempt, 5))
    return False, last


def send_email(event: dict, email_cfg: dict) -> tuple[bool, str]:
    env = os.environ
    needed = ["SMTP_HOST", "EMAIL_FROM", "EMAIL_TO"]
    missing = [k for k in needed if not env.get(k)]
    if missing:
        return False, f"missing env vars: {', '.join(missing)}"
    msg = EmailMessage()
    msg["Subject"] = f"[cloudmon] {event['type']}: {event['endpoint']}"
    msg["From"], msg["To"] = env["EMAIL_FROM"], env["EMAIL_TO"]
    msg.set_content(
        f"{event['message']}\n\nEndpoint: {event['url']}\nSeverity: {event['severity']}\n"
        f"Time (UTC): {event['timestamp']}\n"
    )
    try:
        port = int(env.get("SMTP_PORT") or (465 if email_cfg.get("use_ssl") else 587))
        if email_cfg.get("use_ssl"):
            server = smtplib.SMTP_SSL(env["SMTP_HOST"], port, timeout=15)
        else:
            server = smtplib.SMTP(env["SMTP_HOST"], port, timeout=15)
            server.starttls()
        with server:
            if env.get("SMTP_USER"):
                server.login(env["SMTP_USER"], env.get("SMTP_PASSWORD", ""))
            server.send_message(msg)
        return True, "sent"
    except (smtplib.SMTPException, OSError, ValueError) as exc:
        return False, exc.__class__.__name__


def dispatch(events: list[dict], alerts_cfg: dict) -> list[dict]:
    """Deliver every event through every enabled channel. Delivery failures never raise."""
    deliveries = []
    warned: set[str] = set()  # print each distinct delivery problem once per run, not once per alert
    for ev in events:
        if alerts_cfg.get("console", True):
            send_console(ev)
        wh = alerts_cfg.get("webhook") or {}
        if wh.get("enabled"):
            ok, detail = send_webhook(ev, wh)
            deliveries.append({"event": ev["type"], "channel": "webhook", "ok": ok, "detail": detail})
            if not ok and ("webhook", detail) not in warned:
                warned.add(("webhook", detail))
                print(f"[cloudmon] webhook delivery failed: {detail}", file=sys.stderr)
        em = alerts_cfg.get("email") or {}
        if em.get("enabled"):
            ok, detail = send_email(ev, em)
            deliveries.append({"event": ev["type"], "channel": "email", "ok": ok, "detail": detail})
            if not ok and ("email", detail) not in warned:
                warned.add(("email", detail))
                print(f"[cloudmon] email delivery failed: {detail}", file=sys.stderr)
    return deliveries
