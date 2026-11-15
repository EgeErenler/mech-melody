"""Pick the next song, make its Reel and cover, post it, and tell whoever asked for it."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

from . import build, claude, instagram, state
from .paths import OUT, ROOT, SONGS


def songs() -> list[dict]:
    out = []
    for f in sorted(SONGS.glob("*/song.yaml")):
        spec = yaml.safe_load(f.read_text(encoding="utf-8"))
        if spec.get("disabled"):
            continue
        spec["slug"] = f.parent.name
        spec["_mtime"] = f.stat().st_mtime
        out.append(spec)
    return out


def next_song() -> dict | None:
    """Requested songs first (most votes), then `priority` (higher first), then the oldest folder."""
    published = {p["slug"] for p in state.load("published", [])}
    votes = {e["key"]: e["votes"] for e in state.queue() if e["status"] != "published"}
    todo = [s for s in songs() if s["slug"] not in published]
    if not todo:
        return None

    def rank(s):
        k = s.get("request") or state.key(s.get("title", ""), s.get("artist", ""))
        v = votes.get(k, votes.get(state.key(s.get("title", "")), 0))
        return (-v, -int(s.get("priority", 0)), s["_mtime"])

    return sorted(todo, key=rank)[0]


def render(slug: str, fps: int = 30) -> tuple[Path, Path]:
    subprocess.run(["bun", "mech/render_video.ts", slug, "--fps", str(fps)], cwd=ROOT, check=True)
    return OUT / slug / "reel.mp4", OUT / slug / "cover.jpg"


def host(slug: str, files: list[Path]) -> dict[str, str]:
    """Public URLs for Instagram to fetch: MEDIA_BASE_URL if you host files yourself, else a GitHub release."""
    base = os.environ.get("MEDIA_BASE_URL")
    if base:
        return {f.name: f"{base.rstrip('/')}/{slug}/{f.name}" for f in files}
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise RuntimeError("set MEDIA_BASE_URL, or run in GitHub Actions (a release hosts the files)")
    tag = f"reel-{slug}"
    have = subprocess.run(["gh", "release", "view", tag, "--json", "assets", "-q", ".assets[].name"], capture_output=True, text=True)
    if have.returncode == 0 and all(f.name in have.stdout.split() for f in files):
        return {f.name: f"https://github.com/{repo}/releases/download/{tag}/{f.name}" for f in files}
    subprocess.run(["gh", "release", "create", tag, *map(str, files), "--title", f"Reel: {slug}", "--notes", "Media for an Instagram Reel (fetched by Instagram).", "--latest=false"], check=True)
    return {f.name: f"https://github.com/{repo}/releases/download/{tag}/{f.name}" for f in files}


def run(slug: str | None = None, dry: bool = False, fps: int = 30) -> dict | None:
    spec = None
    if slug:
        spec = next((s for s in songs() if s["slug"] == slug), None)
        if not spec:
            raise SystemExit(f"no songs/{slug}/song.yaml")
    else:
        spec = next_song()
    if not spec:
        print("nothing to post: every song in songs/ is published. Add one (see docs/mech-melody.md).")
        return None
    slug = spec["slug"]
    print(f"next: {slug} — {spec.get('title')} / {spec.get('artist')}")
    video, cover = OUT / slug / "reel.mp4", OUT / slug / "cover.jpg"
    pre = OUT / slug / "studio.json"
    if video.exists() and cover.exists() and pre.exists():  # rendered ahead (the prerender workflow)
        import json
        show = json.loads(pre.read_text(encoding="utf-8"))
        print(f"using the prerendered {video}")
    else:
        show = build.build(slug, translate=claude.translate_lines)
        video, cover = render(slug, fps)
    entry = next((e for e in state.queue() if e["key"] in (spec.get("request"), state.key(spec.get("title", ""), spec.get("artist", ""))) and e["status"] != "published"), None)
    requested_by = entry["requested_by"][:5] if entry else []
    own = SONGS / slug / "caption.txt"  # a caption written by hand wins
    text = own.read_text(encoding="utf-8").strip() if own.exists() else claude.caption(show, requested_by)
    own_cover = SONGS / slug / "cover.jpg"
    if own_cover.exists():
        cover = own_cover
    (OUT / slug / "caption.txt").write_text(text, encoding="utf-8")
    print("caption:\n" + text)
    if dry:
        print(f"dry run: {video} and {cover} are ready; nothing posted")
        return {"slug": slug, "video": str(video), "cover": str(cover)}
    if os.environ.get("IG_API", "instagram") == "facebook" and not os.environ.get("MEDIA_BASE_URL"):
        urls = host(slug, [cover])
        info = instagram.publish_reel(video, text, cover_url=urls["cover.jpg"])
    else:
        urls = host(slug, [video, cover])
        info = instagram.publish_reel(video, text, video_url=urls["reel.mp4"], cover_url=urls["cover.jpg"])
    print(f"posted: {info.get('permalink')}")
    pub = state.load("published", [])
    pub.append({"slug": slug, "title": spec.get("title"), "artist": spec.get("artist"), "media_id": info["id"], "permalink": info.get("permalink"), "date": state.now()})
    state.save("published", pub)
    if entry:
        q = state.queue()
        for e in q:
            if e["key"] == entry["key"]:
                e["status"] = "published"
                e["permalink"] = info.get("permalink")
        state.save("queue", q)
        # tell the people who asked, under their own comments
        for cid in entry.get("comments", [])[:10]:
            try:
                instagram.reply(cid, f"Your request is live! 🤖 {spec.get('title')} → {info.get('permalink')}")
            except Exception as e:
                print(f"could not reply to {cid}: {e}")
    return info


def queue_report() -> str:
    """Markdown for the GitHub issue that lists what people want and what is missing to make it."""
    have = {state.key(s.get("title", ""), s.get("artist", "")): s["slug"] for s in songs()}
    have.update({state.key(s.get("title", "")): s["slug"] for s in songs()})
    pub = {p["slug"] for p in state.load("published", [])}
    lines = ["# 🎤 Song requests from Instagram", "", "Most wanted first. To make one, add `songs/<slug>/` with a MIDI and `song.yaml` (see `docs/mech-melody.md`).", "",
             "| # | Song | Artist | Votes | Status |", "|---|---|---|---|---|"]
    for i, e in enumerate(state.ranked(), 1):
        slug = have.get(e["key"]) or have.get(state.key(e["title"]))
        status = "✅ ready to post" if slug and slug not in pub else "📝 needs MIDI + lyrics"
        lines.append(f"| {i} | {e['title']} | {e['artist'] or '?'} | {e['votes']} | {status} |")
    nxt = next_song()
    lines += ["", f"**Next post:** {nxt['title']} ({nxt['slug']})" if nxt else "**Next post:** nothing left in `songs/` — add a song!"]
    done = [e for e in state.queue() if e["status"] == "published"]
    if done:
        lines += ["", "## Done", *[f"- [{e['title']}]({e.get('permalink', '')}) — {e['votes']} votes" for e in done[-20:]]]
    return "\n".join(lines) + "\n"
