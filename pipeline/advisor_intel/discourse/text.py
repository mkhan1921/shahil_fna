"""Small text utilities for discourse checks: numbers, hedges and segment rendering (spec §6.3, §7.2)."""

from __future__ import annotations

import re
from functools import lru_cache
from itertools import pairwise

from ..models.transcript import Segment

_UNITS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {
    "hundred": 100,
    "thousand": 1_000,
    "grand": 1_000,
    "bar": 1_000,
    "k": 1_000,
    "million": 1_000_000,
    "mil": 1_000_000,
    "billion": 1_000_000_000,
}
_FRACTIONS = {"half": 0.5, "quarter": 0.25, "third": 1 / 3}
_ARTICLES = ("a", "an")
# A number in digits: "45 000", "2,500", "1.5", optionally with a rand marker. Scale suffixes and words
# ("1.5k", "R2.5 million", "250 thousand") are read from what follows the digits.
_NUMBER = re.compile(r"(?<![\w.])R?\s?(\d+(?:[ ,]\d{3})*(?:\.\d+)?)", re.IGNORECASE)
_AFTER_NUMBER = re.compile(r"^\s*(k|m|bn|mil|hundred|thousand|grand|bar|million|billion)\b", re.IGNORECASE)
_SUFFIX_SCALE = {"k": 1_000, "m": 1_000_000, "bn": 1_000_000_000, **_SCALES}
_WORD = re.compile(r"[a-z]+", re.IGNORECASE)

# Hedges every rewrite must keep (§6.3). The lexicon's hedge list extends this when available.
HEDGES = (
    "maybe",
    "perhaps",
    "probably",
    "possibly",
    "might",
    "about",
    "around",
    "roughly",
    "approximately",
    "i think",
    "i guess",
    "i suppose",
    "not sure",
    "more or less",
    "give or take",
    "ish",
    "a bit",
    "a little",
    "sort of",
    "kind of",
    "could be",
    "thinking of",
    "hopefully",
    "ballpark",
    "something like",
    "or so",
    "omtrent",
    "sowat",
    "miskien",
)


def numbers_in(text: str) -> set[float]:
    """Numbers said in digits or English words, with scale words applied where they follow directly.

    "R2.5 million" → {2.5, 2 500 000}; "a hundred and fifty thousand" → {150 000, …};
    "one point two million" → {1 200 000, …}; "two and a half" → {2.5, …}. Every token of a spoken
    number is a candidate on its own as well, and "two fifty" also reads as 250, so the
    no-new-numbers check (§6.3) accepts any reading of what was said.
    """
    out: set[float] = set()
    for m in _NUMBER.finditer(text):
        raw = m.group(1).replace(" ", "").replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        out.add(v)
        after = _AFTER_NUMBER.match(text[m.end(1) :])
        if after:
            out.add(v * _SUFFIX_SCALE[after.group(1).lower()])
    words = [w.lower() for w in _WORD.findall(text)]
    n = len(words)
    i = 0
    while i < n:
        w = words[i]
        starts = w in _UNITS or w in _TENS or (w in _ARTICLES and i + 1 < n and words[i + 1] in _SCALES)
        if not starts:
            i += 1
            continue
        total = 0.0
        current = 0.0
        decimal: str | None = None  # digits spoken after "point"
        j = i
        while j < n:
            x = words[j]
            nxt = words[j + 1] if j + 1 < n else ""
            if x in _UNITS:
                if decimal is not None:
                    if _UNITS[x] > 9:
                        break
                    decimal += str(_UNITS[x])
                else:
                    current += _UNITS[x]
            elif x in _TENS:
                if decimal is not None:
                    break
                current += _TENS[x]
            elif x in _ARTICLES:
                if nxt in _SCALES:
                    current = max(current, 1.0)
                elif nxt not in _FRACTIONS:
                    break
            elif x == "and":
                pass
            elif x == "point":
                if decimal is not None:
                    break
                decimal = ""
            elif x in _FRACTIONS:
                if j > i and words[j - 1] in _ARTICLES:
                    current += _FRACTIONS[x]
                else:
                    break
            elif x == "hundred":
                current = max(current, 1.0) * 100
            elif x in _SCALES:
                base = current + (float("0." + decimal) if decimal else 0.0)
                if decimal:
                    out.add(base)  # "one point two million" is also written "1.2 million"
                total += (base or 1.0) * _SCALES[x]
                current = 0.0
                decimal = None
            else:
                break
            j += 1
        value = total + current + (float("0." + decimal) if decimal else 0.0)
        out.add(value)
        # Every token on its own is a candidate too, and "two fifty" reads as 250 colloquially.
        run = words[i:j]
        for x in run:
            if x in _UNITS:
                out.add(float(_UNITS[x]))
            elif x in _TENS:
                out.add(float(_TENS[x]))
        for a, b in pairwise(run):
            if a in _UNITS and b in _TENS:
                out.add(float(_UNITS[a] * 100 + _TENS[b]))
        i = max(j, i + 1)
    return out


@lru_cache(maxsize=512)
def _hedge_pattern(hedge: str) -> re.Pattern[str]:
    # Letter boundaries rather than spaces: "maybe?" and "65ish" count, "Spanish" and "roundabout" do not.
    return re.compile(r"(?<![a-z])" + re.escape(hedge.lower()).replace(r"\ ", r"\s+") + r"(?![a-z])")


def hedges_in(text: str, extra: tuple[str, ...] = ()) -> set[str]:
    t = re.sub(r"\s+", " ", text.lower())
    return {h for h in HEDGES + tuple(extra) if h and _hedge_pattern(h).search(t)}


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
