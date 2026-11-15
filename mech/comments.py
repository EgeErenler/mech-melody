"""Answer new comments on our recent Reels, and turn song requests into the queue."""
from __future__ import annotations

import os

from . import claude, instagram, state

MAX_REPLIES = int(os.environ.get("MAX_REPLIES_PER_RUN", "40"))


def run(dry: bool = False, posts: int = 10) -> dict:
    done = state.load("comments", {"replied": [], "skipped": []})
    seen = set(done["replied"]) | set(done["skipped"])
    me = instagram.me().get("username", "")
    stats = {"replied": 0, "requests": 0, "skipped": 0}
    for media in instagram.recent_media(posts):
        title = (media.get("caption") or "").split("\n")[0][:80]
        for c in instagram.comments(media["id"]):
            if c["id"] in seen or c.get("username") == me:
                continue
            # someone (us, by hand) already answered it
            if any(r.get("username") == me for r in (c.get("replies") or {}).get("data", [])):
                done["skipped"].append(c["id"])
                continue
            if stats["replied"] >= MAX_REPLIES:
                break
            q_titles = [f"{e['title']} — {e['artist']}".strip(" —") for e in state.ranked()]
            try:
                t = claude.triage_comment(c.get("text", ""), c.get("username", ""), title, q_titles)
            except Exception as e:  # one bad answer must not stop the run
                print(f"comment {c['id']}: triage failed: {e}")
                continue
            kind, reply = t.get("kind"), t.get("reply")
            if kind == "request" and t.get("song"):
                if not dry:
                    entry, new = state.request(t["song"], t.get("artist"), c.get("username", ""), c["id"])
                    pos = state.position(entry["key"])
                    print(f"request: {entry['title']} ({entry['votes']} votes, #{pos} in the queue){' NEW' if new else ''}")
                stats["requests"] += 1
            if not reply or kind in ("spam", "abuse"):
                done["skipped"].append(c["id"])
                stats["skipped"] += 1
                continue
            print(f"@{c.get('username')}: {c.get('text', '')[:80]!r}\n  -> {reply}")
            if not dry:
                instagram.reply(c["id"], reply)
                done["replied"].append(c["id"])
            stats["replied"] += 1
    if not dry:
        done["replied"] = done["replied"][-5000:]
        done["skipped"] = done["skipped"][-5000:]
        state.save("comments", done)
    print(stats)
    return stats
