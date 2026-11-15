"""The Claude API (Anthropic Messages): subtitles, captions and comment replies.

Needs ANTHROPIC_API_KEY. CLAUDE_MODEL picks the model (default below)."""
from __future__ import annotations

import json
import os
import re
import urllib.request

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
FAST_MODEL = os.environ.get("CLAUDE_FAST_MODEL", "claude-haiku-5-5")

PERSONA = """You are the voice of mech.melody, an Instagram account where a band of wireframe robot heads
(physically simulated vocal tracts, no samples, no AI voice cloning) sing popular songs, some of them
playing instruments, with English subtitles. Tone: warm, playful, a little robotic and self-aware,
never cringe. Short. Emojis sparingly (at most one). Never claim to be human; you may say "we" (the band)."""


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def ask(prompt: str, system: str = PERSONA, model: str | None = None, max_tokens: int = 1500) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    body = json.dumps({"model": model or MODEL, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.load(r)
    return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text").strip()


def ask_json(prompt: str, **kw):
    text = ask(prompt + "\n\nAnswer with JSON only, no prose, no code fences.", **kw)
    m = re.search(r"[\[{].*[\]}]", text, re.S)
    return json.loads(m.group(0) if m else text)


def translate_lines(lines: list[str], language: str, spec: dict) -> list[str]:
    """Singable, natural English subtitle lines, one per lyric line (same count, same order)."""
    if not available():
        print("warning: no ANTHROPIC_API_KEY: subtitles stay in the original language")
        return lines
    clean = [re.sub(r"[|_]", "", ln).strip() for ln in lines]
    out = ask_json(
        f"Translate these song lyric lines from language code '{language}' into natural English subtitles "
        f"for a video of the song \"{spec.get('title')}\" by {spec.get('artist', 'unknown')}. "
        f"Keep the meaning and feel; keep each line short enough to read in a few seconds. "
        f"Return a JSON array of exactly {len(clean)} strings, one per input line, same order.\n\n"
        + json.dumps(clean, ensure_ascii=False),
        system="You are a professional subtitle translator.",
    )
    if not isinstance(out, list) or len(out) != len(clean):
        print("warning: translation came back with the wrong number of lines; using the original lines")
        return lines
    return [str(x) for x in out]


def caption(show: dict, requested_by: list[str]) -> str:
    voices = [p for p in show["parts"] if p["kind"] == "voice"]
    players = [p for p in show["parts"] if p["kind"] == "instrument"]
    if not available():
        tags = "#robot #cover #vocalsynth #mechmelody #music"
        return f"🤖 {show['title']} — {show['artist']}\n{len(voices)} robot voices, {len(players)} robot players.\n\n{tags}"
    return ask(
        "Write the Instagram Reel caption for our new video.\n"
        f"Song: {show['title']} by {show['artist']} (sung in language '{show['language']}', English subtitles on screen).\n"
        f"Band: {len(voices)} singing robots ({', '.join(v['body'] for v in voices)} voices)"
        + (f", plus robots on {', '.join(p['instrument'] for p in players)}" if players else "")
        + ".\n"
        + (f"Requested in the comments by: {', '.join('@' + u for u in requested_by)} (thank them).\n" if requested_by else "")
        + "Rules: hook in the first line; credit the original artist clearly and say it is a robot cover; "
        "invite people to request the next song in the comments; 2-4 short lines, then exactly 5 hashtags "
        "(Instagram allows no more than 5): the song, the artist or genre, the season or trend if any, "
        "#robot and #cover. No em dashes. Plain text only.",
        max_tokens=600,
    )


def triage_comment(comment: str, username: str, post_title: str, queue_titles: list[str]) -> dict:
    """What a comment is and what to reply. Returns {kind, song, artist, reply}."""
    return ask_json(
        f"A comment on our Reel of \"{post_title}\" from @{username}:\n\"\"\"{comment}\"\"\"\n\n"
        f"Songs already in our request queue: {json.dumps(queue_titles[:60], ensure_ascii=False)}\n\n"
        "Classify it and draft our reply. JSON object with keys:\n"
        "  kind: one of request | praise | question | criticism | spam | abuse | other\n"
        "  song: for a request, the song title as commonly written (else null)\n"
        "  artist: for a request, the artist (else null; guess only if you are sure)\n"
        "  reply: our reply, in the commenter's language. Keep it very short and warm: usually a few words "
        "plus an emoji (e.g. 'Thank you! 🤖🎶'), sometimes only emojis for a simple compliment. Never more than one sentence. "
        "For a request: always positive, e.g. that the robots are on it / it is in the queue (or already queued, their vote counts). "
        "For a question: a short honest answer (the voices are physical simulations of a human airway, not samples or AI voice clones). "
        "For criticism: kind and brief, never defensive. For spam or abuse: null (we do not answer).",
        model=FAST_MODEL,
        max_tokens=400,
    )
