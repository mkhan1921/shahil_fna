"""Token normalisation shared by the grammars, the span finder and the metrics (§5.3).

The grammars run on lowercase tokens separated by single spaces. Normalisation is deliberately small:
lowercase, unify quotes and dashes, drop sentence punctuation at the edges of a token, collapse dotted
abbreviations ("p.m." → "pm", "R.A." → "ra") and split letter-hyphen-letter compounds ("twenty-five",
"twee-honderd", "mid-fifties") into words. Digit punctuation inside a token ("1,2", "250,000", "11.5%")
is kept, because the grammar reads it.
"""

from __future__ import annotations

import re
import unicodedata

FILLERS = frozenset({"uh", "um", "uhm", "umm", "er", "erm", "ah", "eh", "hmm", "mm", "mmm", "ehm"})

_ABBREV = re.compile(r"^(?:[a-z]\.){1,5}[a-z]?\.?$")
_EDGE = ".,;:!?\"“”()[]{}<>…*_|"
_LETTER_HYPHEN = re.compile(r"(?<=[^\W\d_])[-‐‑–—](?=[^\W\d_'])")
_DASHES = re.compile(r"[‐‑–—]+")
_UNSAFE = re.compile(r"[\[\]\\]")  # special to pynini's string compiler
_SPACES = re.compile(r"\s+")
_DIGIT_RANGE_HYPHEN = re.compile(r"(?<=\d)-(?=\d)")


def normalize_token(text: str) -> str:
    """One ASR token (or a short phrase) in the grammar's input form. May return several space-separated words."""
    t = unicodedata.normalize("NFC", text).lower()
    t = t.replace("’", "'").replace("‘", "'").replace("`", "'").replace(" ", " ").replace(" ", " ")
    t = t.replace("p/m", "pm").replace("p/a", "pa")
    t = _UNSAFE.sub(" ", t)
    words = []
    for w in t.split():
        if _ABBREV.match(w):
            w = w.replace(".", "")
        w = w.strip(_EDGE)
        w = _LETTER_HYPHEN.sub(" ", w)
        w = _DIGIT_RANGE_HYPHEN.sub(" ", w)
        w = _DASHES.sub(" ", w)
        w = w.strip(_EDGE + " '-") if not w.startswith("'n") else w.rstrip(_EDGE + " -")
        if w:
            words.append(w)
    return _SPACES.sub(" ", " ".join(words)).strip()


def normalize_text(text: str) -> str:
    """A whole utterance in grammar input form."""
    return " ".join(t for t in (normalize_token(w) for w in text.split()) if t)


def tokens(text: str) -> list[str]:
    """Normalised word tokens of a text."""
    return normalize_text(text).split()


def is_filler(token: str) -> bool:
    return normalize_token(token) in FILLERS
