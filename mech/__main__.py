"""mech.melody command line.

  python3 -m mech tracks <file.mid>                   list a MIDI file's tracks
  python3 -m mech new <slug> <file.mid> [--lang tr] [--title T] [--artist A]
                                                      start songs/<slug>/ from a MIDI file
  python3 -m mech listen <audio> <slug> [--start S] [--end S] [--title T] [--artist A]
        [--lang auto] [--arrangement band|acapella] [--voice auto|male|female|child] [--lyrics F] [--stems DIR]
                                                      hear a recording and write its robot arrangement
  python3 -m mech syllables <lang> "<lyrics>"         how the robots will read a line
  python3 -m mech build <slug>                        render the band's audio (public/shows/<slug>)
  python3 -m mech video <slug> [--seconds N]          build + the Reel and cover (out/<slug>)
  python3 -m mech next                                which song goes out next
  python3 -m mech publish [<slug>] [--dry-run]        build, render, caption, post to Instagram
  python3 -m mech comments [--dry-run]                reply to new comments, queue requests
  python3 -m mech queue                               the request queue as Markdown
  python3 -m mech refresh-token                       print a fresh Instagram token
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def _opt(args: list[str], name: str, default=None):
    if name in args:
        i = args.index(name)
        v = args[i + 1]
        del args[i : i + 2]
        return v
    return default


def _flag(args: list[str], name: str) -> bool:
    if name in args:
        args.remove(name)
        return True
    return False


GUESS = [  # (kind, test on the track) — used by `new` to pre-fill song.yaml
    ("voice", lambda t: any(w in t.name.lower() for w in ("vocal", "voice", "voc", "melody", "lead", "sing", "lyric", "vox"))),
    ("drums", lambda t: 9 in t.channels),
    ("bass", lambda t: (t.program or -1) in range(32, 40) or "bass" in t.name.lower()),
    ("guitar", lambda t: (t.program or -1) in range(24, 32) or "guitar" in t.name.lower() or "gtr" in t.name.lower()),
    ("piano", lambda t: (t.program or -1) in range(0, 8) or "piano" in t.name.lower() or "keys" in t.name.lower()),
    ("strings", lambda t: (t.program or -1) in range(40, 56) or "string" in t.name.lower()),
    ("synth", lambda t: (t.program or -1) in range(80, 104) or "synth" in t.name.lower()),
]


def new(args: list[str]) -> None:
    import yaml

    from . import midi
    from .paths import SONGS
    lang = _opt(args, "--lang", "en")
    title = _opt(args, "--title")
    artist = _opt(args, "--artist", "")
    slug, src = args[0], Path(args[1])
    d = SONGS / slug
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, d / src.name)
    tracks = [t for t in midi.read(d / src.name) if t.notes]
    parts, used_kinds = [], set()
    has_lyrics = any(t.lyrics for t in tracks)
    for t in tracks:
        kind = next((k for k, test in GUESS if test(t)), None)
        if kind is None or (kind != "voice" and kind in used_kinds) or len(parts) >= 8:
            continue
        used_kinds.add(kind)
        if kind == "voice":
            n_voices = sum(1 for p in parts if "lyrics" in p)
            parts.append({"name": "Lead" if n_voices == 0 else f"Voice {n_voices + 1}", "track": t.index, "body": ["female", "male", "child"][n_voices % 3],
                          "lyrics": "midi" if t.lyrics else ("TODO: paste the lyrics, one line per sung phrase" if n_voices == 0 else "from: Lead")})
        else:
            parts.append({"name": kind.capitalize(), "track": t.index, "instrument": kind})
    if not any("lyrics" in p for p in parts) and tracks:
        t = max(tracks, key=lambda t: max(n.pitch for n in t.notes) if 9 not in t.channels else 0)
        parts.insert(0, {"name": "Lead", "track": t.index, "body": "female", "lyrics": "midi" if has_lyrics else "TODO: paste the lyrics"})
    spec = {"title": title or slug.replace("-", " ").title(), "artist": artist, "language": lang, "midi": src.name,
            "start": 0, "end": 60, "parts": parts}
    (d / "song.yaml").write_text("# Edit me: check the tracks (python3 -m mech tracks), the lyrics and the time range (start/end in s).\n"
                                 + yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(midi.describe(d / src.name))
    print(f"\nwrote {d / 'song.yaml'} — check it, then: python3 -m mech video {slug}")


def main(argv: list[str]) -> None:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    cmd, args = argv[0], argv[1:]
    if cmd == "tracks":
        from . import midi
        print(midi.describe(args[0]))
    elif cmd == "new":
        new(args)
    elif cmd == "listen":
        from . import listen
        kw = dict(start=_opt(args, "--start"), end=_opt(args, "--end"), title=_opt(args, "--title"), artist=_opt(args, "--artist", ""),
                  language=_opt(args, "--lang", "auto"), arrangement=_opt(args, "--arrangement", "band"), voice=_opt(args, "--voice", "auto"),
                  lyrics_file=_opt(args, "--lyrics"), stems_dir=_opt(args, "--stems"))
        kw["start"] = float(kw["start"]) if kw["start"] else None
        kw["end"] = float(kw["end"]) if kw["end"] else None
        listen.run(args[0] if args else "", args[1], **kw)
    elif cmd == "syllables":
        from . import lang
        for s in lang.syllables(args[1], args[0]):
            print(f"{s['text']:<10} /{''.join(x['ipa'] for x in s['onset'])}{s['nucleus']['ipa']}{''.join(x['ipa'] for x in s['coda'])}/" + ("  ‖" if s["phraseEnd"] else ""))
    elif cmd == "build":
        from . import build, claude
        build.build(args[0], translate=claude.translate_lines)
    elif cmd == "video":
        from . import build, claude
        seconds = _opt(args, "--seconds")
        build.build(args[0], translate=claude.translate_lines)
        extra = ["--seconds", seconds] if seconds else []
        subprocess.run(["bun", "mech/render_video.ts", args[0], *extra], check=True, cwd=Path(__file__).parent.parent)
    elif cmd == "next":
        from . import publish
        s = publish.next_song()
        print(f"{s['slug']}: {s.get('title')} — {s.get('artist')}" if s else "nothing left to post")
    elif cmd == "publish":
        from . import publish
        dry = _flag(args, "--dry-run")
        fps = int(_opt(args, "--fps", 30))
        publish.run(args[0] if args else None, dry=dry, fps=fps)
    elif cmd == "comments":
        from . import comments
        comments.run(dry=_flag(args, "--dry-run"))
    elif cmd == "queue":
        from . import publish
        print(publish.queue_report())
    elif cmd == "refresh-token":
        from . import instagram
        print(instagram.refresh_token())
    else:
        raise SystemExit(f"unknown command {cmd!r}\n{__doc__}")


if __name__ == "__main__":
    main(sys.argv[1:])
