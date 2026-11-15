"""Jingle Bells (James Lord Pierpont, 1857; public domain): the chorus twice, in G.
Run from the repo root: python3 songs/jingle-bells/make_midi.py"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mech.compose import Arrangement, melody, third_below, chord  # noqa: E402

A = Arrangement(bpm=148, beats_per_bar=4)
L1 = "B4/1 B4/1 B4/2 | B4/1 B4/1 B4/2 | B4/1 D5/1 G4/1.5 A4/.5 | B4/4"
L2 = "C5/1 C5/1 C5/1.5 C5/.5 | C5/1 B4/1 B4/1 B4/.5 B4/.5 | B4/1 A4/1 A4/1 B4/1 | A4/2 D5/2"
L4 = "C5/1 C5/1 C5/1.5 C5/.5 | C5/1 B4/1 B4/1 B4/.5 B4/.5 | D5/1 D5/1 C5/1 A4/1 | G4/4"
CHORUS = melody(" ".join([L1, L2, L1, L4]))
CHORDS = ["G", "G", "G", "G", "C", "G", "A7", "D7", "G", "G", "G", "G", "C", "G", "D7", "G"]
G_MAJOR = [67, 69, 71, 72, 74, 76, 78]
intro = 2 * 4
c1, c2 = intro, intro + 16 * 4
end = c2 + 16 * 4

A.notes("Lead", CHORUS, c1, vel=100)
A.notes("Lead", CHORUS, c2, vel=104)
A.notes("Harmony", third_below(CHORUS, G_MAJOR), c2, vel=84)
# the choir: held "oo" chords under both choruses (root, from the bass of the chord)
for i, c in enumerate(CHORDS * 2):
    A.hold("Choir", chord(c, 3)[0], c1 + i * 4, 3.9, 76)
A.hold("Choir", chord("G", 3)[0], end, 7.5, 80)
# the band
A.comp("Bells", ["G", "D7"], 0, style="bells", octave=4, vel=84, program=9, channel=3)
A.notes("Bells", [(p + 12 if p else None, b) for p, b in CHORUS], c2, vel=70, channel=3, program=9)
A.comp("Piano", ["G", "D7"] + CHORDS * 2 + ["G", "G"], 0, style="pop", octave=3, vel=70)
A.bassline("Bass", ["G", "D7"] + CHORDS * 2 + ["G"], 0, style="pop")
A.hold("Bass", chord("G", 1)[0] + 12, end + 4, 3.5, 96, 2, 33)
A.drums("Drums", 2 + 32, 0, style="pop", jingle=True)
A.hold("Drums", 36, end, 0.3, 120, 9)
A.hold("Drums", 49, end, 2, 110, 9)
A.save(Path(__file__).with_name("jingle-bells.mid"))
print("wrote jingle-bells.mid")
