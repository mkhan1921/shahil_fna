"""Spoken forms of amounts, ages and rates for synthetic call scripts (spec §15.1, §5.3).

South African clients say amounts many ways: digits ("R85,000"), words ("eighty-five thousand"), scale slang
("eighty-five bar", "fifty K", "two point five mil", "fifty grand", "half a bar") and Afrikaans
("twee-honderd duisend", "vyf-en-twintig duisend"). The synthetic generator uses every one of them so the
extraction and grammar layers are measured on what the §5.3 amount grammar has to accept.
"""

from __future__ import annotations

import math
from typing import Literal

AmountStyle = Literal["digits", "short_k", "words", "mil", "bar", "grand", "afrikaans"]
AMOUNT_STYLES: tuple[str, ...] = ("digits", "short_k", "words", "mil", "bar", "grand", "afrikaans")

_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_EEN = [
    "nul",
    "een",
    "twee",
    "drie",
    "vier",
    "vyf",
    "ses",
    "sewe",
    "agt",
    "nege",
    "tien",
    "elf",
    "twaalf",
    "dertien",
    "veertien",
    "vyftien",
    "sestien",
    "sewentien",
    "agtien",
    "negentien",
]
_TIG = ["", "", "twintig", "dertig", "veertig", "vyftig", "sestig", "sewentig", "tagtig", "negentig"]


def words_en(n: int) -> str:
    """English number words: 85_000 → 'eighty-five thousand'; 1_250 → 'one thousand two hundred and fifty'."""
    if n < 0:
        return "minus " + words_en(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else "")
    if n < 1000:
        rest = n % 100
        return f"{_ONES[n // 100]} hundred" + (f" and {words_en(rest)}" if rest else "")
    for scale, name in ((10**9, "billion"), (10**6, "million"), (1000, "thousand")):
        if n >= scale:
            head, rest = f"{words_en(n // scale)} {name}", n % scale
            if not rest:
                return head
            return head + (" and " if rest < 100 else " ") + words_en(rest)
    raise AssertionError(n)  # pragma: no cover


def words_af(n: int) -> str:
    """Afrikaans number words: 200_000 → 'twee-honderd duisend'; 25_000 → 'vyf-en-twintig duisend'."""
    if n < 20:
        return _EEN[n]
    if n < 100:
        u = n % 10
        return f"{_EEN[u]}-en-{_TIG[n // 10]}" if u else _TIG[n // 10]
    if n < 1000:
        h, rest = n // 100, n % 100
        head = "honderd" if h == 1 else f"{_EEN[h]}-honderd"
        return head + (f" {words_af(rest)}" if rest else "")
    if n < 10**6:
        t, rest = n // 1000, n % 1000
        head = "duisend" if t == 1 else f"{words_af(t)} duisend"
        return head + (f" {words_af(rest)}" if rest else "")
    m, rest = n // 10**6, n % 10**6
    return f"{words_af(m)} miljoen" + (f" {words_af(rest)}" if rest else "")


def nice_amount(x: float, sig: int = 2) -> float:
    """Round to ``sig`` significant figures (how people quote amounts): 83_512 → 84_000."""
    if x == 0:
        return 0.0
    k = math.floor(math.log10(abs(x))) - sig + 1
    return float(round(x / 10**k) * 10**k)


def _decimal_words(x: float, lang: Literal["en", "af"] = "en") -> str | None:
    """'2.5' → 'two point five' ('twee komma vyf'); None unless x has at most one decimal."""
    if abs(x * 10 - round(x * 10)) > 1e-9:
        return None
    whole, tenth = int(x), round((x - int(x)) * 10)
    w = words_en if lang == "en" else words_af
    if tenth == 0:
        return w(whole)
    return f"{w(whole)} {'point' if lang == 'en' else 'komma'} {w(tenth)}"


def possible_styles(value: float) -> list[str]:
    """The styles that can say ``value`` exactly."""
    v = abs(value)
    out = ["digits", "words"]
    if v >= 1000 and v % 1000 == 0 and v < 10**6:
        out += ["short_k", "bar", "grand"]
    if v == 500:
        out.append("bar")
    if v >= 10**6 and _decimal_words(v / 10**6) is not None:
        out.append("mil")
    if float(v).is_integer() and (v < 10**6 or _decimal_words(v / 10**6, "af") is not None):
        out.append("afrikaans")
    return out


def say_amount(value: float, style: str = "digits") -> str:
    """One spoken form of an amount in rand (no period). Falls back to digits when the style cannot say it."""
    v = abs(value)
    if style not in possible_styles(value):
        style = "digits"
    if style == "digits":
        return f"R{v:,.0f}" if float(v).is_integer() else f"R{v:,.2f}"
    if style == "words":
        return f"{words_en(round(v))} rand"
    if style == "short_k":
        return f"{int(v // 1000)}k"
    if style == "grand":
        return f"{words_en(int(v // 1000))} grand"
    if style == "bar":
        if v == 1000:
            return "a bar"
        if v == 500:
            return "half a bar"
        return f"{words_en(int(v // 1000))} bar"
    if style == "mil":
        return f"{_decimal_words(v / 10**6)} mil"
    if style == "afrikaans":
        if v >= 10**6 and v % 10**6 != 0:
            return f"{_decimal_words(v / 10**6, 'af')} miljoen"
        return words_af(int(v))
    raise ValueError(style)  # pragma: no cover


def read_amount(text: str) -> float | None:
    """Inverse of ``say_amount`` for the forms it produces (used to check scripts and in tests)."""
    t = text.strip().lower().removesuffix(" rand")
    if t.startswith("r") and t[1:2].isdigit():
        return float(t[1:].replace(",", ""))
    if t.endswith("k") and t[:-1].isdigit():
        return float(t[:-1]) * 1000
    if t == "a bar":
        return 1000.0
    if t == "half a bar":
        return 500.0
    for suffix, scale in ((" bar", 1000.0), (" grand", 1000.0), (" mil", 1e6), (" miljoen", 1e6)):
        if t.endswith(suffix):
            head = t[: -len(suffix)]
            if " point " in head or " komma " in head:
                whole, _, tenth = head.replace(" komma ", " point ").partition(" point ")
                return (_parse_words(whole) + _parse_words(tenth) / 10) * scale
            return _parse_words(head) * scale
    try:
        return _parse_words(t)
    except KeyError:
        return None


_EN_VALUES = {w: i for i, w in enumerate(_ONES)} | {w: 10 * i for i, w in enumerate(_TENS) if w}
_AF_VALUES = {w: i for i, w in enumerate(_EEN)} | {w: 10 * i for i, w in enumerate(_TIG) if w}


def _parse_words(text: str) -> float:
    total, current = 0.0, 0.0
    for raw in text.replace(" and ", " ").replace(" en ", " ").split():
        if "-en-" in raw:  # Afrikaans unit-en-tens: vyf-en-twintig
            u, _, tens = raw.partition("-en-")
            current += _AF_VALUES[u] + _AF_VALUES[tens]
            continue
        if raw.endswith("-honderd"):
            current += _AF_VALUES[raw.removesuffix("-honderd")] * 100
            continue
        parts = raw.split("-")
        for w in parts:
            if w in ("hundred", "honderd"):
                current = (current or 1) * 100
            elif w in ("thousand", "duisend"):
                total += (current or 1) * 1000
                current = 0
            elif w in ("million", "miljoen"):
                total += (current or 1) * 1e6
                current = 0
            elif w in _EN_VALUES:
                current += _EN_VALUES[w]
            else:
                current += _AF_VALUES[w]
    return total + current


def say_period(period: str, variant: int = 0) -> str:
    if period == "monthly":
        return (" a month", " per month", " p.m.", " every month")[variant % 4]
    if period == "annual":
        return (" a year", " per year", " per annum", " every year")[variant % 4]
    return ""


def say_age(age: int, as_words: bool = True) -> str:
    return words_en(age) if as_words else str(age)


def say_percent(rate: float, as_words: bool = True) -> str:
    """0.1175 → 'eleven point seven five percent' (or '11.75%')."""
    pct = round(rate * 100, 2)
    if not as_words:
        return f"{pct:g}%"
    whole = int(pct)
    frac = round(pct - whole, 2)
    if frac == 0:
        return f"{words_en(whole)} percent"
    if frac == 0.5:
        return f"{words_en(whole)} and a half percent"
    digits = f"{frac:.2f}"[2:].rstrip("0")
    return f"{words_en(whole)} point {' '.join(_ONES[int(d)] for d in digits)} percent"
