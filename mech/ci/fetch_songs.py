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


def _norm(x: str) -> list[str]:
    import unicodedata
    x = unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode().lower()
    return [w for w in re.split(r"[^a-z0-9]+", x) if w and w not in ("the", "a", "feat", "ft")]


def search_midi(title: str, artist: str, want: int = 3) -> list[str]:
    """No URLs given: look the song up on freemidi.org and bitmidi.com. A link counts when it
    names every word of the title and at least one of the artist's."""
    tw, aw = _norm(title), _norm(artist)
    found: list[tuple[int, str]] = []
    q = urllib.parse.quote(f"{title} {artist}")
    try:
        html = get(opener(), f"https://freemidi.org/search?q={urllib.parse.quote(title)}").decode("utf-8", "replace")
        for href, name in re.findall(r'href="?(download3-\d+-[^"\s>]+)"?[^>]*>([^<]*)<', html):
            words = set(_norm(href + " " + name))
            if all(w in words for w in tw) and any(w in words for w in aw):  # "the hills" alone matched Led Zeppelin
                found.append((sum(w in words for w in aw), "https://freemidi.org/" + href))
    except Exception as e:
        print(f"  freemidi search failed: {e}")
    time.sleep(2)
    try:
        html = get(opener(), f"https://bitmidi.com/search?q={q}").decode("utf-8", "replace")
        for href in dict.fromkeys(re.findall(r'href="(/[a-z0-9-]+-mid)"', html)):
            words = set(_norm(href))
            if all(w in words for w in tw) and any(w in words for w in aw):
                page = get(opener(), "https://bitmidi.com" + href).decode("utf-8", "replace")
                up = re.search(r'(/uploads/\d+\.mid)', page)
                if up:
                    found.append((sum(w in words for w in aw), "https://bitmidi.com" + up.group(1)))
                time.sleep(1)
    except Exception as e:
        print(f"  bitmidi search failed: {e}")
    # more free, user-made MIDI archives
    for search, link, file in (
        (f"https://www.midiworld.com/search/?q={q}", r'href="(https://www\.midiworld\.com/download/\d+)"[^>]*>([^<]*)', None),
        (f"https://onlinesequencer.net/sequences?search={urllib.parse.quote(title)}", r'href="/(\d{4,})"[^>]*>([^<]*)', "https://onlinesequencer.net/app/midi.php?id={}"),
        (f"https://www.mididb.com/search/?q={q}", r'href="(/[^"]+\.mid)"[^>]*>([^<]*)', "https://www.mididb.com{}"),
    ):
        if len(found) >= want:
            break
        time.sleep(2)
        try:
            html = get(opener(), search).decode("utf-8", "replace")
            for href, name in re.findall(link, html)[:200]:
                words = set(_norm(href + " " + name))
                # user sequences often carry the title only: then the whole title must be there
                if all(w in words for w in tw) and (any(w in words for w in aw) or len(tw) >= 2):
                    found.append((sum(w in words for w in aw), file.format(href) if file else href))
        except Exception as e:
            print(f"  {search.split('/')[2]} search failed: {e}")
    found.sort(key=lambda x: -x[0])
    return [u for _, u in found[:want]]


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
        if not r.get("midi") and not any(d.glob("source*.mid")):
            r["midi"] = search_midi(r["title"], r["artist"])
            print(f"{r['slug']}: search found {len(r['midi'])}: {r['midi']}")
        for i, url in enumerate(r.get("midi") or []):
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
