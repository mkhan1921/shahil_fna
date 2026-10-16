"""The ``age`` grammar (§5.3): ages, decades and bands, and school grades.

Output: ``G;v=<age>;[k=<lexicon key>;]``. The value is the age in years; the key names the band or the
form the age came from, so Stage 4 can look it up in the vagueness lexicon (§8.2):

- "forty two", "42 years old", "aged sixty" → 42, 60 (no key);
- "turning sixty" → 60, key ``turning``; "sixty-ish" → 60, key ``approx``;
- "late fifties" → 58.5, key ``late fifties``; "early sixties" → 61.5; "mid sixties" → 65; "fifties" → 54.5;
- "grade nine" → 15 (grade + 6, §7.6), key ``grade``; "grade R" → 6; "standard eight" (the old system) → 16,
  key ``standard``; "matric" → 18, key ``matric``. Afrikaans forms ("graad nege", "laat vyftigs", "matriek") too.
"""

from __future__ import annotations

from functools import lru_cache

import pynini
from pynini.lib import pynutil

from .numbers import AF_DECADES, af_words_1_99, en_words_1_99

MAX_AGE = 120

EN_DECADE_PLURALS = {
    "twenties": 20,
    "thirties": 30,
    "forties": 40,
    "fourties": 40,
    "fifties": 50,
    "sixties": 60,
    "seventies": 70,
    "eighties": 80,
    "nineties": 90,
}
AF_DECADE_PLURALS = {f"{w}s": v for w, v in AF_DECADES.items()}  # "vyftigs"
BAND_WORDS = {
    "early": "early",
    "mid": "mid",
    "middle": "mid",
    "late": "late",
    "vroeë": "early",
    "vroee": "early",
    "middel": "mid",
    "laat": "late",
}
BAND_OFFSETS = {"early": 1.5, "mid": 5.0, "late": 8.5, "": 4.5}
TEEN_BANDS = {"early teens": 14.0, "mid teens": 15.5, "late teens": 18.0, "teens": 16.0}
AGE_PREFIXES = {"aged": "", "age": "", "turning": "turning", "turns": "turning", "turned": "turned", "ouderdom": ""}
# Bare "years" / "jaar" is left out on purpose: "eight years" is far more often a duration than an age.
AGE_SUFFIXES = (" years old", " year old", " yrs old", " years of age", " jaar oud")
GRADE_WORDS = ("grade", "graad")
MATRIC_WORDS = ("matric", "matriek", "matriculating")


def _age_forms() -> list[tuple[str, int]]:
    """Every spoken or written form of the ages 1–120."""
    en99, af99 = en_words_1_99(), af_words_1_99()
    forms: list[tuple[str, int]] = [*en99.items(), *af99.items()]
    languages = ((("a hundred", "one hundred", "hundred"), "and", en99), (("honderd", "een honderd", "eenhonderd"), "en", af99))
    for heads, and_word, table in languages:
        for head in heads:
            forms.append((head, 100))
            for w, rest in table.items():
                if 100 + rest <= MAX_AGE:
                    forms += [(f"{head} {and_word} {w}", 100 + rest), (f"{head} {w}", 100 + rest)]
    forms.extend((str(v), v) for v in range(1, MAX_AGE + 1))
    return forms


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


@lru_cache(maxsize=1)
def age_grammar() -> pynini.Fst:
    insert = pynutil.insert
    ages = pynini.string_map(sorted({(w, str(v)) for w, v in _age_forms()}))
    value = insert("v=") + ages + insert(";")

    prefix = pynini.union(*(pynini.cross(f"{w} ", f"k={k};" if k else "") for w, k in AGE_PREFIXES.items()))
    suffix = pynini.union(*(pynutil.delete(s) for s in AGE_SUFFIXES))
    ish = pynini.cross(" ish", "k=approx;") | pynini.cross("ish", "k=approx;")
    cardinal = prefix.ques + value + (suffix | ish).ques

    bands = []
    for plural_table in (EN_DECADE_PLURALS, AF_DECADE_PLURALS):
        for plural, decade in plural_table.items():
            canon_plural = next(k for k, d in EN_DECADE_PLURALS.items() if d == decade and k != "fourties")
            bands.append(pynini.cross(plural, f"v={_fmt(decade + BAND_OFFSETS[''])};k={canon_plural};"))
            for word, band in BAND_WORDS.items():
                out = f"v={_fmt(decade + BAND_OFFSETS[band])};k={band} {canon_plural};"
                bands.append(pynini.cross(f"{word} {plural}", out))
    for phrase, v in TEEN_BANDS.items():
        bands.append(pynini.cross(phrase, f"v={_fmt(v)};k={phrase};"))

    grade_numbers = [(w, v) for w, v in {**en_words_1_99(), **af_words_1_99()}.items() if 1 <= v <= 12]
    grade_numbers += [(str(v), v) for v in range(1, 13)] + [("r", 0), ("nought", 0), ("zero", 0)]
    grades = []
    for gw in GRADE_WORDS:
        for w, v in grade_numbers:
            grades.append(pynini.cross(f"{gw} {w}", f"v={v + 6};k=grade;"))
    for w, v in [(w, v) for w, v in {**en_words_1_99(), **af_words_1_99()}.items() if 1 <= v <= 10] + [(str(v), v) for v in range(1, 11)]:
        grades.append(pynini.cross(f"standard {w}", f"v={v + 8};k=standard;"))
        grades.append(pynini.cross(f"standerd {w}", f"v={v + 8};k=standard;"))
    for mw in MATRIC_WORDS:
        grades.append(pynini.cross(mw, "v=18;k=matric;"))
        grades.append(pynini.cross(f"in {mw}", "v=18;k=matric;"))

    fst = (insert("G;") + pynini.union(cardinal, *bands, *grades)).optimize()
    fst.arcsort("ilabel")
    return fst


def age_marker_vocabulary() -> frozenset[str]:
    """Tokens that make a number an age (for the span finder)."""
    words: set[str] = set(EN_DECADE_PLURALS) | set(AF_DECADE_PLURALS) | set(BAND_WORDS) | set(AGE_PREFIXES)
    words |= set(GRADE_WORDS) | set(MATRIC_WORDS) | {"standard", "standerd", "teens", "old", "ish", "jaar", "oud", "years", "yrs", "year"}
    return frozenset(words)


# Words that make a phrase an age on their own ("late fifties", "matric", "teens"); "old" and "grade" need a number with them.
STRONG_AGE_MARKERS = frozenset(set(EN_DECADE_PLURALS) | set(AF_DECADE_PLURALS) | set(MATRIC_WORDS) | {"teens"})
