"""Which sung words can a listener make out? Whisper (small.en, greedy) transcribes the lead
voice alone (public/shows/<slug>/v0.wav) and the transcript is matched to the words the lead
was given (v0.melody.json). Prints, per song, the share of words heard and each lost word with
its time and note length, so a fix can aim at the notes that swallow words.

usage: sung_words.py <slug>... (after `python3 -m mech build <slug>`)
Writes out/judge/<slug>.json. The lyric text stays in the job log and that file (private repo).
"""

import difflib
import json
import os
import re
import sys

import numpy as np
import soundfile as sf
from faster_whisper import WhisperModel
from scipy.signal import resample_poly

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
from mech import build  # noqa: E402


def norm(w: str) -> str:
    return re.sub(r"[^a-z']", "", w.lower().replace("’", "'"))


def expected(slug: str) -> list[dict]:
    m = json.load(open(f"{REPO}/public/shows/{slug}/v0.melody.json"))
    t, notes = m.get("t0", 0.0), []
    for p, d in m["notes"]:
        if p > 0:
            notes.append((t, d))
        t += d
    words, cur = [], None
    for (t0, d), s in zip(notes, m["syllables"]):
        if cur is None:
            cur = {"w": "", "t": round(t0, 2), "shortest": d, "notes": 0}
        if not s.get("melisma"):
            cur["w"] += s["text"].strip("~-")
        cur["shortest"] = min(cur["shortest"], d)
        cur["notes"] += 1
        if s.get("wordEnd"):
            cur["w"] = norm(cur["w"])
            if cur["w"]:
                words.append(cur)
            cur = None
    return words


def main(slugs: list[str]) -> None:
    model = WhisperModel("small.en", device="cpu", compute_type="int8")
    os.makedirs(f"{REPO}/out/judge", exist_ok=True)
    for slug in slugs:
        want = expected(slug)
        x, sr = sf.read(f"{REPO}/public/shows/{slug}/v0.wav", dtype="float32", always_2d=True)
        x = build._ride(x)[:, 0]  # the lead as it sits in the mix (leveled)
        x = resample_poly(x, 16000, sr).astype(np.float32)  # Whisper wants 16 kHz mono
        segs, _ = model.transcribe(x, beam_size=1, language="en",
                                   condition_on_previous_text=False, vad_filter=False)
        heard = [norm(w) for s in segs for w in s.text.split() if norm(w)]
        sm = difflib.SequenceMatcher(a=[w["w"] for w in want], b=heard, autojunk=False)
        ok = set()
        for a, _, n in sm.get_matching_blocks():
            ok.update(range(a, a + n))
        lost = [dict(w, i=i) for i, w in enumerate(want) if i not in ok]
        share = 100 * len(ok) / max(1, len(want))
        print(f"\n== {slug}: {share:.0f}% of {len(want)} words heard")
        for w in lost:
            print(f"  lost {w['t']:6.2f}s  {w['w']:<14} notes {w['notes']}  shortest {w['shortest'] * 1000:.0f} ms")
        json.dump({"heard_pct": share, "lost": lost, "transcript": " ".join(heard)},
                  open(f"{REPO}/out/judge/{slug}.json", "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
