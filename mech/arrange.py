"""Write songs/<slug>/song.yaml from a fetched MIDI (python3 -m mech arrange <slug> --voice male).

Picks the source file with the most words, the channel or track the words are sung on, keeps
every other part in the mix (with its own General MIDI instrument), shows the four that matter
most, and chooses the strongest 59 seconds from a quick render of the whole arrangement."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import mido
import numpy as np
import yaml

from . import karaoke, midi
from .paths import SONGS

COLORS = ["gold", "cyan", "pink", "green", "red", "violet", "blue", "white"]


def kind_of(program: int | None, drums: bool) -> str:
    if drums:
        return "drums"
    p = program or 0
    for lo, hi, k in ((0, 7, "piano"), (8, 15, "bells"), (16, 23, "organ"), (24, 31, "guitar"), (32, 39, "bass"), (40, 55, "strings"),
                      (56, 63, "brass"), (64, 71, "sax"), (72, 79, "flute"), (80, 87, "synth"), (88, 103, "pad"), (104, 127, "synth")):
        if lo <= p <= hi:
            return k
    return "piano"


def _programs_by_channel(path: Path) -> dict[int, int]:
    out: dict[int, int] = {}
    for tr in mido.MidiFile(str(path)).tracks:
        for m in tr:
            if m.type == "program_change" and m.channel not in out:
                out[m.channel] = m.program
    return out


def _render_full(path: Path) -> Path:
    from .instruments import soundfont
    wav = Path(tempfile.mkdtemp()) / "full.wav"
    subprocess.run(["fluidsynth", "-ni", "-q", "-g", "0.6", "-r", "22050", "-F", str(wav), soundfont(), str(path)], check=True, capture_output=True)
    return wav


def arrange(slug: str, title: str, artist: str, voice: str = "male", length: float = 59.0, color: str | None = None) -> Path:
    from .listen import best_window
    d = SONGS / slug
    sources = sorted(d.glob("source*.mid"))
    words = {p: len(karaoke.lyric_events(p)) for p in sources}
    src = max(sources, key=lambda p: words[p])
    ev = karaoke.lyric_events(src)
    mel = karaoke.melody_source(src, ev) if words[src] >= 20 else {}
    mf = mido.MidiFile(str(src))
    progs = _programs_by_channel(src)
    parts: list[dict] = []
    lead = {"name": "Lead", "role": "lead", "body": voice, "color": color or COLORS[sum(map(ord, slug)) % len(COLORS)], "midi": src.name,
            "lyrics": "karaoke" if mel else "TODO", "tone": {"vibrato": 26, "tension": 0.6 if voice == "female" else 0.56, "pressure": 9.8}}
    if "channel" in mel:
        lead["channel"] = mel["channel"]
        mnotes = midi.by_channel(src, mel["channel"])
    elif "track" in mel:
        lead["track"] = mel["track"]
        mnotes = midi.read(src)[mel["track"]].notes
    else:
        raise SystemExit(f"{slug}: no words found in {', '.join(p.name for p in sources)}; it needs a karaoke MIDI or lyrics")
    med = float(np.median([n.pitch for n in mnotes]))
    # the octave that puts the tune where that voice sings it full, not thin: a melody track is
    # often written an octave up (A4 for a male voice strains into falsetto at every climax)
    target = 60 if voice == "male" else 65  # C4 / F4 medians
    lead["octave"] = max(-2, min(2, round((target - med) / 12)))
    parts.append(lead)
    # every other part, by channel (type-0 files) or track
    others = []
    if mf.type == 0 or len(mf.tracks) == 1:
        chans = sorted({m.channel for m in mf.tracks[0] if hasattr(m, "channel")})
        for c in chans:
            if c == mel.get("channel"):
                continue
            ns = midi.by_channel(src, c)
            if ns:
                others.append({"channel": c, "kind": kind_of(progs.get(c), c == 9), "program": progs.get(c), "n": len(ns)})
    else:
        for tr in midi.read(src):
            if tr.index == mel.get("track") or not tr.notes:
                continue
            drums = 9 in tr.channels
            others.append({"track": tr.index, "kind": kind_of(tr.program, drums), "program": tr.program, "n": len(tr.notes)})
    # show the four that matter most: drums, bass, then the busiest of the rest (one per kind)
    def weight(o):
        return {"drums": 3e6, "bass": 2e6}.get(o["kind"], 0) + o["n"]
    shown, kinds = [], set()
    for o in sorted(others, key=weight, reverse=True):
        if len(shown) < 4 and o["kind"] not in kinds:
            shown.append(id(o))
            kinds.add(o["kind"])
    for i, o in enumerate(others):
        p = {"name": o["kind"].capitalize(), "instrument": o["kind"], "midi": src.name, "color": COLORS[(i + 2) % len(COLORS)],
             "gain": 0.8 if o["kind"] in ("drums", "bass") else 0.6}
        if "channel" in o:
            p["channel"] = o["channel"]
        else:
            p["track"] = o["track"]
        if o["program"] is not None and o["kind"] != "drums":
            p["program"] = o["program"]
        if id(o) not in shown:
            p["hidden"] = True
        parts.append(p)
    # the strongest stretch, where the words are
    wav = _render_full(src)
    times = [w["t0"] for ln in karaoke.lines(ev) for w in ln["words"]]
    most = max((sum(1 for x in times if a <= x < a + length) for a in np.arange(0, max(times or [0]) + 1, 2.0)), default=1) or 1
    sung = lambda t0: 0.8 * sum(1 for x in times if t0 <= x < t0 + length) / most  # a clip should be sung through
    start, why = best_window(wav, length, bonus=sung)
    spec = {"title": title, "artist": artist, "language": "en", "midi": src.name, "start": round(start, 2), "end": round(start + length, 2),
            "fade_out": 1.5, "priority": 5, "parts": parts}
    (d / "song.yaml").write_text(f"# Arranged from {src.name} (python3 -m mech arrange). Window {why}.\n"
                                 + yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    print(f"{slug}: {src.name}, lead {mel}, octave {lead['octave']}, {len(others)} other parts ({len(shown)} shown), clip {start:.1f}-{start + length:.1f} s")
    return d / "song.yaml"
