"""Instruments the robots play, synthesized from MIDI notes (no samples, like the voices).

guitar / bass: Karplus-Strong plucked strings. piano: struck partials with stretched tuning.
synth: a filtered sawtooth. strings: a detuned ensemble pad. drums: General MIDI drum map.
Everything returns mono float32 at 48 kHz on the song's clock (sample 0 = the song's 0 s).
"""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, lfilter, sosfilt

FS = 48000
KINDS = ("guitar", "bass", "piano", "synth", "strings", "drums")


def hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def _env(n: int, a: float, r: float) -> np.ndarray:
    e = np.ones(n, np.float32)
    na = max(1, int(a * FS))
    e[:na] = np.linspace(0, 1, na, dtype=np.float32)[: len(e[:na])]
    nr = min(n, int(r * FS))
    if nr > 0:
        e[-nr:] *= np.linspace(1, 0, nr, dtype=np.float32)
    return e


def _pluck(f: float, dur: float, vel: float, bright: float, decay: float, rng) -> np.ndarray:
    n = int((dur + decay * 0.6) * FS)
    period = FS / f
    N = int(period)
    frac = period - N
    burst = rng.uniform(-1, 1, N).astype(np.float64)
    burst = lfilter([bright], [1, -(1 - bright)], burst)  # softer pick for lower `bright`
    x = np.zeros(n)
    x[:N] = burst
    w = period - N
    d = float(np.exp(-6.9 / (decay * f)))  # -60 dB after `decay` seconds
    a = np.zeros(N + 2)
    a[0] = 1
    a[N] = -d * (1 - w)
    a[N + 1] = -d * w
    y = lfilter([1.0], a, x)
    # damp the string when the note ends
    off = int(dur * FS)
    if off < n:
        tail = n - off
        y[off:] *= np.exp(-np.arange(tail) / (0.06 * FS))
    return (y * vel / max(1e-9, np.abs(y[: N * 4]).max())).astype(np.float32)


def _piano(f: float, dur: float, vel: float) -> np.ndarray:
    n = int((dur + 0.6) * FS)
    t = np.arange(n) / FS
    y = np.zeros(n)
    B = 0.0004
    for k in range(1, 9):
        fk = f * k * np.sqrt(1 + B * k * k)
        if fk > FS / 2.2:
            break
        y += np.sin(2 * np.pi * fk * t) * np.exp(-t * (1.2 + 0.9 * k) * (f / 260) ** 0.3) / k ** (1.4 - 0.5 * vel)
    rel = np.ones(n)
    off = int(dur * FS)
    rel[off:] = np.exp(-np.arange(n - off) / (0.12 * FS))
    return (y * rel * _env(n, 0.003, 0.01) * vel * 0.5).astype(np.float32)


def _saw(f: float, t: np.ndarray) -> np.ndarray:
    # band-limited enough at these pitches: a few harmonics
    y = np.zeros_like(t)
    for k in range(1, 16):
        if f * k > FS / 2.5:
            break
        y += np.sin(2 * np.pi * f * k * t) / k
    return y


def _synth(f: float, dur: float, vel: float) -> np.ndarray:
    n = int((dur + 0.2) * FS)
    t = np.arange(n) / FS
    y = _saw(f, t) * 0.6 + np.sign(np.sin(2 * np.pi * f * 0.5 * t)) * 0.15
    sos = butter(2, min(0.45, (f * 5) / (FS / 2)), output="sos")
    return (sosfilt(sos, y) * _env(n, 0.01, 0.2) * vel * 0.35).astype(np.float32)


def _strings(f: float, dur: float, vel: float) -> np.ndarray:
    n = int((dur + 0.5) * FS)
    t = np.arange(n) / FS
    y = sum(_saw(f * c, t + ph) for c, ph in ((1.0, 0), (1.004, 0.13), (0.996, 0.29)))
    vib = 1 + 0.003 * np.sin(2 * np.pi * 5.2 * t)
    sos = butter(2, min(0.45, 2500 / (FS / 2)), output="sos")
    return (sosfilt(sos, y * vib) * _env(n, 0.18, 0.45) * vel * 0.18).astype(np.float32)


def _noise(n, rng):
    return rng.uniform(-1, 1, n)


def _drum(note: int, vel: float, rng) -> np.ndarray:
    if note in (35, 36):  # kick
        n = int(0.45 * FS); t = np.arange(n) / FS
        f = 50 + 110 * np.exp(-t * 30)
        y = np.sin(2 * np.pi * np.cumsum(f) / FS) * np.exp(-t * 7)
        y[:200] += _noise(200, rng) * np.linspace(0.4, 0, 200)
    elif note in (38, 40, 37, 39):  # snare, clap, rim
        n = int(0.3 * FS); t = np.arange(n) / FS
        body = np.sin(2 * np.pi * 185 * t) * np.exp(-t * 25)
        sos = butter(2, [1500 / (FS / 2), 9000 / (FS / 2)], btype="band", output="sos")
        y = 0.6 * body + sosfilt(sos, _noise(n, rng)) * np.exp(-t * 16) * 1.6
    elif note in (42, 44, 46, 51, 53, 59):  # hats, ride
        n = int((0.35 if note in (46, 51, 59) else 0.08) * FS); t = np.arange(n) / FS
        sos = butter(4, 7000 / (FS / 2), btype="high", output="sos")
        y = sosfilt(sos, _noise(n, rng)) * np.exp(-t * (8 if note in (46, 51, 59) else 45)) * 0.7
    elif note in (49, 57, 52, 55):  # crash
        n = int(1.4 * FS); t = np.arange(n) / FS
        sos = butter(2, 4000 / (FS / 2), btype="high", output="sos")
        y = sosfilt(sos, _noise(n, rng)) * np.exp(-t * 2.6) * 0.6
    else:  # toms and the rest
        n = int(0.4 * FS); t = np.arange(n) / FS
        f0 = hz(note) * 0.5 + 60
        y = np.sin(2 * np.pi * np.cumsum(f0 * (1 + 0.5 * np.exp(-t * 20))) / FS) * np.exp(-t * 9)
    return (y * vel).astype(np.float32)


def render(kind: str, notes, length: float, seed: int = 1) -> np.ndarray:
    """notes: objects with start, end, pitch, vel (0-127). Returns mono float32 of `length` s."""
    if kind not in KINDS:
        raise SystemExit(f"unknown instrument {kind!r}; have {', '.join(KINDS)}")
    rng = np.random.default_rng(seed)
    out = np.zeros(int(length * FS) + FS, np.float32)
    for nt in notes:
        v = (nt.vel / 127) ** 1.3
        dur = max(0.05, nt.end - nt.start)
        if kind == "drums":
            y = _drum(nt.pitch, v, rng)
        elif kind == "guitar":
            y = _pluck(hz(nt.pitch), dur, v, 0.55, 1.6, rng) * 0.55
        elif kind == "bass":
            y = _pluck(hz(nt.pitch), dur, v, 0.25, 2.4, rng) * 0.8
        elif kind == "piano":
            y = _piano(hz(nt.pitch), dur, v)
        elif kind == "synth":
            y = _synth(hz(nt.pitch), dur, v)
        else:
            y = _strings(hz(nt.pitch), dur, v)
        i = int(nt.start * FS)
        j = min(len(out), i + len(y))
        out[i:j] += y[: j - i]
    return out[: int(length * FS)]
