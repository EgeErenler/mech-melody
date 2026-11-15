"""The automation's memory, kept as JSON in data/ and committed back by the workflows."""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone

from .paths import DATA


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(name: str, default):
    p = DATA / f"{name}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return default


def save(name: str, value) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def key(title: str, artist: str | None = "") -> str:
    s = f"{title} {artist or ''}".lower().translate(str.maketrans({"ı": "i", "ø": "o", "ß": "ss", "æ": "ae", "œ": "oe", "ł": "l", "đ": "d"}))
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


# ---------------------------------------------------------------- the request queue

def queue() -> list[dict]:
    return load("queue", [])


def request(title: str, artist: str | None, username: str, comment_id: str) -> tuple[dict, bool]:
    """Add a request or a vote. Returns (entry, is_new)."""
    q = queue()
    k = key(title, artist)
    k_title = key(title)
    for e in q:
        if e["key"] == k or (not artist and key(e["title"]) == k_title):
            if username not in e["requested_by"]:
                e["requested_by"].append(username)
                e["votes"] += 1
            e.setdefault("comments", []).append(comment_id)
            save("queue", q)
            return e, False
    e = {"key": k, "title": title, "artist": artist or "", "votes": 1, "requested_by": [username], "comments": [comment_id],
         "status": "requested", "slug": None, "first_requested": now()}
    q.append(e)
    save("queue", q)
    return e, True


def ranked() -> list[dict]:
    """Open requests, most wanted first (ties: oldest first)."""
    return sorted([e for e in queue() if e["status"] in ("requested", "ready")], key=lambda e: (-e["votes"], e["first_requested"]))


def position(entry_key: str) -> int:
    for i, e in enumerate(ranked()):
        if e["key"] == entry_key:
            return i + 1
    return 0
