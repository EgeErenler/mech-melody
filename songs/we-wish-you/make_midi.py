"""We Wish You a Merry Christmas (traditional English carol; public domain), in G, 3/4.
Run from the repo root: python3 songs/we-wish-you/make_midi.py"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mech.compose import Arrangement, melody, third_below, chord  # noqa: E402

A = Arrangement(bpm=132, beats_per_bar=3)
SEC_A = ("D4/1 | G4/1 G4/.5 A4/.5 G4/.5 F#4/.5 | E4/1 E4/1 E4/1 | A4/1 A4/.5 B4/.5 A4/.5 G4/.5 | F#4/1 D4/1 D4/1 | "
         "B4/1 B4/.5 C5/.5 B4/.5 A4/.5 | G4/1 E4/1 D4/.5 D4/.5 | E4/1 A4/1 F#4/1 | G4/2")
SEC_B = "D4/1 | G4/1 G4/1 G4/1 | F#4/2 F#4/1 | G4/1 F#4/1 E4/1 | D4/2 A4/1 | B4/1 A4/1 G4/1 | D5/1 D4/1 D4/.5 D4/.5 | E4/1 A4/1 F#4/1 | G4/2"
CH_A = ["G", "C", "A7", "D", "B7", "Em", "D7", "G"]
CH_B = ["G", "D", "Em", "D", "G", "G", "D7", "G"]
G_MAJOR = [67, 69, 71, 72, 74, 76, 78]
a, b = melody(SEC_A), melody(SEC_B)
last = a[:-1] + [(a[-1][0], 3)]
intro = 2 * 3
start = intro - 1  # the pickup "We" on the last beat of the intro
song = a + b + a + b + last
A.notes("Lead", song, start, vel=100)
half = len(a) + len(b) + len(a)
t_half = start + sum(x[1] for x in a + b + a)
A.notes("Harmony", third_below(b + last, G_MAJOR), t_half, vel=84)
chords = CH_A + CH_B + CH_A + CH_B + CH_A
for i, c in enumerate(chords):
    A.hold("Choir", chord(c, 3)[0], intro + i * 3, 2.9, 74)
A.comp("Strings", chords, intro, style="pad", octave=3, vel=60, program=48, channel=4)
A.comp("Piano", ["G", "D7"] + chords, 0, style="waltz", octave=3, vel=72)
A.bassline("Bass", ["G", "D7"] + chords, 0, style="waltz")
A.drums("Drums", 2 + len(chords), 0, style="waltz", jingle=True)
end = intro + len(chords) * 3
A.hold("Drums", 49, end, 2, 110, 9)
A.hold("Bass", 43, end, 2.8, 96, 2, 33)
A.save(Path(__file__).with_name("we-wish-you.mid"))
print("wrote we-wish-you.mid,", len(song), "lead notes")
