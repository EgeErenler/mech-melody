"""Karaoke MIDI (.kar and MIDIs with lyric events): the words with their times, and which
channel or track carries the sung melody they belong to."""
from __future__ import annotations

from pathlib import Path

import mido

from . import midi


def lyric_events(path: Path) -> list[tuple[float, str]]:
    """(time s, text) for every lyric event; plain text events only when a file has no lyric events."""
    mf = mido.MidiFile(str(path))
    conv = midi._seconds(midi._tempo_map(mf), mf.ticks_per_beat)
    lyr, txt = [], []
    for tr in mf.tracks:
        t = 0
        for m in tr:
            t += m.time
            if m.type == "lyrics":
                lyr.append((conv(t), m.text))
            elif m.type == "text" and t > 0 and not m.text.startswith("@"):
                txt.append((conv(t), m.text))
    ev = lyr or txt
    return sorted(ev, key=lambda e: e[0])


def lines(events: list[tuple[float, str]]) -> list[dict]:
    return _split_long(_lines(events))


def _lines(events: list[tuple[float, str]]) -> list[dict]:
    """Karaoke events -> [{line, words: [{w, t0, t1}]}]. A space starts or ends a word; CR, LF,
    "/" or "\\" start a new line; a syllable with no space before it continues the word."""
    out: list[dict] = []
    words: list[dict] = []
    cur: dict | None = None
    spaced = sum(1 for _, x in events if x.startswith(" ") or x.endswith(" ") or "\n" in x or "\r" in x or x.startswith(("/", "\\")))
    if events and spaced < 0.1 * len(events):
        # one word per event (a trailing "-" joins the next); lines break at a pause
        for i, (t, raw) in enumerate(events):
            nxt = events[i + 1][0] if i + 1 < len(events) else t + 1.0
            body = raw.strip()
            if not body:
                continue
            if words and t - words[-1]["t1"] > 1.2:
                out.append({"line": " ".join(w["w"] for w in words), "words": list(words)})
                words.clear()
            if cur is None:
                cur = {"w": "", "t0": t, "t1": nxt}
            cur["w"] += body.rstrip("-")
            cur["t1"] = min(nxt, t + 1.5)
            if not body.endswith("-"):
                words.append(cur)
                cur = None
        if words:
            out.append({"line": " ".join(w["w"] for w in words), "words": list(words)})
        return out

    def end_word():
        nonlocal cur
        if cur and cur["w"].strip():
            cur["w"] = cur["w"].strip()
            words.append(cur)
        cur = None

    def end_line():
        end_word()
        if words:
            out.append({"line": " ".join(w["w"] for w in words), "words": list(words)})
            words.clear()

    for i, (t, raw) in enumerate(events):
        nxt = events[i + 1][0] if i + 1 < len(events) else t + 1.0
        text = raw.replace("\r\n", "\n").replace("\r", "\n")
        if text.startswith(("/", "\\")):
            end_line()
            text = text[1:]
        if text.strip() == "" and "\n" in text:
            end_line()
            continue
        if text.startswith(" "):
            end_word()
        body = text.strip().strip("-").strip()
        if body:
            if cur is None:
                cur = {"w": "", "t0": t, "t1": nxt}
            cur["w"] += body
            cur["t1"] = min(nxt, t + 1.5)
        if text.endswith((" ", "\n")) or text.rstrip().endswith((",", ".", "!", "?")):
            end_word()
        if "\n" in text:
            end_line()
    end_line()
    return out


def _split_long(ls: list[dict], most: int = 9) -> list[dict]:
    """Subtitle-sized lines: a line of more than `most` words breaks at its longest pause."""
    out = []
    for ln in ls:
        stack = [ln["words"]]
        while stack:
            ws = stack.pop(0)
            if len(ws) <= most:
                out.append({"line": " ".join(w["w"] for w in ws), "words": ws})
                continue
            lo, hi = max(2, len(ws) // 4), min(len(ws) - 2, 3 * len(ws) // 4)
            k = max(range(lo, hi + 1), key=lambda i: ws[i]["t0"] - ws[i - 1]["t0"])
            stack[0:0] = [ws[:k], ws[k:]]
    return out


def melody_source(path: Path, events: list[tuple[float, str]]) -> dict:
    """Which channel (type-0 files) or track carries the words: the one whose note onsets fall on
    the lyric times. Returns {"channel": c} or {"track": i}, with the hit rate."""
    times = [t for t, _ in events]
    if not times:
        return {}
    best: dict = {}
    mf = mido.MidiFile(str(path))

    def rate(notes):
        if len(notes) < 20:
            return 0.0
        starts = sorted(n.start for n in notes)
        import bisect
        hit = 0
        for t in times:
            k = bisect.bisect_left(starts, t - 0.08)
            if k < len(starts) and starts[k] <= t + 0.08:
                hit += 1
        return hit / len(times)

    if mf.type == 0 or len(mf.tracks) == 1:
        chans = set()
        for m in mf.tracks[0]:
            if hasattr(m, "channel"):
                chans.add(m.channel)
        for c in sorted(chans - {9}):
            r = rate(midi.by_channel(path, c))
            if r > best.get("rate", 0):
                best = {"channel": c, "rate": round(r, 3)}
    else:
        for tr in midi.read(path):
            if 9 in tr.channels or not tr.notes:
                continue
            r = rate(tr.notes)
            if r > best.get("rate", 0):
                best = {"track": tr.index, "rate": round(r, 3)}
    return best
