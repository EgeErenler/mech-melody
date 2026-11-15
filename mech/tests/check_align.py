"""listen.align keeps one phrase per lyric line.

Bug: lang.syllables() returns each single word as a whole phrase (phraseEnd on its last
syllable), so every word ended a phrase and the singer breathed between words; sung lines
came out choppy and hard to follow. Run: python3 -m mech.tests.check_align
"""
from mech import listen

line = {"line": "sing a little song for me", "words": [
    {"w": w, "t0": 1.0 + i * 0.5, "t1": 1.45 + i * 0.5} for i, w in enumerate("sing a little song for me".split())]}
notes = [listen.N(1.0 + i * 0.5, 1.45 + i * 0.5, 60 + i, 90) for i in range(6)]
notes.insert(3, listen.N(2.25, 2.45, 63, 90))  # "lit-tle": two syllables, two notes
notes.sort(key=lambda n: n.start)
_, syls = listen.align([line], notes, "en")
ends = [s["text"] for s in syls if s["phraseEnd"]]
assert ends == [syls[-1]["text"]], f"phrase ends mid-line: {ends}"
print("ok:", len(syls), "syllables, one phrase")
