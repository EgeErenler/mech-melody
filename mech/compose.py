"""Write an arrangement as MIDI from a compact text score (for songs we arrange ourselves,
such as public-domain carols).

    melody("B4/1 B4/1 B4/2 | r/1 D5/.5")    note/beats, r = rest, | ignored (bar lines for reading)
    Arrangement(bpm, beats_per_bar).voice(name, notes, start_beat) / .chords(...) / .save(path)
"""
from __future__ import annotations

import re
from pathlib import Path

import mido

NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
QUAL = {"": (0, 4, 7), "m": (0, 3, 7), "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11), "m7": (0, 3, 7, 10), "dim": (0, 3, 6), "sus4": (0, 5, 7), "6": (0, 4, 7, 9)}


def midi_of(name: str) -> int:
    m = re.fullmatch(r"([A-G])([#b]?)(-?\d)", name)
    if not m:
        raise ValueError(f"bad note {name!r}")
    n = NOTE[m.group(1)] + {"#": 1, "b": -1, "": 0}[m.group(2)]
    return 12 * (int(m.group(3)) + 1) + n


def melody(text: str) -> list[tuple[int | None, float]]:
    out = []
    for tok in text.replace("|", " ").split():
        n, b = tok.split("/")
        out.append((None if n == "r" else midi_of(n), float(b)))
    return out


def chord(name: str, octave: int = 3) -> list[int]:
    m = re.fullmatch(r"([A-G][#b]?)(maj7|m7|dim|sus4|m|7|6)?", name)
    if not m:
        raise ValueError(f"bad chord {name!r}")
    root = midi_of(m.group(1) + str(octave))
    return [root + i for i in QUAL[m.group(2) or ""]]


def third_below(notes: list[tuple[int | None, float]], scale: list[int]) -> list[tuple[int | None, float]]:
    """The same tune a diatonic third lower (two steps down the scale)."""
    pcs = sorted(p % 12 for p in scale)
    out = []
    for p, b in notes:
        if p is None:
            out.append((None, b))
            continue
        q = p
        steps = 0
        while steps < 2:
            q -= 1
            if q % 12 in pcs:
                steps += 1
        out.append((q, b))
    return out


class Arrangement:
    def __init__(self, bpm: float, beats_per_bar: int = 4, tpb: int = 480):
        self.bpm, self.bar, self.tpb = bpm, beats_per_bar, tpb
        self.tracks: list[tuple[str, int, int, list]] = []

    def _get(self, name: str, channel: int, program: int) -> list:
        for n, c, p, ev in self.tracks:
            if n == name:
                return ev
        ev: list = []
        self.tracks.append((name, channel, program, ev))
        return ev

    def notes(self, name: str, notes: list[tuple[int | None, float]], start: float, vel: int = 96, legato: float = 0.95, channel: int = 0, program: int = 0) -> float:
        """Lay a line of (midi, beats) from beat `start`; returns the beat after it."""
        ev = self._get(name, channel, program)
        t = start
        for p, b in notes:
            if p is not None:
                ev.append((t, b * legato, p, vel))
            t += b
        return t

    def hold(self, name: str, pitch: int, start: float, beats: float, vel: int = 80, channel: int = 0, program: int = 0) -> None:
        self._get(name, channel, program).append((start, beats, pitch, vel))

    def comp(self, name: str, chords: list[str], start: float, style: str = "pop", octave: int = 3, vel: int = 72, program: int = 0, channel: int = 1) -> None:
        """Chords, one per bar: pop (beats 1, 2&, 3, 4), waltz (bass then two chords), pad (held)."""
        for i, c in enumerate(chords):
            b0 = start + i * self.bar
            notes = chord(c, octave + 1)
            if style == "pad":
                for p in notes:
                    self.hold(name, p, b0, self.bar * 0.98, vel, channel, program)
            elif style == "waltz":
                for k, p in enumerate(notes[:3]):
                    self.hold(name, p, b0 + 1, 0.9, vel, channel, program)
                    self.hold(name, p, b0 + 2, 0.9, vel - 6, channel, program)
            elif style == "bells":  # a high arpeggio, like a music box
                for k in range(self.bar * 2):
                    self.hold(name, notes[k % len(notes)] + 12, b0 + k * 0.5, 0.45, vel - (k % 2) * 10, channel, program)
            else:
                for at, dur, dv in ((0, 1.4, 0), (1.5, 0.9, -12), (2.5, 0.5, -16), (3, 0.9, -8)):
                    if at < self.bar:
                        for p in notes:
                            self.hold(name, p, b0 + at, dur, vel + dv, channel, program)

    def bassline(self, name: str, chords: list[str], start: float, style: str = "pop", vel: int = 92) -> None:
        for i, c in enumerate(chords):
            b0 = start + i * self.bar
            root = chord(c, 2)[0]
            if root > 43:  # keep the bass between E1 and G2
                root -= 12
            fifth = root + 7
            if style == "waltz":
                self.hold(name, root, b0, 0.95, vel, 2, 33)
            elif style == "walk":
                for k, p in enumerate((root, fifth, root + 12, fifth)[: self.bar]):
                    self.hold(name, p, b0 + k, 0.9, vel - (k % 2) * 8, 2, 33)
            else:
                self.hold(name, root, b0, 1.4, vel, 2, 33)
                self.hold(name, fifth if self.bar >= 4 else root, b0 + 2, 1.4, vel - 6, 2, 33)

    def drums(self, name: str, bars: int, start: float, style: str = "pop", jingle: bool = True, fill_every: int = 8) -> None:
        for i in range(bars):
            b0 = start + i * self.bar
            if style == "waltz":
                self.hold(name, 36, b0, 0.2, 100, 9)
                self.hold(name, 37, b0 + 1, 0.2, 70, 9)
                self.hold(name, 37, b0 + 2, 0.2, 64, 9)
            else:
                for k in range(self.bar):
                    self.hold(name, 36 if k % 2 == 0 else 38, b0 + k, 0.2, 104 if k % 2 == 0 else 92, 9)
                if fill_every and i % fill_every == fill_every - 1:
                    for k, p in enumerate((50, 48, 45, 43)):
                        self.hold(name, p, b0 + 2 + k * 0.5, 0.2, 96, 9)
            if jingle:  # sleigh bells on every eighth, the off-beats softer
                for k in range(self.bar * 2):
                    self.hold(name, 83, b0 + k * 0.5, 0.2, 92 if k % 2 == 0 else 66, 9)
            if i % 8 == 0:
                self.hold(name, 49, b0, 1, 96, 9)

    def save(self, path: Path) -> None:
        mid = mido.MidiFile(ticks_per_beat=self.tpb)
        meta = mido.MidiTrack()
        meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(self.bpm), time=0))
        meta.append(mido.MetaMessage("time_signature", numerator=self.bar, denominator=4, time=0))
        mid.tracks.append(meta)
        for name, ch, prog, ev in self.tracks:
            tr = mido.MidiTrack()
            tr.append(mido.MetaMessage("track_name", name=name, time=0))
            tr.append(mido.Message("program_change", program=prog, channel=ch, time=0))
            msgs = []
            for t, d, p, v in ev:
                msgs.append((int(round(t * self.tpb)), 1, p, v))
                msgs.append((int(round((t + d) * self.tpb)) - 2, 0, p, 0))
            msgs.sort(key=lambda m: (m[0], m[1]))
            now = 0
            for tick, on, p, v in msgs:
                tr.append(mido.Message("note_on" if on else "note_off", note=p, velocity=v if on else 0, channel=ch, time=max(0, tick - now)))
                now = max(now, tick)
            mid.tracks.append(tr)
        mid.save(str(path))
