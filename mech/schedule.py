"""When to post: data/schedule.json lists songs and the earliest time each may go out.

    [{"slug": "jingle-bells", "at": "2026-11-15T17:00:00Z"}, ...]

A slot is due when its time has come, the previous post is at least MIN_GAP_HOURS old and the
clock (Europe/London) is inside POST_WINDOW ("9-24"). Only json is needed here: the hourly
check runs before anything heavy is installed.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


def _load(name: str, default):
    p = DATA / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def london_hour(now: datetime) -> int:
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("Europe/London")).hour
    except Exception:
        return now.hour


def due(now: datetime | None = None) -> str | None:
    now = now or datetime.now(timezone.utc)
    gap = timedelta(hours=float(os.environ.get("MIN_GAP_HOURS", "3")))
    lo, hi = (int(x) for x in os.environ.get("POST_WINDOW", "9-24").split("-"))
    if not (lo <= london_hour(now) < hi):
        return None
    published = _load("published", [])
    done = {p["slug"] for p in published}
    last = max((_t(p["date"]) for p in published), default=None)
    if last and now - last < gap:
        return None
    for slot in _load("schedule", []):
        if slot["slug"] in done:
            continue
        return slot["slug"] if _t(slot["at"]) <= now else None
    return None


if __name__ == "__main__":
    print(due() or "")
