"""MIDI files -> notes and lyric events in seconds (tempo changes respected)."""
from __future__ import annotations

from dataclasses import dataclass

import mido


@dataclass
class Note:
    start: float
    end: float
    pitch: int
    vel: int


@dataclass
class Lyric:
    time: float
    text: str


@dataclass
class Track:
    index: int
    name: str
    channels: set
    notes: list
    lyrics: list
    program: int | None


def _tempo_map(mid: mido.MidiFile) -> list[tuple[int, int]]:
    changes = []
    for tr in mid.tracks:
        t = 0
        for msg in tr:
            t += msg.time
            if msg.type == "set_tempo":
                changes.append((t, msg.tempo))
    changes.sort()
    if not changes or changes[0][0] != 0:
        changes.insert(0, (0, 500000))
    return changes


def _seconds(changes, tpb):
    # cumulative seconds at each tempo change
    acc = [0.0]
    for (t0, tempo), (t1, _) in zip(changes, changes[1:]):
        acc.append(acc[-1] + mido.tick2second(t1 - t0, tpb, tempo))

    def conv(tick: int) -> float:
        k = 0
        while k + 1 < len(changes) and changes[k + 1][0] <= tick:
            k += 1
        return acc[k] + mido.tick2second(tick - changes[k][0], tpb, changes[k][1])

    return conv


def first_bpm(mid: mido.MidiFile) -> float:
    return round(mido.tempo2bpm(_tempo_map(mid)[0][1]), 2)


def read(path) -> list[Track]:
    mid = mido.MidiFile(str(path))
    conv = _seconds(_tempo_map(mid), mid.ticks_per_beat)
    tracks = []
    for i, tr in enumerate(mid.tracks):
        t = 0
        on: dict[tuple[int, int], tuple[int, int]] = {}
        notes, lyrics, channels, name, program = [], [], set(), tr.name or f"track {i}", None
        for msg in tr:
            t += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                on[(msg.channel, msg.note)] = (t, msg.velocity)
                channels.add(msg.channel)
            elif msg.type in ("note_off", "note_on"):
                k = (msg.channel, msg.note)
                if k in on:
                    t0, v = on.pop(k)
                    notes.append(Note(conv(t0), conv(t), msg.note, v))
            elif msg.type == "program_change" and program is None:
                program = msg.program
            elif msg.type == "lyrics" or (msg.type == "text" and t > 0):
                txt = msg.text
                if msg.type == "text" and (txt.startswith("@") or not txt.strip("/\\ -")):
                    continue
                lyrics.append(Lyric(conv(t), txt))
        notes.sort(key=lambda n: (n.start, -n.pitch))
        tracks.append(Track(i, name, channels, notes, lyrics, program))
    return tracks


def pick(tracks: list[Track], sel) -> Track:
    """A track by index or (part of its) name."""
    if isinstance(sel, int):
        tr = tracks[sel]
    else:
        hits = [t for t in tracks if str(sel).lower() in t.name.lower()]
        if not hits:
            names = ", ".join(f"{t.index}:{t.name!r}({len(t.notes)} notes)" for t in tracks)
            raise SystemExit(f"no MIDI track named {sel!r}; tracks are: {names}")
        tr = hits[0]
    return tr


def by_channel(path, channel: int) -> list[Note]:
    """Every note on one MIDI channel (0-based), across all tracks (type-0 files)."""
    mid = mido.MidiFile(str(path))
    conv = _seconds(_tempo_map(mid), mid.ticks_per_beat)
    notes = []
    for tr in mid.tracks:
        t, on = 0, {}
        for msg in tr:
            t += msg.time
            if getattr(msg, "channel", None) != channel:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                on[msg.note] = (t, msg.velocity)
            elif msg.type in ("note_off", "note_on") and msg.note in on:
                t0, v = on.pop(msg.note)
                notes.append(Note(conv(t0), conv(t), msg.note, v))
    notes.sort(key=lambda n: (n.start, -n.pitch))
    return notes


def monophonic(notes: list[Note]) -> list[Note]:
    """One note at a time for a singer: the top note of a chord, each cut where the next begins."""
    out: list[Note] = []
    for n in notes:
        if out and abs(n.start - out[-1].start) < 0.02:
            if n.pitch > out[-1].pitch:
                out[-1] = Note(out[-1].start, n.end, n.pitch, n.vel)
            continue
        if out and out[-1].end > n.start:
            out[-1].end = n.start
        out.append(Note(n.start, n.end, n.pitch, n.vel))
    return [n for n in out if n.end - n.start > 0.04]


def describe(path) -> str:
    rows = []
    for t in read(path):
        if t.notes or t.lyrics:
            lo = min((n.pitch for n in t.notes), default=0)
            hi = max((n.pitch for n in t.notes), default=0)
            rows.append(f"  {t.index:>2}  {t.name[:28]:<28} notes {len(t.notes):>4}  lyrics {len(t.lyrics):>4}  "
                        f"channels {sorted(t.channels)}  range {lo}-{hi}  program {t.program}")
    return "\n".join(rows)
