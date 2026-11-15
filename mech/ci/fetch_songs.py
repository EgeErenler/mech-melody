"""Fetch free MIDI arrangements (and, when a MIDI has no words, the plain lyrics) for the songs
listed in requests/songs.json, into songs/<slug>/. Runs in CI (open network).

    [{"slug": "...", "title": "...", "artist": "...", "midi": ["https://bitmidi.com/uploads/1.mid",
      "https://freemidi.org/download3-123-name"]}, ...]

freemidi.org pages hand out the file from /getter-<id> to a session that opened the page first.
Lyrics come from lrclib.net (plain text, no timings needed: the build lays them on the tune).
"""
from __future__ import annotations

import http.cookiejar
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}


def opener():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def get(op, url, referer=None) -> bytes:
    h = dict(UA)
    if referer:
        h["Referer"] = referer
    with op.open(urllib.request.Request(url, headers=h), timeout=60) as r:
        return r.read()


def fetch_midi(url: str) -> bytes:
    op = opener()
    m = re.search(r"freemidi\.org/download3-(\d+)", url)
    if m:
        get(op, url)
        time.sleep(2)
        data = get(op, f"https://freemidi.org/getter-{m.group(1)}", referer=url)
    else:
        data = get(op, url)
    if not data.startswith(b"MThd"):
        raise RuntimeError(f"{url}: not a MIDI file ({data[:60]!r})")
    return data


def has_words(mid: Path) -> int:
    import mido
    n = 0
    for tr in mido.MidiFile(str(mid)).tracks:
        n += sum(1 for m in tr if m.type == "lyrics" or (m.type == "text" and not m.text.startswith("@")))
    return n


def fetch_lyrics(title: str, artist: str) -> str | None:
    q = urllib.parse.urlencode({"track_name": title, "artist_name": artist})
    try:
        data = json.loads(get(opener(), f"https://lrclib.net/api/get?{q}"))
    except Exception:
        try:
            res = json.loads(get(opener(), f"https://lrclib.net/api/search?{q}"))
            data = res[0] if res else {}
        except Exception:
            return None
    return data.get("plainLyrics")


def main() -> None:
    reqs = json.loads((ROOT / "requests" / "songs.json").read_text(encoding="utf-8"))
    only = set(sys.argv[1:])
    for r in reqs:
        if only and r["slug"] not in only:
            continue
        d = ROOT / "songs" / r["slug"]
        d.mkdir(parents=True, exist_ok=True)
        got = 0
        for i, url in enumerate(r["midi"]):
            out = d / ("source.mid" if i == 0 else f"source-{i + 1}.mid")
            if out.exists():
                got += 1
                continue
            try:
                out.write_bytes(fetch_midi(url))
                got += 1
                print(f"{r['slug']}: {out.name} <- {url}")
            except Exception as e:
                print(f"{r['slug']}: could not fetch {url}: {e}")
            time.sleep(3)
        if not got:
            continue
        words = max((has_words(p) for p in d.glob("source*.mid")), default=0)
        if words < 20 and not (d / "lyrics.txt").exists():
            text = fetch_lyrics(r["title"], r["artist"])
            if text:
                (d / "lyrics.txt").write_text(text.strip() + "\n", encoding="utf-8")
                print(f"{r['slug']}: lyrics.txt ({len(text.splitlines())} lines)")
            else:
                print(f"{r['slug']}: no lyrics found")
        info = subprocess_tracks(d)
        (d / "tracks.txt").write_text(info, encoding="utf-8")


def subprocess_tracks(d: Path) -> str:
    import subprocess
    out = []
    for p in sorted(d.glob("source*.mid")):
        r = subprocess.run([sys.executable, "-m", "mech", "tracks", str(p)], capture_output=True, text=True, cwd=ROOT)
        out.append(f"== {p.name}\n{r.stdout}{r.stderr}")
    return "\n".join(out)


if __name__ == "__main__":
    main()
