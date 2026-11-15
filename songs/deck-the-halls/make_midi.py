"""Deck the Halls (Welsh melody, English words by T. Oliphant 1862; public domain), in F.
Call and response: the lead sings the lines, a three-part robot choir answers "fa la la".
Run from the repo root: python3 songs/deck-the-halls/make_midi.py"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mech.compose import Arrangement, melody, third_below, chord  # noqa: E402

A = Arrangement(bpm=138, beats_per_bar=4)
TEXT = {
    1: "C5/1.5 Bb4/.5 A4/1 G4/1 | F4/1 G4/1 A4/1 F4/1",
    3: "G4/1.5 A4/.5 Bb4/1 G4/1 | A4/1.5 Bb4/.5 C5/1 G4/1",
}
TEXT[2] = TEXT[1]
TEXT[4] = TEXT[1]
FALA = {
    1: "G4/.5 A4/.5 Bb4/.5 G4/.5 A4/1.5 G4/.5 | F4/1 E4/1 F4/2",
    3: "A4/.5 B4/.5 C5/1 D5/.5 E5/.5 F5/1 | E5/1 D5/1 C5/2",
    4: "D5/.5 D5/.5 D5/.5 D5/.5 C5/1.5 Bb4/.5 | A4/1 G4/1 F4/2",
}
FALA[2] = FALA[1]
CHORDS = ["F", "F", "C7", "F", "F", "F", "C7", "F", "C7", "F", "G7", "C", "F", "F", "Bb", "F"]
F_MAJOR = [65, 67, 69, 70, 72, 74, 76]
intro = 2 * 4
for verse in range(2):
    v0 = intro + verse * 16 * 4
    for line in range(1, 5):
        t = v0 + (line - 1) * 16
        A.notes("Lead", melody(TEXT[line]), t, vel=100 + 4 * verse)
        fala = melody(FALA[line])
        A.notes("Choir high", fala, t + 8, vel=90)
        A.notes("Choir mid", third_below(fala, F_MAJOR), t + 8, vel=82)
        A.notes("Choir low", third_below(third_below(fala, F_MAJOR), F_MAJOR), t + 8, vel=80)
chords = CHORDS * 2
A.comp("Bells", ["F", "C7"], 0, style="bells", octave=4, vel=84, program=9, channel=3)
A.comp("Piano", ["F", "C7"] + chords, 0, style="pop", octave=3, vel=72)
A.bassline("Bass", ["F", "C7"] + chords, 0, style="pop")
A.drums("Drums", 2 + len(chords), 0, style="pop", jingle=True)
end = intro + len(chords) * 4
A.hold("Drums", 49, end, 2, 110, 9)
A.hold("Drums", 36, end, 0.3, 120, 9)
A.hold("Bass", 41, end, 3.5, 96, 2, 33)
for p in chord("F", 4):
    A.hold("Piano", p, end, 3.5, 80, 1, 0)
A.save(Path(__file__).with_name("deck-the-halls.mid"))
print("wrote deck-the-halls.mid")
