"""A song folder -> the band's audio and a studio show the video renderer plays.

    songs/<slug>/song.yaml   what to play (see docs/mech-melody.md)
    songs/<slug>/*.mid       the arrangement

writes public/shows/<slug>/: one .wav/.tele/.melody.json per singer, mix.wav, studio.json.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from . import instruments, lang, midi
from .paths import CHIMERA, SHOWS, SONGS

FS = 48000
LEAD_IN = 0.8  # s of silence before the song's first note (consonants land before the beat)
PALETTE = {"gold": "#ffc45a", "blue": "#4fb6f2", "pink": "#ff73cf", "green": "#5be381", "red": "#ff5d40", "white": "#eef6f8", "violet": "#a98bff", "cyan": "#5ee6e6"}
ORDER = ["gold", "blue", "pink", "green", "violet", "cyan", "red", "white"]
BODY_CENTRE = {"male": 55, "female": 67, "child": 70}


@dataclass
class Report:
    warnings: list = field(default_factory=list)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print("warning:", msg)


def load(slug: str) -> tuple[dict, Path]:
    d = SONGS / slug
    spec = yaml.safe_load((d / "song.yaml").read_text(encoding="utf-8"))
    spec.setdefault("slug", slug)
    return spec, d


def _part_notes(spec: dict, part: dict, d: Path) -> list:
    path = d / part.get("midi", spec["midi"])
    if "channel" in part:
        notes = midi.by_channel(path, int(part["channel"]))
    else:
        notes = midi.pick(midi.read(path), part["track"]).notes
    shift = int(part.get("transpose", 0))
    start = float(spec.get("start", 0))
    end = float(spec.get("end", 1e9))
    out = []
    for n in notes:
        if n.start < start or n.start >= end:
            continue
        out.append(midi.Note(n.start - start + LEAD_IN, min(n.end, end) - start + LEAD_IN, n.pitch + shift, n.vel))
    return out


def _octave_fit(notes: list, body: str) -> int:
    if not notes:
        return 0
    med = float(np.median([n.pitch for n in notes]))
    return int(round((BODY_CENTRE[body] - med) / 12)) * 12


def _phrases(notes: list, gap: float = 0.3) -> list[list[int]]:
    groups, cur = [], []
    for k, n in enumerate(notes):
        if cur and n.start - notes[cur[-1]].end >= gap:
            groups.append(cur)
            cur = []
        cur.append(k)
    if cur:
        groups.append(cur)
    return groups


def _fit(n: int, syls: list[dict], rep: Report, where: str) -> list[dict]:
    syls = [dict(s) for s in syls]
    if not syls:
        return [{"text": "oo", "onset": [], "nucleus": {"ipa": "u", "stress": 1}, "coda": [], "wordEnd": True, "phraseEnd": False, "line": -1} for _ in range(n)]
    if len(syls) > n:
        rep.warn(f"{where}: {len(syls)} syllables for {n} notes; dropped: {' '.join(s['text'] for s in syls[n:])}")
        syls = syls[:n]
        syls[-1]["phraseEnd"] = True
    while len(syls) < n:  # a long last note in the phrase: hold its vowel over the extra notes
        syls.append(lang.melisma_of(syls[-1]))
    return syls


def assign(notes: list, syls: list[dict], rep: Report, name: str) -> list[dict]:
    """One syllable per note. Lyric lines are matched to the melody's phrases when their counts agree."""
    phrases = _phrases(notes)
    lines: dict[int, list] = {}
    for s in syls:
        lines.setdefault(s.get("line", 0), []).append(s)
    if len(lines) == len(phrases) and len(lines) > 1:
        out = []
        for k, (ph, ln) in enumerate(zip(phrases, lines.values())):
            out.extend(_fit(len(ph), ln, rep, f"{name}, line {k + 1}"))
        return out
    if len(lines) > 1:
        rep.warn(f"{name}: {len(lines)} lyric lines but {len(phrases)} melodic phrases; syllables are laid on the notes in order")
    return _fit(len(notes), syls, rep, name)


def _midi_lyrics(spec: dict, part: dict, d: Path, notes: list, language: str) -> list[dict]:
    """Karaoke (.kar) lyrics: one event per note. Words are rebuilt, read, and spread back on their notes."""
    tr = midi.pick(midi.read(d / part.get("midi", spec["midi"])), part.get("lyrics_track", part.get("track", 0)))
    words, cur = [], ""
    for ev in tr.lyrics:
        t = ev.text
        new_line = t.startswith(("/", "\\"))
        t = t.lstrip("/\\")
        if t.startswith(" ") or new_line:
            if cur.strip():
                words.append(cur.strip())
            cur = ""
            if new_line:
                words.append("\n")
        cur += t.replace("-", "")
        if t.endswith(" "):
            words.append(cur.strip())
            cur = ""
    if cur.strip():
        words.append(cur.strip())
    text = " ".join(words).replace(" \n ", "\n")
    return lang.syllables(text, language)


def _write_wav(path: Path, x: np.ndarray, channels: int = 1) -> None:
    x = np.clip(x, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32767


def _melody(notes: list, syls: list[dict]) -> dict:
    t0 = notes[0].start
    seq, t = [], t0
    for n, nxt in zip(notes, notes[1:] + [None]):
        dur = n.end - n.start
        gap = (nxt.start - n.end) if nxt else 0
        if 0 < gap < 0.05:  # legato: fold a tiny gap into the note
            dur += gap
            gap = 0
        seq.append([n.pitch, round(dur, 4)])
        if gap > 0:
            seq.append([0, round(gap, 4)])
    clean = [{k: v for k, v in s.items() if k != "line"} for s in syls]
    return {"bpm": 60, "t0": round(t0, 4), "vibrato": 30, "notes": seq, "syllables": clean, "lyrics": " ".join(s["text"] for s in syls)}


def _pan(x: np.ndarray, p: float) -> np.ndarray:
    a = (p + 1) * np.pi / 4
    return np.stack([x * np.cos(a), x * np.sin(a)], axis=1)


def build(slug: str, translate=None) -> dict:
    """Render every part and write the show. `translate(lines, language)` gives English subtitle lines."""
    spec, d = load(slug)
    rep = Report()
    language = spec.get("language", "en")
    out = SHOWS / slug
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    parts = spec["parts"]
    lyrics_by_name: dict[str, str] = {p["name"]: p["lyrics"] for p in parts if isinstance(p.get("lyrics"), str) and p["lyrics"] not in ("midi",)}
    stems, show_parts, lead_timing = [], [], None
    for k, p in enumerate(parts):
        color = p.get("color", ORDER[k % len(ORDER)])
        entry = {"name": p["name"], "color": PALETTE.get(color, color), "body": p.get("body", "male")}
        notes = _part_notes(spec, p, d)
        if not notes:
            rep.warn(f"{p['name']}: no notes in the chosen track/time range")
            continue
        if "instrument" in p:
            kind = p["instrument"]
            audio = instruments.render(kind, notes, max(n.end for n in notes) + 2, seed=k + 1) * float(p.get("gain", 1.0))
            entry.update(kind="instrument", instrument=kind, notes=[[round(n.start, 3), round(n.end, 3), n.pitch, n.vel] for n in notes])
            stems.append((audio, float(p.get("pan", 0.0))))
            show_parts.append(entry)
            continue
        body = entry["body"]
        notes = midi.monophonic(notes)
        if p.get("octave", "auto") == "auto":
            sh = _octave_fit(notes, body)
            notes = [midi.Note(n.start, n.end, n.pitch + sh, n.vel) for n in notes]
        else:
            notes = [midi.Note(n.start, n.end, n.pitch + 12 * int(p["octave"]), n.vel) for n in notes]
        lyr = p.get("lyrics", "")
        if lyr == "midi":
            syls = _midi_lyrics(spec, p, d, notes, language)
        elif isinstance(lyr, str) and lyr.startswith("from:"):
            syls = lang.syllables(lyrics_by_name[lyr[5:].strip()], language)
        elif isinstance(lyr, str) and lyr.startswith("vocalise:"):
            syls = lang.syllables(" ".join([lyr[9:].strip()] * len(notes)), "en")
        else:
            syls = lang.syllables(lyr, language)
        syls = assign(notes, syls, rep, p["name"])
        mel = _melody(notes, syls)
        stem = f"v{k}"
        (out / f"{stem}.melody.json").write_text(json.dumps(mel, ensure_ascii=False))
        r = subprocess.run([str(CHIMERA), "render", "notes", str(out / f"{stem}.melody.json"), str(out / stem), body, "--tele", str(out / f"{stem}.tele")], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"{p['name']}: the engine refused the melody: {r.stderr or r.stdout}")
        audio = _read_wav(out / f"{stem}.wav") * float(p.get("gain", 1.0))
        stems.append((audio, float(p.get("pan", 0.0))))
        entry.update(kind="voice", tele=f"{stem}.tele", melody=f"{stem}.melody.json", role=p.get("role", "lead" if lead_timing is None else "backing"))
        show_parts.append(entry)
        if lead_timing is None:
            lead_timing = (notes, syls)

    # the mix: every stem on sample 0, panned, levelled, a gentle limiter
    n = max(len(a) for a, _ in stems)
    mix = np.zeros((n, 2), np.float32)
    for a, pan in stems:
        a = a / max(1e-6, float(np.sqrt(np.mean(a[a != 0] ** 2))) if np.any(a) else 1) * 0.12
        mix[: len(a)] += _pan(a, pan)
    mix /= max(1.0, float(np.abs(mix).max()) / 0.95)
    mix = np.tanh(mix * 1.3) / np.tanh(1.3)
    _write_wav(out / "mix.wav", mix.reshape(-1), channels=2)
    duration = round(n / FS + 0.6, 3)

    # subtitles: one per lyric line of the lead, timed by the notes it is sung on
    subs = []
    if lead_timing:
        notes, syls = lead_timing
        lines = lang.lyric_lines(next((p["lyrics"] for p in parts if isinstance(p.get("lyrics"), str) and p["lyrics"] not in ("midi",) and not p["lyrics"].startswith(("from:", "vocalise:"))), ""))
        spans: dict[int, list[float]] = {}
        for nt, s in zip(notes, syls):
            li = s.get("line", -1)
            if li < 0:
                continue
            sp = spans.setdefault(li, [nt.start, nt.end])
            sp[1] = nt.end
        english = spec.get("subtitles")
        cache = d / "subtitles.en.json"
        if not english and cache.exists():
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if cached.get("lines") == lines:
                english = cached["english"]
        if not english and lines:
            english = lines if language == "en" else (translate(lines, language, spec) if translate else lines)
            if language != "en" and english is not lines:
                cache.write_text(json.dumps({"lines": lines, "english": english}, ensure_ascii=False, indent=1), encoding="utf-8")
        for li, (a, b) in sorted(spans.items()):
            if li < len(lines):
                subs.append({"start": round(a - 0.15, 3), "end": round(b + 0.35, 3), "text": (english[li] if english and li < len(english) else lines[li]), "orig": lines[li]})
        for x, y in zip(subs, subs[1:]):  # never two on screen at once
            x["end"] = min(x["end"], y["start"])
    show = {
        "title": spec["title"],
        "artist": spec.get("artist", ""),
        "language": language,
        "bpm": spec.get("bpm") or midi.first_bpm(midi.mido.MidiFile(str(d / spec["midi"]))),
        "downbeat": LEAD_IN,
        "duration": duration,
        "audio": "mix.wav",
        "parts": show_parts,
        "subtitles": subs,
        "warnings": rep.warnings,
    }
    (out / "studio.json").write_text(json.dumps(show, ensure_ascii=False, indent=1))
    print(f"{slug}: {len(show_parts)} parts, {duration:.1f} s -> {out}")
    return show
