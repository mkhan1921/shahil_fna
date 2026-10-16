"""Phrase canonicalisation and spoken-number parsing for lexicon lookups (spec §8.2–8.3).

Lexicon keys and spoken phrases are compared in one canonical form: accents folded, lower case,
apostrophes dropped, hyphens and slashes as spaces, other punctuation removed, whitespace collapsed.
Decades are spelled out ("60s", "sixty's" → "sixties") and digits are split from their suffixes
("50k" → "50 k", "60ish" → "60 ish"), so "Early 60's", "early sixties" and "early-sixties" are one key.
"""

from __future__ import annotations

import re
import unicodedata

UNITS_EN: dict[str, int] = {
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
TENS_EN: dict[str, int] = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fourty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
UNITS_AF: dict[str, int] = {
    "nul": 0,
    "een": 1,
    "twee": 2,
    "drie": 3,
    "vier": 4,
    "vyf": 5,
    "ses": 6,
    "sewe": 7,
    "agt": 8,
    "nege": 9,
    "tien": 10,
    "elf": 11,
    "twaalf": 12,
    "dertien": 13,
    "veertien": 14,
    "vyftien": 15,
    "sestien": 16,
    "sewentien": 17,
    "agtien": 18,
    "negentien": 19,
}
TENS_AF: dict[str, int] = {
    "twintig": 20,
    "dertig": 30,
    "veertig": 40,
    "vyftig": 50,
    "sestig": 60,
    "sewentig": 70,
    "tagtig": 80,
    "negentig": 90,
}
UNITS = {**UNITS_EN, **UNITS_AF}
TENS = {**TENS_EN, **TENS_AF}
HUNDRED = {"hundred", "honderd"}
THOUSAND = {"thousand", "duisend"}
NUMBER_WORDS = set(UNITS) | set(TENS) | HUNDRED | THOUSAND

# Decades as spoken, keyed by their first year. Afrikaans plurals are folded into English keys.
DECADES: dict[str, int] = {
    "teens": 10,
    "twenties": 20,
    "thirties": 30,
    "forties": 40,
    "fifties": 50,
    "sixties": 60,
    "seventies": 70,
    "eighties": 80,
    "nineties": 90,
}
_DECADE_BY_DIGIT = {str(v // 10): k for k, v in DECADES.items() if v >= 20}
_DECADE_AF = {
    "twintigs": "twenties",
    "dertigs": "thirties",
    "veertigs": "forties",
    "vyftigs": "fifties",
    "sestigs": "sixties",
    "sewentigs": "seventies",
    "tagtigs": "eighties",
    "negentigs": "nineties",
    "tieners": "teens",
}
_TENS_WORD_RE = "|".join(sorted(TENS_EN, key=len, reverse=True))
_ISH_RE = re.compile(r"\b(" + "|".join(sorted(set(UNITS_EN) | set(TENS_EN), key=len, reverse=True)) + r")ish\b")
_AF_JOINED = re.compile(r"^(een|twee|drie|vier|vyf|ses|sewe|agt|nege)en(twintig|dertig|veertig|vyftig|sestig|sewentig|tagtig|negentig)$")


def canon(text: str) -> str:
    """The canonical form used for every lexicon key and every spoken phrase."""
    s = unicodedata.normalize("NFKD", text or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = s.replace("’", "'").replace("‘", "'").replace("`", "'")
    prev = None
    while prev != s:  # thousands separators: 1,000 000 → 1000000
        prev = s
        s = re.sub(r"(?<=\d)[, ](?=\d{3}(?!\d))", "", s)
    s = s.replace("%", " percent ")
    s = re.sub(r"\br\s?(?=\d)", "", s)  # currency marker: R50k → 50k
    s = re.sub(r"\b([2-9])0\s?'?s\b", lambda m: _DECADE_BY_DIGIT[m.group(1)], s)  # 60s, 60's → sixties
    s = re.sub(r"\b(" + _TENS_WORD_RE + r")'?s\b", lambda m: m.group(1)[:-1] + "ies", s)  # sixty's → sixties
    s = s.replace("'", "")  # 'n → n, i'm → im
    s = re.sub(r"[-/–—_+]", " ", s)
    s = re.sub(r"(?<=\d)(?=[a-z])", " ", s)  # 50k → 50 k, 60ish → 60 ish
    s = _ISH_RE.sub(r"\1 ish", s)  # sixtyish → sixty ish
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", s)  # sentence dots go, decimals stay
    s = re.sub(r"[^\w\s.]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return " ".join(_DECADE_AF.get(t, t) for t in s.split())


def tokens(text: str) -> list[str]:
    return canon(text).split()


# --------------------------------------------------------------------------- spoken numbers


def _expand(toks: list[str]) -> list[str]:
    out: list[str] = []
    for t in toks:
        m = _AF_JOINED.match(t)
        out.extend([m.group(1), "en", m.group(2)] if m else [t])
    return out


def _below_100(toks: list[str]) -> int | None:
    """'sixty five', 'sixty', 'five', '65', 'vyf en sestig'."""
    if len(toks) == 1:
        t = toks[0]
        if t.isdigit():
            return int(t)
        return UNITS.get(t, TENS.get(t))
    if len(toks) == 2 and toks[0] in TENS and toks[1] in UNITS and 0 < UNITS[toks[1]] < 10:
        return TENS[toks[0]] + UNITS[toks[1]]
    if len(toks) == 3 and toks[0] in UNITS and toks[1] == "en" and toks[2] in TENS and 0 < UNITS[toks[0]] < 10:
        return TENS[toks[2]] + UNITS[toks[0]]
    return None


def _elided_hundreds(toks: list[str]) -> int | None:
    """Spoken with the 'hundred' left out: 'two fifty' → 250, 'nineteen eighty five' → 1985, 'twenty twenty' → 2020."""
    if len(toks) not in (2, 3):
        return None
    head = UNITS.get(toks[0]) if toks[0] in UNITS and 0 < UNITS[toks[0]] < 20 else TENS.get(toks[0])
    if head is None or toks[1] not in TENS:
        return None
    rest = _below_100(toks[1:])
    return None if rest is None else head * 100 + rest


def _integer(toks: list[str]) -> int | None:
    """Whole numbers below a million: 'a hundred and twenty', 'two thousand five hundred', 'twee honderd'."""
    if not toks:
        return None
    for mult_words, mult in ((THOUSAND, 1000), (HUNDRED, 100)):
        idx = next((i for i, t in enumerate(toks) if t in mult_words), None)
        if idx is None:
            continue
        head = toks[:idx]
        if head in ([], ["a"], ["an"], ["n"]):
            high = 1
        else:
            high = _integer(head)
            if high is None:
                return None
        rest = toks[idx + 1 :]
        if rest[:1] in (["and"], ["en"]):
            rest = rest[1:]
        low = _integer(rest) if rest else 0
        if low is None or low >= mult:
            return None
        return high * mult + low
    n = _below_100(toks)
    return n if n is not None else _elided_hundreds(toks)


def parse_number(text: str) -> float | None:
    """A spoken or written number, or None when the text is not (only) a number.

    Handles digits ('62', '2.5'), English and Afrikaans words ('sixty-five', 'vyf-en-sestig',
    'vyfensestig'), 'a hundred and twenty', 'two point five' and 'two and a half'.
    """
    c = canon(text)
    if not c:
        return None
    if re.fullmatch(r"-?\d+(\.\d+)?", c):
        return float(c)
    toks = _expand(c.split())
    frac = 0.0
    for tail in (["and", "a", "half"], ["en", "n", "half"], ["and", "half"], ["en", "half"]):
        if toks[-len(tail) :] == tail and len(toks) > len(tail):
            frac, toks = 0.5, toks[: -len(tail)]
            break
    if toks in (["half"], ["a", "half"], ["n", "half"]):
        return 0.5
    if "point" in toks or "komma" in toks:
        i = toks.index("point") if "point" in toks else toks.index("komma")
        whole = _integer(toks[:i])
        digits = [UNITS.get(t) if not t.isdigit() else int(t) for t in toks[i + 1 :]]
        if whole is None or not digits or any(d is None or d > 9 for d in digits):
            return None
        return whole + float("0." + "".join(str(d) for d in digits))
    n = _integer(toks)
    return None if n is None else n + frac


def strip_numbers(c: str, extra: set[str] | frozenset[str] = frozenset()) -> str:
    """Drop number tokens (and ``extra`` words such as scale words) from a canonical phrase."""
    keep = [t for t in c.split() if not (re.fullmatch(r"-?\d+(\.\d+)?", t) or t in NUMBER_WORDS or t in extra or t in ("and", "point"))]
    return " ".join(keep)


def split_number(c: str) -> tuple[str, float, str] | None:
    """Split a canonical phrase into (words before, the number, words after), using the longest number run.

    'about sixty five' → ('about', 65.0, ''); 'sixty ish' → ('', 60.0, 'ish'); 'grade nine' → ('grade', 9.0, '').
    """
    toks = c.split()
    best: tuple[int, int, float] | None = None
    for i in range(len(toks)):
        for j in range(len(toks), i, -1):
            if best and (j - i) <= (best[1] - best[0]):
                break
            v = parse_number(" ".join(toks[i:j]))
            if v is not None:
                best = (i, j, v)
                break
    if best is None:
        return None
    i, j, v = best
    return " ".join(toks[:i]), v, " ".join(toks[j:])
