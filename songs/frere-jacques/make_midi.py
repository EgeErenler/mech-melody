"""Writes frere-jacques.mid: the traditional round (public domain) for three singers,
guitar, bass and drums. Run: python3 songs/frere-jacques/make_midi.py"""
from pathlib import Path
import mido

BPM, TPB = 108, 480
Q = TPB
# (midi, beats) — in F major
TUNE = [(65, 1), (67, 1), (69, 1), (65, 1)] * 2 + [(69, 1), (70, 1), (72, 2)] * 2 \
     + [(72, .5), (74, .5), (72, .5), (70, .5), (69, 1), (65, 1)] * 2 + [(65, 1), (60, 1), (65, 2)] * 2
BARS_PER_TUNE = 8
REPEATS = 3
TOTAL_BARS = BARS_PER_TUNE * REPEATS + 4  # the round's tail


def track(name, events, channel=0, program=0):
    t = mido.MidiTrack()
    t.append(mido.MetaMessage("track_name", name=name, time=0))
    t.append(mido.Message("program_change", program=program, channel=channel, time=0))
    ev = []
    for start, dur, note, vel in events:
        ev.append((int(start * Q), 1, note, vel))
        ev.append((int((start + dur) * Q) - 5, 0, note, 0))
    ev.sort(key=lambda e: (e[0], e[1]))
    now = 0
    for tick, on, note, vel in ev:
        t.append(mido.Message("note_on" if on else "note_off", note=note, velocity=vel, channel=channel, time=tick - now))
        now = tick
    return t


def voice(entry_bar, reps, vel):
    ev, b = [], entry_bar * 4
    for _ in range(reps):
        for note, beats in TUNE:
            if b < TOTAL_BARS * 4 - 4:
                ev.append((b, beats * 0.95, note, vel))
            b += beats
    return ev


mid = mido.MidiFile(ticks_per_beat=TPB)
meta = mido.MidiTrack()
meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(BPM), time=0))
meta.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
mid.tracks.append(meta)
mid.tracks.append(track("Voice 1", voice(0, 3, 100)))
mid.tracks.append(track("Voice 2", voice(2, 3, 90)))
mid.tracks.append(track("Voice 3", voice(4, 3, 85)))

guitar, bass, drums = [], [], []
for bar in range(TOTAL_BARS - 2):
    b = bar * 4
    root = 53 if bar % 2 == 0 else 48  # F, C
    chord = [53, 57, 60, 65] if root == 53 else [48, 55, 60, 64]
    for beat, length in ((0, 1.5), (1.5, 1), (2.5, 0.5), (3, 1)):
        for k, n in enumerate(chord):
            guitar.append((b + beat + 0.015 * k, length, n, 80 if beat == 0 else 64))
    bass += [(b, 1.8, root - 12, 100), (b + 2, 1.8, root - 5, 90)]
    for beat in range(4):
        drums.append((b + beat, .2, 36 if beat in (0, 2) else 38, 110 if beat in (0, 2) else 95))
        drums += [(b + beat, .1, 42, 60), (b + beat + .5, .1, 42, 45)]
    if bar % 8 == 0:
        drums.append((b, 1, 49, 90))
end = (TOTAL_BARS - 2) * 4
guitar += [(end + 0.02 * k, 4, n, 80) for k, n in enumerate([53, 57, 60, 65])]
bass.append((end, 4, 41, 100))
drums += [(end, 1, 36, 120), (end, 2, 49, 110)]
mid.tracks.append(track("Guitar", guitar, 1, 25))
mid.tracks.append(track("Bass", bass, 2, 33))
mid.tracks.append(track("Drums", drums, 9, 0))
mid.save(Path(__file__).with_name("frere-jacques.mid"))
print("wrote frere-jacques.mid")
