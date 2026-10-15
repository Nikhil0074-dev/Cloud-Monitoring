"""Persistence: JSON-lines check log, alert state file, retention pruning."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_ts(value: str) -> datetime:
    """Parse the ISO-8601 UTC timestamps written by the checker."""
    return datetime.strptime(value.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z") \
        if "." in value else datetime.strptime(value.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")


def append_results(path: str | Path, results: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")


def read_log(path: str | Path) -> list[dict]:
    """Read all records, skipping blank or corrupt lines (e.g. a half-written line)."""
    path = Path(path)
    if not path.exists():
        return []
    records = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and "timestamp" in rec and "name" in rec:
                records.append(rec)
    return records


def prune_log(path: str | Path, retention_days: int) -> int:
    """Drop records older than retention_days. Returns number removed."""
    if not retention_days or retention_days <= 0:
        return 0
    records = read_log(path)
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    kept = []
    for record in records:
        try:
            if parse_ts(record["timestamp"]) >= cutoff:
                kept.append(record)
        except (KeyError, TypeError, ValueError):
            continue
    removed = len(records) - len(kept)
    if removed:
        _atomic_write(Path(path), "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in kept))
    return removed


def load_state(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(path: str | Path, state: dict) -> None:
    _atomic_write(Path(path), json.dumps(state, indent=2, sort_keys=True) + "\n")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
