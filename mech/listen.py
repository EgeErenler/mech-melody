"""Listen to a recording and write the robots' arrangement of it.

    python3 -m mech listen <audio> <slug> [--start S --end S] [--title T --artist A]
        [--lang auto|tr|en|es|fr] [--arrangement band|acapella] [--voice auto|male|female|child]
        [--lyrics lyrics.txt] [--stems DIR]

1. Separate the mix into stems: vocals, drums, bass, guitar, piano, other (Demucs htdemucs_6s).
2. Tempo and beats (librosa).
3. The lead vocal's melody from its pitch track (pYIN): notes, the singer's range (which robot
   voice: male / female / child), vibrato depth and brightness (fold tension).
4. The words with their times (faster-whisper), split into syllables and laid on the notes they
   are sung on.
5. Backing vocals: the vocal stem's other notes (Basic Pitch), grouped into harmony voices.
6. Instruments: notes of each stem (Basic Pitch); drum hits sorted into kick / snare / hat / crash.
7. Acapella: the bass becomes a "dum dum" bass singer, the chords a three-part "oo" choir, the
   drums a beatboxer.

Writes songs/<slug>/arrangement.mid (open it in any DAW), lead.syllables.json and song.yaml.
Heavy models load only when needed; without Demucs, `--stems DIR` takes stems you already have
(vocals.wav, drums.wav, bass.wav, guitar.wav, piano.wav, other.wav).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import mido
import numpy as np
import yaml

from . import lang
from .paths import ROOT, SONGS

SR = 22050
WORK = ROOT / "work"
STEMS = ("vocals", "drums", "bass", "guitar", "piano", "other")


@dataclass
class N:
    start: float
    end: float
    pitch: int
    vel: int = 90


def log(msg: str) -> None:
    print(f"[listen] {msg}", flush=True)


# ------------------------------------------------------------------ audio in, stems out

def clip(audio: Path, out: Path, start: float | None, end: float | None) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    dst = out / "clip.wav"
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(audio)]
    if end:
        cmd += ["-t", str(end - (start or 0))]
    cmd += ["-ac", "2", "-ar", "44100", str(dst)]
    subprocess.run(cmd, check=True)
    return dst


def separate(mix: Path, out: Path) -> dict[str, Path]:
    log("separating stems (Demucs htdemucs_6s)…")
    subprocess.run(["python3", "-m", "demucs", "-n", "htdemucs_6s", "-o", str(out / "demucs"), str(mix)], check=True)
    d = out / "demucs" / "htdemucs_6s" / mix.stem
    return {s: d / f"{s}.wav" for s in STEMS if (d / f"{s}.wav").exists()}


def load(path: Path, sr: int = SR) -> np.ndarray:
    import librosa
    y, _ = librosa.load(str(path), sr=sr, mono=True)
    return y


def loudness(y: np.ndarray) -> float:
    return float(10 * np.log10(np.mean(y ** 2) + 1e-12))


# ------------------------------------------------------------------ the best stretch of the song

def best_window(path: Path, length: float, skip_intro: float = 8.0) -> tuple[float, dict]:
    """Where the strongest `length` seconds start: lots of what the song repeats (the chorus),
    loud and busy, starting on a section change at a bar line, and not the very beginning."""
    import librosa
    y, _ = librosa.load(str(path), sr=SR, mono=True)
    dur = len(y) / SR
    if dur <= length + 1:
        return 0.0, {"reason": "the song is shorter than the clip"}
    hop = 512
    _, beats = librosa.beat.beat_track(y=y, sr=SR, hop_length=hop)
    if len(beats) < 32:
        return max(0.0, min(dur - length, dur * 0.3)), {"reason": "no steady beat"}
    chroma = librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=hop)
    mfcc = librosa.feature.mfcc(y=y, sr=SR, hop_length=hop, n_mfcc=13)
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    feat = np.vstack([librosa.util.normalize(chroma, axis=0), 0.5 * librosa.util.normalize(mfcc[1:], axis=1)])
    bs = librosa.util.sync(feat, beats, aggregate=np.median)
    br = librosa.util.sync(rms[None, :], beats, aggregate=np.mean)[0]
    bt = librosa.frames_to_time(np.r_[beats, beats[-1] + 1][: bs.shape[1]], sr=SR, hop_length=hop)
    n = bs.shape[1]
    # repetition: how strongly each beat recurs elsewhere (not just next door), over 8-beat stacks
    stack = librosa.feature.stack_memory(bs, n_steps=8, delay=1)
    R = librosa.segment.recurrence_matrix(stack, mode="affinity", k=max(3, n // 12), width=16, sym=True)
    rep = np.asarray(R.sum(axis=1)).ravel()
    norm = lambda x: (x - x.min()) / (x.max() - x.min() + 1e-9)
    score = 0.6 * norm(rep) + 0.4 * norm(br)
    # section changes: a checkerboard novelty on the self-similarity
    S = np.asarray(librosa.segment.recurrence_matrix(bs, mode="affinity", sym=True, width=1, k=max(3, n // 4)))
    w = 8
    kern = np.kron(np.array([[1, -1], [-1, 1]]), np.ones((w, w)))
    nov = np.zeros(n)
    for i in range(w, n - w):
        nov[i] = max(0.0, float(np.sum(S[i - w : i + w, i - w : i + w] * kern)))
    nov = norm(nov)
    # bar lines: the beat phase (of 4) carrying the most energy
    phase = int(np.argmax([br[p::4].sum() for p in range(4)]))
    best, best_s, why = -1e9, 0.0, {}
    for i in range(phase, n, 4):
        t0 = float(bt[i])
        if t0 < skip_intro or t0 + length > dur - 0.5:
            continue
        j = int(np.searchsorted(bt, t0 + length))
        sc = float(score[i:j].mean()) + 0.35 * float(nov[max(0, i - 1) : i + 2].max())
        # the chorus should come early in the clip, not in its last seconds
        k = int(np.searchsorted(bt, t0 + length * 0.4))
        sc += 0.15 * float(score[i:k].mean() - score[k:j].mean())
        if sc > best:
            best, best_s, why = sc, t0, {"score": round(sc, 3), "repetition": round(float(norm(rep)[i:j].mean()), 3), "loudness": round(float(norm(br)[i:j].mean()), 3)}
    if best < -1e8:
        best_s = max(0.0, min(dur - length, dur * 0.3))
    return round(best_s, 2), why


# ------------------------------------------------------------------ tempo

def tempo(y: np.ndarray) -> tuple[float, float]:
    import librosa
    bpm, beats = librosa.beat.beat_track(y=y, sr=SR, units="time")
    bpm = float(np.atleast_1d(bpm)[0])
    return round(bpm, 1), float(beats[0]) if len(beats) else 0.0


# ------------------------------------------------------------------ the lead melody

def lead_melody(y: np.ndarray) -> tuple[list[N], dict]:
    import librosa
    from scipy.ndimage import median_filter
    hop = 256
    f0, voiced, prob = librosa.pyin(y, fmin=65, fmax=1100, sr=SR, frame_length=2048, hop_length=hop)
    t = librosa.times_like(f0, sr=SR, hop_length=hop)
    m = 69 + 12 * np.log2(np.where(voiced & (prob > 0.25), f0, np.nan) / 440)
    ms = median_filter(np.nan_to_num(m, nan=0), size=5)
    ms[np.isnan(m)] = np.nan
    onsets = set(np.round(librosa.onset.onset_detect(y=y, sr=SR, hop_length=hop, units="frames", backtrack=True)).astype(int))
    notes: list[N] = []
    seg: list[int] = []

    def close():
        if len(seg) * hop / SR >= 0.07:
            vals = ms[seg]
            notes.append(N(float(t[seg[0]]), float(t[seg[-1]] + hop / SR), int(round(float(np.median(vals))))))
        seg.clear()

    run_away = 0
    for i in range(len(ms)):
        if np.isnan(ms[i]):
            close()
            continue
        if seg:
            ref = float(np.median(ms[seg[-6:]]))
            run_away = run_away + 1 if abs(ms[i] - ref) > 0.8 else 0
            long_enough = (i - seg[0]) * hop / SR > 0.09
            if run_away >= 4 or (i in onsets and long_enough):
                back = seg[-run_away:] if run_away >= 4 else []
                if back:
                    del seg[-run_away:]
                close()
                seg.extend(back)
                run_away = 0
        seg.append(i)
    close()
    # tidy: a short slide into a note joins that note; touching notes of one pitch are one note
    # (syllable boundaries inside a held pitch come back from the word timings in align())
    tidy: list[N] = []
    for k, n in enumerate(notes):
        nxt = notes[k + 1] if k + 1 < len(notes) else None
        if nxt and n.end - n.start < 0.1 and nxt.start - n.end < 0.03 and abs(nxt.pitch - n.pitch) <= 2 and nxt.end - nxt.start > n.end - n.start:
            nxt.start = n.start
            continue
        tidy.append(n)
    merged: list[N] = []
    for n in tidy:
        if merged and n.pitch == merged[-1].pitch and n.start - merged[-1].end < 0.05:
            merged[-1].end = n.end
        else:
            merged.append(n)
    # the singer: range, vibrato, brightness
    good = m[~np.isnan(m)]
    stats = {"median": float(np.median(good)) if len(good) else 60.0, "low": float(np.percentile(good, 5)) if len(good) else 50, "high": float(np.percentile(good, 95)) if len(good) else 70}
    vib = []
    for n in merged:
        if n.end - n.start > 0.45:
            a, b = np.searchsorted(t, n.start + 0.15), np.searchsorted(t, n.end)
            x = m[a:b]
            x = x[~np.isnan(x)]
            if len(x) > 10:
                trend = np.polyval(np.polyfit(np.arange(len(x)), x, 2), np.arange(len(x)))
                vib.append(float(np.std(x - trend) * 100 * 1.41))
    stats["vibrato"] = float(np.clip(np.median(vib), 10, 80)) if vib else 30.0
    cent = librosa.feature.spectral_centroid(y=y, sr=SR, hop_length=hop)[0]
    cv = cent[: len(voiced)][voiced[: len(cent)]] if len(cent) else cent
    stats["centroid"] = float(np.median(cv)) if len(cv) else 2000.0
    return merged, stats


def voice_for(stats: dict) -> str:
    """Which robot sings it: by where the melody sits (a woman's melody mostly above D4)."""
    med = stats["median"]
    if med >= 78:
        return "child"
    return "female" if med >= 62 else "male"


def tone_for(stats: dict) -> dict:
    """Brighter singers get more fold tension; a big vibrato stays big."""
    c = stats["centroid"]
    tension = float(np.clip(0.45 + (c - 1500) / 2000 * 0.25, 0.42, 0.72))
    return {"vibrato": round(stats["vibrato"], 1), "tension": round(tension, 3), "pressure": 9.5}


# ------------------------------------------------------------------ the words

def transcribe(vocals: Path, language: str | None) -> tuple[list[dict], str]:
    """[{line, words: [{w, t0, t1}]}], language. faster-whisper; WHISPER_MODEL picks the size."""
    from faster_whisper import WhisperModel
    size = os.environ.get("WHISPER_MODEL", "small")
    log(f"transcribing the vocals (whisper {size})…")
    model = WhisperModel(size, device="cpu", compute_type="int8")
    segs, info = model.transcribe(str(vocals), language=None if language in (None, "auto") else language, word_timestamps=True, vad_filter=True, beam_size=5)
    lines = []
    for s in segs:
        words = [{"w": w.word.strip(), "t0": float(w.start), "t1": float(w.end)} for w in (s.words or []) if w.word.strip()]
        if words:
            lines.append({"line": s.text.strip(), "words": words})
    return lines, info.language


def lines_from_text(text: str, notes: list[N], language: str) -> list[dict]:
    """Lyrics you typed, no timings: words take the lead's notes in order, one per syllable."""
    out, k = [], 0
    for ln in lang.lyric_lines(text):
        words = []
        for w in ln.split():
            clean = w.strip(lang.PHRASE_PUNCT + "\"'()¡¿«»“”-")
            n = max(1, len(lang.syllables(clean, language))) if clean else 0
            if not n or k >= len(notes):
                continue
            grp = notes[k : k + n]
            words.append({"w": w, "t0": grp[0].start, "t1": grp[-1].end})
            k += n
        if words:
            out.append({"line": ln, "words": words})
    return out


def align(lines: list[dict], notes: list[N], language: str) -> tuple[list[N], list[dict]]:
    """Each word's syllables onto the notes sung while it sounds. Returns the notes kept (some split,
    unworded ones dropped or held as melisma) and one syllable per note."""
    # a word that starts in the middle of a held note splits it there (two syllables, one pitch)
    cuts = sorted(w["t0"] for line in lines for w in line["words"])
    split: list[N] = []
    for n in notes:
        parts = [N(n.start, n.end, n.pitch, n.vel)]
        for c in cuts:
            last = parts[-1]
            if last.start + 0.08 < c < last.end - 0.08:
                parts[-1:] = [N(last.start, c, n.pitch, n.vel), N(c, last.end, n.pitch, n.vel)]
        split.extend(parts)
    notes = split
    used = [False] * len(notes)
    out_notes: list[N] = []
    out_syls: list[dict] = []
    for li, line in enumerate(lines):
        for wi, w in enumerate(line["words"]):
            word = w["w"].strip(lang.PHRASE_PUNCT + "\"'()¡¿«»“”-")
            if not word:
                continue
            syls = lang.syllables(word, language)
            if not syls:
                continue
            idx = [i for i, n in enumerate(notes) if not used[i] and w["t0"] - 0.12 <= n.start < w["t1"] - 0.02]
            if not idx:  # no note starts inside the word: the note sounding at its middle
                mid = (w["t0"] + w["t1"]) / 2
                idx = [i for i, n in enumerate(notes) if not used[i] and n.start <= mid < n.end][:1]
            if not idx:
                continue
            group = [notes[i] for i in idx]
            for i in idx:
                used[i] = True
            while len(group) < len(syls):  # more syllables than notes: split the longest note
                k = max(range(len(group)), key=lambda j: group[j].end - group[j].start)
                g = group[k]
                mid = (g.start + g.end) / 2
                group[k:k + 1] = [N(g.start, mid, g.pitch, g.vel), N(mid, g.end, g.pitch, g.vel)]
            assigned = [dict(s) for s in syls[:-1]] + [dict(syls[-1])]
            while len(assigned) < len(group):  # more notes than syllables: the last one runs on
                assigned.append(lang.melisma_of(assigned[-1]))
            last_word = wi == len(line["words"]) - 1
            if last_word:
                assigned[-1]["phraseEnd"] = True
            for s in assigned:
                s["line"] = li
            out_notes.extend(group)
            out_syls.extend(assigned)
    # notes no word claimed: held on as melisma when they follow on, else left out
    order = sorted(range(len(out_notes)), key=lambda i: out_notes[i].start)
    out_notes = [out_notes[i] for i in order]
    out_syls = [out_syls[i] for i in order]
    extra = [n for i, n in enumerate(notes) if not used[i]]
    for n in extra:
        k = max((i for i, m in enumerate(out_notes) if m.end <= n.start + 0.02), default=-1, key=lambda i: out_notes[i].end)
        if k >= 0 and n.start - out_notes[k].end < 0.1 and n.end - n.start < 1.2:
            out_notes.insert(k + 1, n)
            out_syls.insert(k + 1, dict(lang.melisma_of(out_syls[k]), line=out_syls[k].get("line", 0)))
    # one note at a time: trim overlaps from splitting
    for a, b in zip(out_notes, out_notes[1:]):
        if a.end > b.start:
            a.end = b.start
    keep = [i for i, n in enumerate(out_notes) if n.end - n.start > 0.04]
    return [out_notes[i] for i in keep], [out_syls[i] for i in keep]


# ------------------------------------------------------------------ polyphonic stems

def notes_of(path: Path, lo: int = 21, hi: int = 108, min_amp: float = 0.3) -> list[N]:
    from basic_pitch.inference import predict
    _, _, ev = predict(str(path), onset_threshold=0.5, frame_threshold=0.3, minimum_note_length=60)
    out = [N(float(s), float(e), int(p), int(min(127, 40 + 87 * a))) for s, e, p, a, _ in ev if a >= min_amp and lo <= p <= hi and e - s >= 0.05]
    return sorted(out, key=lambda n: (n.start, -n.pitch))


def lowest_line(notes: list[N]) -> list[N]:
    out: list[N] = []
    for n in sorted(notes, key=lambda n: (n.start, n.pitch)):
        if out and abs(n.start - out[-1].start) < 0.04:
            continue
        if out and out[-1].end > n.start:
            out[-1].end = n.start
        out.append(N(n.start, n.end, n.pitch, n.vel))
    return [n for n in out if n.end - n.start > 0.05]


def voices_from(notes: list[N], n_voices: int, min_notes: int = 8) -> list[list[N]]:
    """Polyphony into separate singable lines: each note to the free voice nearest in pitch."""
    lines: list[list[N]] = [[] for _ in range(n_voices)]
    for n in sorted(notes, key=lambda n: (n.start, -n.pitch)):
        free = [k for k in range(n_voices) if not lines[k] or lines[k][-1].end <= n.start + 0.03]
        if not free:
            continue
        k = min(free, key=lambda k: abs((lines[k][-1].pitch if lines[k] else 72 - 6 * k) - n.pitch))
        if lines[k] and lines[k][-1].end > n.start:
            lines[k][-1].end = n.start
        lines[k].append(n)
    lines = [ln for ln in lines if len(ln) >= min_notes]
    return sorted(lines, key=lambda ln: -np.median([n.pitch for n in ln]))


def backing_vocals(vocals: Path, lead: list[N]) -> list[list[N]]:
    allnotes = notes_of(vocals, 40, 90, 0.35)

    def lead_at(t):
        for n in lead:
            if n.start - 0.05 <= t < n.end + 0.05:
                return n.pitch
        return None

    rest = []
    for n in allnotes:
        lp = lead_at((n.start + n.end) / 2)
        if lp is not None and (abs(n.pitch - lp) <= 1 or abs(abs(n.pitch - lp) - 12) <= 1):
            continue
        if n.end - n.start >= 0.12:
            rest.append(n)
    return voices_from(rest, 3, min_notes=10)


def drum_hits(path: Path) -> list[N]:
    """Onsets of the drum stem, each sorted by where its energy is: kick, snare, hat, crash."""
    import librosa
    y = load(path)
    hop = 256
    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=hop)
    on = librosa.onset.onset_detect(onset_envelope=env, sr=SR, hop_length=hop, units="samples", backtrack=False)
    if not len(on):
        return []
    peak = float(np.max(env)) or 1.0
    hits = []
    freqs = np.fft.rfftfreq(1024, 1 / SR)
    band = lambda S, a, b: float(S[(freqs >= a) & (freqs < b)].sum())
    for s in on:
        w = y[s : s + 1024]
        if len(w) < 1024:
            break
        S = np.abs(np.fft.rfft(w * np.hanning(1024))) ** 2
        tot = S.sum() + 1e-12
        low, mid, high = band(S, 20, 150) / tot, band(S, 150, 2500) / tot, band(S, 5000, 11000) / tot
        tail = y[s + int(0.25 * SR) : s + int(0.35 * SR)]
        ring = float(np.sqrt(np.mean(tail ** 2))) / (float(np.sqrt(np.mean(w ** 2))) + 1e-9) if len(tail) else 0
        if low > 0.45:
            note = 36
        elif high > 0.45 and ring > 0.25:
            note = 49
        elif high > 0.35:
            note = 42
        else:
            note = 38
        st = env[min(len(env) - 1, s // hop)] / peak
        hits.append(N(s / SR, s / SR + 0.1, note, int(40 + 87 * min(1, st))))
    return hits


# ------------------------------------------------------------------ acapella

def chord_lines(notes: list[N], bpm: float, first_beat: float, end: float) -> list[list[N]]:
    """The harmony as three held choir lines (low, middle, high), one chord per beat."""
    beat = 60 / max(40.0, bpm)
    t = first_beat
    while t - beat > 0:
        t -= beat
    ranges = [(50, 62), (57, 69), (62, 75)]
    lines: list[list[N]] = [[], [], []]
    while t < end:
        w = np.zeros(12)
        for n in notes:
            ov = min(n.end, t + beat) - max(n.start, t)
            if ov > 0:
                w[n.pitch % 12] += ov * n.vel
        if w.sum() > 0:
            pcs = [int(p) for p in np.argsort(w)[::-1][:3] if w[p] > 0.15 * w.max()]
            pcs = sorted(pcs)
            for k, (lo, hi) in enumerate(ranges):
                if not pcs:
                    break
                pc = pcs[k % len(pcs)]
                prev = lines[k][-1].pitch if lines[k] else (lo + hi) // 2
                cands = [p for p in range(lo, hi + 1) if p % 12 == pc]
                p = min(cands, key=lambda c: abs(c - prev)) if cands else prev
                if lines[k] and lines[k][-1].pitch == p and abs(lines[k][-1].end - t) < 1e-6:
                    lines[k][-1].end = t + beat
                else:
                    lines[k].append(N(t, t + beat, p, 80))
        t += beat
    return [ln for ln in lines if ln]


BEAT_OF = {36: "B", 35: "B", 38: "Pf", 40: "Pf", 37: "K", 42: "t", 44: "t", 46: "tss", 49: "kch", 57: "kch", 51: "t", 52: "kch", 55: "kch"}


# ------------------------------------------------------------------ writing the song

def write_midi(path: Path, tracks: list[tuple[str, list[N], int]]) -> None:
    """Seconds straight into ticks: 60 bpm and 1000 ticks a beat (1 tick = 1 ms)."""
    mid = mido.MidiFile(ticks_per_beat=1000)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("set_tempo", tempo=1_000_000, time=0))
    mid.tracks.append(meta)
    for name, notes, ch in tracks:
        tr = mido.MidiTrack()
        tr.append(mido.MetaMessage("track_name", name=name, time=0))
        ev = []
        for n in notes:
            ev.append((int(n.start * 1000), 1, n.pitch, n.vel))
            ev.append((max(int(n.start * 1000) + 1, int(n.end * 1000) - 1), 0, n.pitch, 0))
        ev.sort(key=lambda e: (e[0], e[1]))
        now = 0
        for tick, on, p, v in ev:
            tr.append(mido.Message("note_on" if on else "note_off", note=max(0, min(127, p)), velocity=max(1, min(127, v)) if on else 0, channel=ch, time=tick - now))
            now = tick
        mid.tracks.append(tr)
    mid.save(str(path))


def run(audio: str, slug: str, *, start=None, end=None, length=None, title=None, artist="", language="auto", arrangement="band",
        voice="auto", lyrics_file=None, stems_dir=None) -> Path:
    out = SONGS / slug
    work = WORK / slug
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    if stems_dir:
        stems = {s: Path(stems_dir) / f"{s}.wav" for s in STEMS if (Path(stems_dir) / f"{s}.wav").exists()}
        mix = Path(audio) if audio and Path(audio).exists() else stems["vocals"]
        offset = 0.0
    else:
        if start == "auto":
            L = float(length or 59)
            start, why = best_window(Path(audio), L)
            end = start + L
            log(f"the best {L:.0f} s start at {start:.1f} s ({why})")
        elif start is not None and length and not end:
            end = float(start) + float(length)
        mix = clip(Path(audio), work, start, end)
        stems = separate(mix, work)
        offset = 0.0
    ymix = load(mix)
    duration = len(ymix) / SR
    bpm, first_beat = tempo(ymix)
    log(f"{duration:.1f} s, {bpm} bpm, first beat at {first_beat:.2f} s")
    mix_db = loudness(ymix)
    present = {s: p for s, p in stems.items() if loudness(load(p)) > mix_db - 30}
    log("stems with something in them: " + ", ".join(present))

    tracks: list[tuple[str, list[N], int]] = []
    parts: list[dict] = []

    # the lead and its words
    lead, stats, tone, body = [], {}, {}, "female"
    if "vocals" in present:
        yv = load(present["vocals"])
        lead, stats = lead_melody(yv)
        body = voice if voice != "auto" else voice_for(stats)
        tone = tone_for(stats)
        log(f"lead: {len(lead)} notes, median midi {stats['median']:.1f} -> {body} voice, vibrato {tone['vibrato']} c, tension {tone['tension']}")
        if lyrics_file:
            lang_code = language if language != "auto" else "en"
            lines = lines_from_text(Path(lyrics_file).read_text(encoding="utf-8"), lead, lang_code)
        else:
            try:
                lines, lang_code = transcribe(present["vocals"], language)
            except ImportError:
                log("faster-whisper is not installed: give the words with --lyrics")
                lines, lang_code = [], (language if language != "auto" else "en")
        language = lang_code if language == "auto" else language
        if lines:
            lead, syls = align(lines, lead, language)
        else:
            syls = [{"text": "la", "onset": [{"ipa": "l", "stress": 0}], "nucleus": {"ipa": "a", "stress": 1}, "coda": [], "wordEnd": True, "phraseEnd": False, "line": -1} for _ in lead]
        (out / "lead.syllables.json").write_text(json.dumps(syls, ensure_ascii=False))
        tracks.append(("Lead vocal", lead, 0))
        parts.append({"name": "Lead", "track": "Lead vocal", "body": body, "role": "lead", "syllables": "lead.syllables.json", "octave": 0,
                      "tone": tone, "lines": [ln["line"] for ln in lines]})
        for k, bv in enumerate(backing_vocals(present["vocals"], lead)):
            med = float(np.median([n.pitch for n in bv]))
            b = "female" if med >= 62 else "male"
            tracks.append((f"Backing vocal {k + 1}", bv, 0))
            parts.append({"name": f"Backing {k + 1}", "track": f"Backing vocal {k + 1}", "body": b, "role": "backing", "lyrics": "echo: Lead", "octave": 0, "gain": 0.6,
                          "tone": {"vibrato": tone.get("vibrato", 30) * 0.6, "tension": 0.5, "pressure": 8.5}})
        log(f"backing vocals: {sum(1 for p in parts if p['role'] == 'backing')}")

    drums = drum_hits(present["drums"]) if "drums" in present else []
    bass = lowest_line(notes_of(present["bass"], 24, 64)) if "bass" in present else []
    harmonic = {s: notes_of(present[s], 36, 96) for s in ("guitar", "piano", "other") if s in present}
    if arrangement == "acapella":
        if bass:
            vb = [N(n.start, n.end, n.pitch + 12 * max(0, (40 - n.pitch + 11) // 12), n.vel) for n in bass]
            tracks.append(("Vocal bass", vb, 0))
            parts.append({"name": "Vocal bass", "track": "Vocal bass", "body": "male", "role": "bass", "lyrics": "vocalise: dum", "octave": 0, "gain": 0.9,
                          "tone": {"vibrato": 0, "tension": 0.5, "pressure": 10}})
        allh = [n for v in harmonic.values() for n in v]
        for k, ln in enumerate(chord_lines(allh, bpm, first_beat, duration)):
            nm = ["Choir low", "Choir mid", "Choir high"][k]
            tracks.append((nm, ln, 0))
            parts.append({"name": nm, "track": nm, "body": ["male", "female", "female"][k], "role": "choir", "lyrics": "vocalise: oo", "octave": 0, "gain": 0.55,
                          "tone": {"vibrato": 20, "tension": 0.48, "pressure": 8.5}})
        if drums:
            tracks.append(("Beatbox", drums, 9))
            parts.append({"name": "Beatbox", "track": "Beatbox", "body": "male", "role": "beatbox", "beatbox": True, "gain": 0.9})
    else:
        if drums:
            tracks.append(("Drums", drums, 9))
            parts.append({"name": "Drums", "track": "Drums", "instrument": "drums", "gain": 0.7})
        if bass:
            tracks.append(("Bass", bass, 1))
            parts.append({"name": "Bass", "track": "Bass", "instrument": "bass", "gain": 0.8})
        kinds = {"guitar": "guitar", "piano": "piano", "other": "strings"}
        for s, ns in harmonic.items():
            if len(ns) >= 8:
                kind = kinds[s]
                if s == "other" and np.median([n.end - n.start for n in ns]) < 0.35:
                    kind = "synth"
                tracks.append((s.capitalize(), ns, 2))
                parts.append({"name": kind.capitalize(), "track": s.capitalize(), "instrument": kind, "gain": 0.55})
    write_midi(out / "arrangement.mid", tracks)
    spec = {"title": title or slug.replace("-", " ").title(), "artist": artist, "language": language, "midi": "arrangement.mid",
            "arrangement": arrangement, "bpm": bpm, "start": 0, "end": round(duration + offset, 2), "fade_out": 1.5, "parts": parts}
    (out / "song.yaml").write_text("# Written by `python3 -m mech listen`. Edit freely: names, colours, bodies, gains.\n"
                                   + yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    log(f"wrote {out}/song.yaml with {len(parts)} parts")
    return out
