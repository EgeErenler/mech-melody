"""A song folder -> the band's audio and a studio show the video renderer plays.

    songs/<slug>/song.yaml   what to play (see docs/mech-melody.md)
    songs/<slug>/*.mid       the arrangement

writes public/shows/<slug>/: one .wav/.tele/.melody.json per singer, mix.wav, studio.json.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml
from scipy.signal import butter, fftconvolve, lfilter, sosfilt

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


def _part_notes_full(spec: dict, part: dict, d: Path) -> list:
    """All of a part's notes on the clip's clock (before the window is applied)."""
    path = d / part.get("midi", spec["midi"])
    notes = midi.by_channel(path, int(part["channel"])) if "channel" in part else midi.pick(midi.read(path), part["track"]).notes
    shift = int(part.get("transpose", 0))
    start = float(spec.get("start", 0))
    return [midi.Note(n.start - start + LEAD_IN, n.end - start + LEAD_IN, n.pitch + shift, n.vel) for n in notes]


def _karaoke(spec: dict, part: dict, d: Path, language: str) -> tuple[list, list, list]:
    """The lead from a karaoke MIDI: words laid on the notes by their times over the whole song,
    then cut to the clip. Returns (notes, syllables, lyric lines)."""
    from . import karaoke, listen
    path = d / part.get("lyrics_midi", part.get("midi", spec["midi"]))
    start = float(spec.get("start", 0))
    end = float(spec.get("end", 1e9))
    ls = karaoke.lines(karaoke.lyric_events(path))
    for ln in ls:
        for w in ln["words"]:
            w["t0"] += LEAD_IN - start
            w["t1"] += LEAD_IN - start
    full = midi.monophonic(_part_notes_full(spec, part, d))
    notes, syls = listen.align(ls, [listen.N(n.start, n.end, n.pitch, n.vel) for n in full], language)
    lo, hi = LEAD_IN, LEAD_IN + (end - start)
    keep = [i for i, n in enumerate(notes) if lo <= n.start < hi]
    notes = [midi.Note(notes[i].start, min(notes[i].end, hi), notes[i].pitch, notes[i].vel) for i in keep]
    syls = [dict(syls[i]) for i in keep]
    if syls:
        if syls[0].get("melisma"):  # the clip starts inside a held note: sing it as a syllable
            syls[0].pop("melisma")
        syls[-1]["phraseEnd"] = True
    return notes, syls, [ln["line"] for ln in ls]


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


BEAT_OF = {36: "B", 35: "B", 38: "Pf", 40: "Pf", 37: "K", 39: "K", 42: "t", 44: "t", 46: "tss", 49: "kch", 57: "kch", 51: "t", 52: "kch", 55: "kch", 59: "t"}
BEAT_RANK = {"B": 0, "Pf": 1, "K": 1, "kch": 2, "tss": 3, "t": 4}


def _thin(hits: list[dict], gap: float = 0.07) -> list[dict]:
    """One mouth can't make two sounds at once: of hits closer than `gap`, keep the most important."""
    out: list[dict] = []
    for h in sorted(hits, key=lambda h: h["t"]):
        if out and h["t"] - out[-1]["t"] < gap:
            if BEAT_RANK.get(h["id"], 5) < BEAT_RANK.get(out[-1]["id"], 5):
                out[-1] = h
            continue
        out.append(h)
    return out


def _echo(notes: list, lead_notes: list, lead_syls: list[dict]) -> list[dict]:
    """A backing singer's words: the lead's syllable when they start together, else "oo"."""
    oo = lang.syllables("oo", "en")[0]
    out = []
    for n in notes:
        hit = next((s for m, s in zip(lead_notes, lead_syls) if abs(m.start - n.start) < 0.1 and not s.get("melisma")), None)
        s = dict(hit) if hit else dict(oo)
        s.pop("melisma", None)
        s["wordEnd"] = True
        s["phraseEnd"] = False
        s["line"] = -1
        out.append(s)
    return out


def _engine(args: list[str], name: str) -> None:
    r = subprocess.run([str(CHIMERA), *args, "--float"], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"{name}: the engine refused it: {r.stderr or r.stdout}")


def _write_wav(path: Path, x: np.ndarray, channels: int = 1) -> None:
    """32-bit float WAV (no rounding to 16 bits until the final AAC)."""
    import soundfile
    x = np.asarray(x, np.float32)
    if channels == 2 and x.ndim == 1:
        x = x.reshape(-1, 2)
    soundfile.write(str(path), x, FS, subtype="FLOAT")


def _read_wav(path: Path) -> np.ndarray:
    import soundfile
    y, _ = soundfile.read(str(path), dtype="float32")
    return y


MIN_SYLLABLE = 0.14  # s: shorter than this, a syllable's vowel never sounds and the word is lost


def _room_for_syllables(notes: list, syls: list[dict]) -> tuple[list, list[dict]]:
    """Give every sung syllable time to sound. A melisma note under MIN_SYLLABLE joins the note
    before it (a quick ornament the voice can't articulate anyway); a syllable on a note under
    MIN_SYLLABLE starts earlier, taking the time from the rest or the long note before it.
    Rolling in the Deep had 14 such notes, sung 8.5 dB quieter than the rest (some 40 dB down),
    which is where the words disappeared."""
    import copy
    ns, ss = [copy.copy(n) for n in notes], [dict(x) for x in syls]
    i = 1
    while i < len(ns):
        n, prev = ns[i], ns[i - 1]
        if ss[i].get("melisma") and n.end - n.start < MIN_SYLLABLE and n.start - prev.end < 0.05:
            prev.end = n.end
            for k in ("wordEnd", "phraseEnd"):  # the joined note may have closed the word
                if ss[i].get(k):
                    ss[i - 1][k] = True
            del ns[i], ss[i]
            continue
        need = MIN_SYLLABLE - (n.end - n.start)
        if need > 0:
            free = n.start - prev.end  # a rest before it
            if free < need:  # and the note before, down to MIN_SYLLABLE of its own
                take = min(need - max(free, 0), (prev.end - prev.start) - MIN_SYLLABLE)
                if take > 0:
                    prev.end -= take
            n.start = max(prev.end, n.start - need)
        i += 1
    return ns, ss


def _melody(notes: list, syls: list[dict]) -> dict:
    notes, syls = _room_for_syllables(notes, syls)
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


def _presence(x: np.ndarray) -> np.ndarray:
    """The lead's voice forward: a gentle lift around 3 kHz, the rumble below 90 Hz cut."""
    hp = sosfilt(butter(2, 90 / (FS / 2), btype="high", output="sos"), x, axis=0)
    band = sosfilt(butter(2, [2000 / (FS / 2), 5000 / (FS / 2)], btype="band", output="sos"), hp, axis=0)
    return (hp + 0.35 * band).astype(np.float32)


def _ride(x: np.ndarray) -> np.ndarray:
    """A vocal leveler: lift quiet syllables (close vowels like i, e and quick notes came out up
    to 14 dB below the open ones) toward the singer's typical level, up to +9 dB, and pull the
    loudest back up to 3 dB. Gain follows a 30 ms level, easing in 15 ms and out 120 ms; silence
    (below -40 dB from the typical level) is left alone so breaths and rests stay quiet."""
    mono = np.abs(x).max(axis=1) if x.ndim == 2 else np.abs(x)
    hop = int(0.01 * FS)
    n = len(mono) // hop
    lvl = np.sqrt(np.convolve((mono[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(axis=1), np.ones(3) / 3, "same")) + 1e-9
    voiced = lvl > 1e-5
    if not voiced.any():
        return x
    typ = np.percentile(lvl[voiced], 75)
    want = np.where(lvl > typ * 10 ** (-40 / 20), np.clip((typ / lvl) ** 0.7, 10 ** (-3 / 20), 10 ** (9 / 20)), 1.0)
    out, g = np.empty(n), 1.0
    for i, w in enumerate(want):
        k = 1 - np.exp(-1 / (1.5 if w < g else 12))
        g += (w - g) * k
        out[i] = g
    gain = np.interp(np.arange(len(mono)), np.arange(n) * hop + hop / 2, out).astype(np.float32)
    return x * (gain[:, None] if x.ndim == 2 else gain)


def _make_room_for(band: np.ndarray, lead: np.ndarray) -> np.ndarray:
    """The band steps aside for the words: a 3 dB dip around 2.5 kHz (where consonants are heard)
    and up to 4 dB less level while the lead sings, eased in 30 ms and out 250 ms. At the loud
    choruses the band otherwise covered the lead and the words got lost."""
    b, a = _peaking(2500.0, -3.0, 0.9)
    band = lfilter(b, a, band, axis=0).astype(np.float32)
    env = np.abs(lead).max(axis=1)
    hop = int(0.01 * FS)
    frames = np.sqrt(np.add.reduceat(env.astype(np.float64) ** 2, np.arange(0, len(env), hop)) / hop)
    on = frames > 0.25 * (np.percentile(frames[frames > 1e-5], 90) if np.any(frames > 1e-5) else 1.0)
    duck = np.where(on, 10 ** (-4 / 20), 1.0)
    out = np.empty_like(duck)
    g = 1.0
    for i, d in enumerate(duck):  # fast attack, slow release
        k = 1 - np.exp(-1 / (3 if d < g else 25))
        g += (d - g) * k
        out[i] = g
    gain = np.repeat(out, hop)[: len(band)].astype(np.float32)
    return band * gain[:, None]


def _peaking(f0: float, gain_db: float, q: float):
    """RBJ peaking EQ coefficients."""
    A = 10 ** (gain_db / 40)
    w = 2 * np.pi * f0 / FS
    al = np.sin(w) / (2 * q)
    b = np.array([1 + al * A, -2 * np.cos(w), 1 - al * A])
    a = np.array([1 + al / A, -2 * np.cos(w), 1 - al / A])
    return b / a[0], a / a[0]


def _master(x: np.ndarray) -> np.ndarray:
    """Bus glue (a slow 2:1 compressor), a little air above 10 kHz, then a peak limiter at -1 dBFS.
    Loudness (-14 LUFS for Instagram) is set when the video is encoded."""
    x = sosfilt(butter(2, 30 / (FS / 2), btype="high", output="sos"), x, axis=0)
    air = sosfilt(butter(1, 10000 / (FS / 2), btype="high", output="sos"), x, axis=0)
    x = x + 0.25 * air
    # compressor on the RMS envelope (both channels linked)
    env = np.sqrt(np.convolve(np.mean(x ** 2, axis=1), np.ones(int(0.05 * FS)) / int(0.05 * FS), mode="same") + 1e-12)
    db = 20 * np.log10(env)
    thr = float(np.percentile(db[db > -60], 75)) if np.any(db > -60) else -20.0
    gain_db = np.where(db > thr, (thr - db) * 0.5, 0.0)
    a = np.exp(-1 / (0.15 * FS))
    from scipy.signal import lfilter
    gain_db = lfilter([1 - a], [1, -a], gain_db)
    x = x * (10 ** (gain_db / 20))[:, None]
    # limiter: the gain each sample needs to stay under the ceiling, held and smoothed
    ceiling = 10 ** (-1 / 20)
    x = x / max(1e-9, float(np.percentile(np.abs(x), 99.95))) * ceiling * 0.9
    need = np.minimum(1.0, ceiling / np.maximum(np.abs(x).max(axis=1), 1e-9))
    from scipy.ndimage import minimum_filter1d
    need = minimum_filter1d(need, int(0.005 * FS))
    need = np.convolve(need, np.ones(int(0.004 * FS)) / int(0.004 * FS), mode="same")
    return (x * need[:, None]).astype(np.float32)


def _room(x: np.ndarray, seconds: float = 1.6) -> np.ndarray:
    """A plain stereo room: each side convolved with its own decaying noise (no samples needed)."""
    rng = np.random.default_rng(7)
    t = np.arange(int(seconds * FS)) / FS
    out = np.zeros_like(x)
    for c in range(2):
        ir = rng.standard_normal(len(t)) * np.exp(-t * 6.9 / seconds)
        ir[: int(0.012 * FS)] = 0  # a short gap before the reflections
        ir = sosfilt(butter(1, 5000 / (FS / 2), output="sos"), ir)
        ir /= np.sqrt(np.sum(ir ** 2))
        out[:, c] = fftconvolve(x[:, c], ir)[: len(x)]
    return out.astype(np.float32)


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
    # the lead goes first: backing singers echo its words
    parts = sorted(parts, key=lambda p: 0 if p.get("role") == "lead" else 1)
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
            length = max(n.end for n in notes) + 2
            audio = None if p.get("synth") else instruments.render_sampled(kind, notes, length, program=p.get("program"))
            if audio is None:
                audio = instruments.render(kind if kind in instruments.KINDS else "piano", notes, length, seed=k + 1)
            audio = audio * float(p.get("gain", 1.0))
            _write_wav(out / f"i{k}.wav", audio)
            group = "inst"
            entry.update(kind="instrument", instrument=kind, notes=[[round(n.start, 3), round(n.end, 3), n.pitch, n.vel] for n in notes])
            stems.append((audio, float(p.get("pan", 0.0)), group))
            if not p.get("hidden"):  # heard in the mix, not shown
                show_parts.append(entry)
            continue
        body = entry["body"]
        if p.get("beatbox"):
            hits = [{"t": round(n.start, 4), "id": BEAT_OF.get(n.pitch, "Pf"), "accent": round(0.55 + 0.45 * n.vel / 127, 3), "len": 0.4 if BEAT_OF.get(n.pitch) in ("kch", "tss") else 0.15} for n in notes]
            hits = _thin(hits)
            stem = f"v{k}"
            (out / f"{stem}.hits.json").write_text(json.dumps({"hits": hits}))
            _engine(["render", "hits", str(out / f"{stem}.hits.json"), str(out / stem), body, "--tele", str(out / f"{stem}.tele")], p["name"])
            audio = _read_wav(out / f"{stem}.wav") * float(p.get("gain", 1.0))
            stems.append((audio, float(p.get("pan", 0.0)), "voices"))
            entry.update(kind="voice", tele=f"{stem}.tele", role="beatbox", hits=[[h["t"], h["id"]] for h in hits])
            show_parts.append(entry)
            continue
        if p.get("lyrics") == "karaoke":
            notes, syls, p["lines"] = _karaoke(spec, p, d, language)
            p["syllables_ready"] = True
        elif p.get("syllables"):
            syls = json.loads((d / p["syllables"]).read_text(encoding="utf-8"))
            notes = sorted(notes, key=lambda n: n.start)
            if len(syls) != len(notes):
                rep.warn(f"{p['name']}: {len(syls)} syllables for {len(notes)} notes in the arrangement; the extra are dropped")
                m = min(len(syls), len(notes))
                syls, notes = syls[:m], notes[:m]
        elif not p.get("syllables_ready"):
            notes = midi.monophonic(notes)
        if p.get("octave", "auto") == "auto":
            sh = _octave_fit(notes, body)
            notes = [midi.Note(n.start, n.end, n.pitch + sh, n.vel) for n in notes]
        else:
            notes = [midi.Note(n.start, n.end, n.pitch + 12 * int(p["octave"]), n.vel) for n in notes]
        lyr = p.get("lyrics", "")
        if p.get("syllables") or p.get("syllables_ready"):
            pass
        elif lyr == "midi":
            syls = _midi_lyrics(spec, p, d, notes, language)
        elif isinstance(lyr, str) and lyr.startswith("echo:") and lead_timing:
            syls = _echo(notes, *lead_timing)
        elif isinstance(lyr, str) and lyr.startswith("from:"):
            syls = lang.syllables(lyrics_by_name[lyr[5:].strip()], language)
        elif isinstance(lyr, str) and lyr.startswith("vocalise:"):
            syls = lang.syllables(" ".join([lyr[9:].strip()] * len(notes)), "en")
        else:
            syls = lang.syllables(lyr, language)
        if not (p.get("syllables") or p.get("syllables_ready")):
            syls = assign(notes, syls, rep, p["name"])
        spell = {k.lower(): v for k, v in (spec.get("spell") or {}).items()}
        if spell:  # words spelled for the pronouncing dictionary, shown as they are written
            syls = [dict(x, text=spell.get(x["text"].lower(), x["text"])) for x in syls]
        mel = _melody(notes, syls)
        mel.update({k2: v for k2, v in (p.get("tone") or {}).items() if k2 in ("vibrato", "tension", "pressure")})
        stem = f"v{k}"
        (out / f"{stem}.melody.json").write_text(json.dumps(mel, ensure_ascii=False))
        _engine(["render", "notes", str(out / f"{stem}.melody.json"), str(out / stem), body, "--tele", str(out / f"{stem}.tele")], p["name"])
        is_lead = p.get("role", "lead" if lead_timing is None else "backing") == "lead"
        audio = _read_wav(out / f"{stem}.wav") * float(p.get("gain", 1.0))
        stems.append((audio, 0.0 if is_lead else float(p.get("pan", 0.0)), "lead" if is_lead else "voices"))
        entry.update(kind="voice", tele=f"{stem}.tele", melody=f"{stem}.melody.json", role=p.get("role", "lead" if lead_timing is None else "backing"))
        show_parts.append(entry)
        if lead_timing is None:
            lead_timing = (notes, syls)

    # the mix: the lead in front, the band a step behind, the other voices further back (quieter,
    # darker, more room); every stem on sample 0
    n = max(len(a) for a, _, _ in stems)
    groups = {g: np.zeros((n, 2), np.float32) for g in ("lead", "voices", "inst")}
    for a, pan, g in stems:
        act = a[np.abs(a) > 1e-4] if a.ndim == 1 else a[np.abs(a).max(axis=1) > 1e-4]
        a = a / max(1e-6, float(np.sqrt(np.mean(act ** 2))) if len(act) else 1.0)
        if a.ndim == 2:  # a stereo instrument: keep its own image, pan as balance
            bal = np.array([min(1.0, 1 - pan), min(1.0, 1 + pan)], np.float32)
            groups[g][: len(a)] += a[:, :2] * bal
        else:
            groups[g][: len(a)] += _pan(a, pan)
    level = {"lead": 0.2, "inst": 0.2 * 10 ** (-6 / 20), "voices": 0.2 * 10 ** (-11 / 20)}
    wet = {"lead": 0.12, "inst": 0.08, "voices": 0.38}
    mix = np.zeros((n, 2), np.float32)
    for g, x in groups.items():
        act = x[np.abs(x).max(axis=1) > 1e-4]
        if not len(act):
            continue
        x = x * (level[g] / float(np.sqrt(np.mean(act ** 2))))
        if g == "voices":
            x = sosfilt(butter(2, 6000 / (FS / 2), output="sos"), x, axis=0).astype(np.float32)
        if g == "lead":
            x = _presence(_ride(x))
        if g == "inst":
            x = _make_room_for(x, groups["lead"])
        mix += (1 - wet[g] * 0.5) * x + wet[g] * _room(x)
    mix = _master(mix)
    # a clip cut from the middle of a song: a quick fade in, a gentle fade out at the end
    if spec.get("fade_out"):
        end_s = min(n, int((float(spec.get("end", n / FS)) - float(spec.get("start", 0)) + LEAD_IN) * FS))
        fo = int(float(spec["fade_out"]) * FS)
        mix[max(0, end_s - fo) : end_s] *= np.linspace(1, 0, min(fo, end_s))[:, None]
        mix[end_s:] = 0
        fi = int(0.25 * FS)
        mix[int(LEAD_IN * FS) : int(LEAD_IN * FS) + fi] *= np.linspace(0, 1, fi)[:, None]
    _write_wav(out / "mix.wav", mix.reshape(-1), channels=2)
    duration = round(n / FS + 0.6, 3)

    # subtitles: one per lyric line of the lead, timed by the notes it is sung on
    subs = []
    if lead_timing:
        notes, syls = lead_timing
        lead_part = next((p for p in parts if p.get("lines")), None)
        lines = lead_part["lines"] if lead_part else lang.lyric_lines(next((p["lyrics"] for p in parts if isinstance(p.get("lyrics"), str) and p["lyrics"] not in ("midi",) and not p["lyrics"].startswith(("from:", "vocalise:", "echo:"))), ""))
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
                respell = {k.lower(): v for k, v in (spec.get("spell") or {}).items()}
                show_text = lambda x: " ".join(respell.get(w.lower().strip(",.!?;:'"), None) and re.sub(r"[A-Za-z']+", respell[w.lower().strip(",.!?;:'")], w) or w for w in re.sub(r"[|_]", "", x).split())
                subs.append({"start": round(a - 0.15, 3), "end": round(b + 0.35, 3), "text": show_text(english[li] if english and li < len(english) else lines[li]), "orig": show_text(lines[li])})
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
