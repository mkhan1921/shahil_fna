"""Small text utilities for discourse checks: numbers, hedges and segment rendering (spec §6.3, §7.2)."""

from __future__ import annotations

import re

from ..models.transcript import Segment

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1_000, "grand": 1_000, "bar": 1_000, "k": 1_000, "million": 1_000_000, "mil": 1_000_000, "billion": 1_000_000_000}
_DIGITS = re.compile(r"(?<![\w.])R?\s?(\d+(?:[ ,]\d{3})*(?:\.\d+)?)\s?(k|m|mil|bn)?\b", re.I)
_WORD = re.compile(r"[a-z]+", re.I)

# Hedges every rewrite must keep (§6.3). The lexicon's hedge list extends this when available.
HEDGES = (
    "maybe", "perhaps", "probably", "possibly", "might", "about", "around", "roughly", "approximately", "i think", "i guess",
    "i suppose", "not sure", "more or less", "give or take", "ish", "a bit", "a little", "sort of", "kind of", "could be",
    "thinking of", "hopefully", "ballpark", "something like", "or so", "omtrent", "sowat", "miskien",
)


def numbers_in(text: str) -> set[float]:
    """Numbers said in digits or English words, with scale words applied where they follow directly."""
    out: set[float] = set()
    for m in _DIGITS.finditer(text):
        raw = m.group(1).replace(" ", "").replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        suf = (m.group(2) or "").lower()
        out.add(v)
        if suf in ("k",):
            out.add(v * 1_000)
        elif suf in ("m", "mil"):
            out.add(v * 1_000_000)
        elif suf == "bn":
            out.add(v * 1_000_000_000)
    words = [w.lower() for w in _WORD.findall(text)]
    i = 0
    while i < len(words):
        w = words[i]
        if w in _UNITS or w in _TENS:
            total, current = 0.0, 0.0
            j = i
            while j < len(words) and (words[j] in _UNITS or words[j] in _TENS or words[j] in _SCALES or words[j] == "and"):
                x = words[j]
                if x in _UNITS:
                    current += _UNITS[x]
                elif x in _TENS:
                    current += _TENS[x]
                elif x == "hundred":
                    current = max(current, 1) * 100
                elif x in _SCALES:
                    total += max(current, 1) * _SCALES[x]
                    current = 0
                j += 1
            value = total + current
            out.add(value)
            # Every token on its own is a candidate too, and "two fifty" reads as 250 colloquially.
            run = words[i:j]
            for x in run:
                if x in _UNITS:
                    out.add(float(_UNITS[x]))
                elif x in _TENS:
                    out.add(float(_TENS[x]))
            for a, b in zip(run, run[1:]):
                if a in _UNITS and b in _TENS:
                    out.add(float(_UNITS[a] * 100 + _TENS[b]))
            i = j
        else:
            i += 1
    return out


def hedges_in(text: str, extra: tuple[str, ...] = ()) -> set[str]:
    t = " " + re.sub(r"\s+", " ", text.lower()) + " "
    return {h for h in HEDGES + extra if f" {h} " in t or f" {h}," in t or f" {h}." in t}


def clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def render_segment(seg: Segment, *, rewrite: str | None = None, low_conf: float = 0.6) -> str:
    """``[s0123 | client_primary | 14:02] <text> ⟨rewrite: …⟩`` with low-ASR words as ``{word?0.42}`` (§7.2)."""
    if seg.words and any(w.conf < low_conf for w in seg.words):
        text = " ".join(f"{{{w.text}?{w.conf:.2f}}}" if w.conf < low_conf else w.text for w in seg.words)
    else:
        text = seg.text
    out = f"[{seg.segment_id} | {seg.speaker_role} | {clock(seg.t_start)}] {text}"
    if rewrite and rewrite.strip() != seg.text.strip():
        out += f" ⟨rewrite: {rewrite}⟩"
    return out
