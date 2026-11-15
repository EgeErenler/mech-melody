"""Lyrics -> CHIMERA syllables for any language.

English goes through CHIMERA's own front end (`chimera syllables`, CMUdict). Turkish, Spanish and
French (and any other language espeak-ng knows) are read by espeak-ng into IPA, mapped onto the
engine's phoneme inventory, and split into syllables here. The result is the engine's "one format"
(docs/language-front-ends.md): one dict per sung syllable.

Lyrics conventions (all languages):
  - a line break or , . ! ? ; ends a phrase (the singer breathes there)
  - `_` on its own sings the previous syllable on one more note (melisma)
  - `word|word` forces a syllable split where you want it in one word: `ca|ri|ño`
"""
from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from functools import lru_cache

from .paths import CHIMERA

ESPEAK_VOICE = {"tr": "tr", "es": "es-419", "fr": "fr-fr", "de": "de", "it": "it", "pt": "pt-br"}

VOWELS = ["i", "ɪ", "e", "ɛ", "æ", "a", "ɑ", "ɒ", "ɔ", "o", "ʊ", "u", "ʌ", "ə", "ɐ", "ɝ", "y", "ø", "œ", "ɨ", "ɯ", "ɤ"]
CONSONANTS = (
    "p b t d ʈ ɖ c ɟ k g q ɢ ʔ m ɱ n ɳ ɲ ŋ ɴ ɸ β f v θ ð s z ʃ ʒ ʂ ʐ ç ʝ x ɣ χ ʁ ħ ʕ h ɦ ɬ ɮ "
    "ts dz tʃ dʒ ɕ ʑ tɕ dʑ tʂ dʐ pf w ʋ ɹ ɻ j ɥ ɰ l ɫ ɭ ʎ ɾ ɽ r ʀ ʙ"
).split()

# espeak symbols the engine does not have, onto the nearest it does
REMAP = {
    "ɫ": "l", "ɡ": "g", "ɚ": "ə", "ɜ": "ɛ", "ä": "a", "ɘ": "ə", "ɵ": "ø", "ʏ": "y", "ɞ": "œ",
}
# French nasal vowels: the oral vowel with a velar nasal after it
NASAL = {"ɑ̃": ("ɑ", "ŋ"), "ɔ̃": ("o", "ŋ"), "ɛ̃": ("ɛ", "ŋ"), "œ̃": ("œ", "ŋ"), "ã": ("a", "ŋ"), "õ": ("o", "ŋ"), "ẽ": ("e", "ŋ"), "ĩ": ("i", "ŋ"), "ũ": ("u", "ŋ")}

OBSTRUENTS = set("p b t d k g f v".split())
LIQUIDS = set("l ɾ r ʁ".split())
GLIDES = {"i": "j", "u": "w", "y": "ɥ"}

PHRASE_PUNCT = ",.!?;:…"


def _espeak_ipa(text: str, lang: str) -> str:
    voice = ESPEAK_VOICE.get(lang, lang)
    out = subprocess.run(["espeak-ng", "-q", "--ipa", "-v", voice, text], capture_output=True, text=True, check=True).stdout
    out = re.sub(r"\([a-z-]+\)", "", out)  # espeak's language-switch tags, "(en)dˈɪŋ(fr)"
    return " ".join(out.split())


def _tokenize(ipa: str) -> list[dict]:
    """IPA of one word -> segments [{ipa, vowel, stress}] on the engine's inventory."""
    ipa = unicodedata.normalize("NFC", ipa)
    for k in ("ː", "ˑ", "-", "͡", "‿", "'"):
        ipa = ipa.replace(k, "")
    segs: list[dict] = []
    stress = 0
    i = 0
    by_len = sorted(set(VOWELS) | set(CONSONANTS) | set(NASAL) | set(REMAP), key=len, reverse=True)
    while i < len(ipa):
        ch = ipa[i]
        if ch in "ˈ":
            stress = 1; i += 1; continue
        if ch in "ˌ":
            stress = 2; i += 1; continue
        # a nasal vowel written with a combining tilde
        if i + 1 < len(ipa) and ipa[i + 1] == "̃":
            key = ch + "̃"
            if key in NASAL:
                v, n = NASAL[key]
                segs.append({"ipa": v, "vowel": True, "stress": stress}); stress = 0
                segs.append({"ipa": n, "vowel": False, "stress": 0, "nasal_coda": True})
                i += 2; continue
        for sym in by_len:
            if ipa.startswith(sym, i):
                s = REMAP.get(sym, sym)
                if sym in NASAL:
                    v, n = NASAL[sym]
                    segs.append({"ipa": v, "vowel": True, "stress": stress}); stress = 0
                    segs.append({"ipa": n, "vowel": False, "stress": 0, "nasal_coda": True})
                else:
                    segs.append({"ipa": s, "vowel": s in VOWELS, "stress": stress if s in VOWELS else 0})
                    if s in VOWELS:
                        stress = 0
                i += len(sym)
                break
        else:
            i += 1  # a symbol the engine has no sound for: skipped, as the engine itself does
    return segs


def _glides(segs: list[dict], lang: str) -> list[dict]:
    """Spanish/French/Italian/Portuguese: an unstressed i/u next to another vowel is a glide (ai-re -> aj.ɾe)."""
    if lang not in ("es", "fr", "it", "pt"):
        return segs
    out = [dict(s) for s in segs]
    for s in out:  # espeak writes some Romance vowels with English symbols
        if s["vowel"] and s["ipa"] in ("ɪ", "ʊ"):
            s["ipa"] = {"ɪ": "i", "ʊ": "u"}[s["ipa"]]
    for k, s in enumerate(out):
        if not s["vowel"] or s["ipa"] not in GLIDES or s["stress"]:
            continue
        prev_v = k > 0 and out[k - 1]["vowel"]
        next_v = k + 1 < len(out) and out[k + 1]["vowel"]
        if prev_v or next_v:
            s["ipa"] = GLIDES[s["ipa"]]
            s["vowel"] = False
    return out


def _syllabify(segs: list[dict]) -> list[dict]:
    nuclei = [k for k, s in enumerate(segs) if s["vowel"]]
    if not nuclei:
        return []
    syls = [{"onset": [], "nucleus": segs[k], "coda": []} for k in nuclei]
    syls[0]["onset"] = segs[: nuclei[0]]
    syls[-1]["coda"] = segs[nuclei[-1] + 1 :]
    for a, (i, j) in enumerate(zip(nuclei, nuclei[1:])):
        mid = segs[i + 1 : j]
        if not mid:
            split = 0
        elif mid[0].get("nasal_coda"):
            split = 1 if len(mid) > 1 else 0
            if len(mid) == 1:
                split = 1  # a nasal vowel's ŋ stays on its own syllable
        elif len(mid) >= 2 and mid[-2]["ipa"] in OBSTRUENTS and mid[-1]["ipa"] in LIQUIDS:
            split = len(mid) - 2
        else:
            split = len(mid) - 1
        syls[a]["coda"] = mid[:split]
        syls[a + 1]["onset"] = mid[split:]
    return syls


def _seg_out(s: dict) -> dict:
    return {"ipa": s["ipa"], "stress": s.get("stress", 0)}


_LETTER_VOWELS = set("aeiouyáéíóúàèìòùâêîôûäëïöüıİœæãõåøAEIOUYÁÉÍÓÚÀÈÌÒÙÂÊÎÔÛÄËÏÖÜ")


def _spell_split(word: str, n: int) -> list[str]:
    """Split a written word into n pieces for the words shown at the mouth: by vowel groups when
    that gives n pieces (Turkish, Spanish), else into near-equal runs of letters (French's silent letters)."""
    if n <= 1:
        return [word]
    groups = [m.start() for m in re.finditer(r"[%s]+" % "".join(_LETTER_VOWELS), word)]
    if len(groups) == n:
        cuts = [0]
        for g in groups[1:]:
            # one consonant before the vowel group goes with it
            c = g - 1 if g - 1 > cuts[-1] and word[g - 1] not in _LETTER_VOWELS else g
            cuts.append(c)
        cuts.append(len(word))
        return [word[a:b] for a, b in zip(cuts, cuts[1:])]
    size = len(word) / n
    cuts = [round(k * size) for k in range(n)] + [len(word)]
    return [word[a:b] or word[a:a + 1] for a, b in zip(cuts, cuts[1:])]


@lru_cache(maxsize=4096)
def _word_syllables(word: str, lang: str, forced: bool = False) -> tuple:
    parts = word.split("|")
    if len(parts) > 1:
        out = []
        for p in parts:
            out.extend(_word_syllables(p, lang, True))
        # one word: only the last piece ends it
        out = [dict(s, wordEnd=False) for s in out]
        out[-1]["wordEnd"] = True
        return tuple(out)
    ipa = _espeak_ipa(word, lang).replace(" ", "")
    segs = _glides(_tokenize(ipa), lang)
    if forced and segs and not any(x["vowel"] for x in segs):
        segs.append({"ipa": "ə", "vowel": True, "stress": 0})  # a sung mute e: "ma|ti|nes"
    syls = _syllabify(segs)
    if not syls:
        return tuple()
    texts = _spell_split(word, len(syls))
    out = []
    for k, (s, t) in enumerate(zip(syls, texts)):
        out.append({
            "text": t,
            "onset": [_seg_out(x) for x in s["onset"]],
            "nucleus": _seg_out(s["nucleus"]),
            "coda": [_seg_out(x) for x in s["coda"]],
            "wordEnd": k == len(syls) - 1,
            "phraseEnd": False,
        })
    return tuple(out)


def melisma_of(prev: dict) -> dict:
    """One more note on `prev`'s vowel: the coda moves to the end of the run."""
    m = dict(prev, melisma=True, onset=[], coda=prev["coda"], phraseEnd=prev.get("phraseEnd", False))
    prev["coda"] = []
    prev["phraseEnd"] = False
    prev["wordEnd"] = False
    return m


def _english_line(line: str) -> list[dict]:
    out = subprocess.run([str(CHIMERA), "syllables", line], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def lyric_lines(lyrics: str) -> list[str]:
    return [ln.strip() for ln in lyrics.strip().splitlines() if ln.strip()]


def syllables(lyrics: str, lang: str) -> list[dict]:
    """All sung syllables of the lyrics, in order. Each carries `line` (its lyric line's index)."""
    result: list[dict] = []
    for li, line in enumerate(lyric_lines(lyrics)):
        line_syls: list[dict] = []
        for tok in re.findall(r"_|[^\s_]+", line):
            if tok == "_":
                if line_syls or result:
                    line_syls.append(melisma_of((line_syls or result)[-1]))
                continue
            word = tok.strip(PHRASE_PUNCT + "\"'()¡¿«»“”")
            ends_phrase = any(tok.endswith(p) for p in PHRASE_PUNCT)
            if not word:
                if ends_phrase and line_syls:
                    line_syls[-1]["phraseEnd"] = True
                continue
            if lang == "en":
                ws = _english_line(word.replace("|", ""))
            else:
                ws = [dict(s) for s in _word_syllables(word, lang)]
            if ws and ends_phrase:
                ws[-1]["phraseEnd"] = True
            line_syls.extend(ws)
        if line_syls:
            line_syls[-1]["phraseEnd"] = True
        for s in line_syls:
            s["line"] = li
        result.extend(line_syls)
    return result


if __name__ == "__main__":
    import sys
    lang, text = sys.argv[1], sys.argv[2]
    for s in syllables(text, lang):
        print(s["text"], "|", "".join(x["ipa"] for x in s["onset"]), s["nucleus"]["ipa"], "".join(x["ipa"] for x in s["coda"]), "#" if s["phraseEnd"] else "")
