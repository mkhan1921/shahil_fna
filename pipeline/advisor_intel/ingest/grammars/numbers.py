"""Number words and the shared number transducers of the §5.3 grammars, in English and Afrikaans.

Every transducer here maps normalised input tokens (see ``tokens.py``) to a digit string, so the amount
and age grammars can build on them:

- ``m999``: cardinals 1–999 ("two hundred and fifty" → "250", "tweehonderd en vyftig" → "250").
- ``hcount``: hundred-counts 1,100–9,999 ("twelve hundred" → "1200", "twenty five hundred" → "2500").
- ``decimal``: "one point two" → "1.2", "point five" → "0.5", "een komma twee" → "1.2",
  "two and a half" → "2.5".
- ``fraction`` and ``count_a``: mantissas that only make sense before a scale word ("half a" bar → "0.5",
  "a" million → "1").
- ``compressed_*``: the spoken shorthand "two fifty" (one digit then a two-digit number), whose literal
  reading is 250 and whose million reading is 2.50; "twelve fifty" → 1250; "two five" → 2.5.
- ``digits_*``: written forms from ITN or numeric ASR output: "250", "250,000", "250 000", "1.2", "1,2".

The tables are also the vocabulary the span finder uses to spot numeric tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import pynini
from pynini.lib import pynutil

EN_ZERO = {"zero": 0, "nought": 0, "naught": 0, "oh": 0}
EN_UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
EN_TEENS = {
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
EN_DECADES = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}

AF_ZERO = {"nul": 0}
AF_UNITS = {"een": 1, "twee": 2, "drie": 3, "vier": 4, "vyf": 5, "ses": 6, "sewe": 7, "agt": 8, "nege": 9}
AF_TEENS = {
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
AF_DECADES = {"twintig": 20, "dertig": 30, "veertig": 40, "vyftig": 50, "sestig": 60, "sewentig": 70, "tagtig": 80, "negentig": 90}
AF_JOINTS = (" en ", "en", "ën")  # "vyf en twintig", "vyfentwintig", "tweeëntwintig"

DECIMAL_POINT_WORDS = ("point", "comma", "komma", "punt")
AND_FRACTIONS = {
    " and a half": ".5",
    " and a quarter": ".25",
    " and three quarters": ".75",
    " en 'n half": ".5",
    " en n half": ".5",
    " en 'n halwe": ".5",
    " en 'n kwart": ".25",
    " en n kwart": ".25",
}
FRACTIONS = {  # mantissas that need a following scale word: "half a bar", "a quarter of a million"
    "half a": "0.5",
    "half": "0.5",
    "a half": "0.5",
    "quarter of a": "0.25",
    "a quarter of a": "0.25",
    "quarter": "0.25",
    "a quarter": "0.25",
    "three quarters of a": "0.75",
    "'n halwe": "0.5",
    "n halwe": "0.5",
    "halwe": "0.5",
    "half 'n": "0.5",
    "'n kwart": "0.25",
    "n kwart": "0.25",
    "kwart": "0.25",
}
COUNT_A = {"a": "1", "an": "1", "'n": "1", "n": "1"}


def en_words_1_99() -> dict[str, int]:
    out: dict[str, int] = {**EN_UNITS, **EN_TEENS, **EN_DECADES}
    for d, dv in EN_DECADES.items():
        for u, uv in EN_UNITS.items():
            out[f"{d} {u}"] = dv + uv
    return out


def af_words_1_99() -> dict[str, int]:
    out: dict[str, int] = {**AF_UNITS, **AF_TEENS, **AF_DECADES}
    for d, dv in AF_DECADES.items():
        for u, uv in AF_UNITS.items():
            for j in AF_JOINTS:
                out[f"{u}{j}{d}"] = dv + uv
    return out


def number_vocabulary() -> frozenset[str]:
    """Every single token that is part of a number word (for the span finder's pre-filter)."""
    words: set[str] = set()
    for table in (en_words_1_99(), af_words_1_99(), EN_ZERO, AF_ZERO):
        for form in table:
            words.update(form.split())
    words.update({"hundred", "honderd", "thousand", "duisend", "million", "miljoen", "billion", "miljard"})
    for u in AF_UNITS:
        words.update({f"{u}honderd", f"{u}duisend"})
    words.update({"honderdduisend"})
    words.discard("en")
    return frozenset(words)


def _sm(pairs) -> pynini.Fst:
    return pynini.string_map(sorted(set(pairs)))


def _closure(fst: pynini.Fst, lo: int, hi: int) -> pynini.Fst:
    return pynini.closure(fst, lo, hi)


@dataclass(frozen=True)
class NumberFsts:
    """The shared number transducers (see the module docstring)."""

    units: pynini.Fst  # 1–9 → "1".."9"
    ten99: pynini.Fst  # 10–99 → "10".."99"
    digit_word: pynini.Fst  # 0–9 including "oh", "nought", "nul" → one digit
    m999: pynini.Fst
    hcount: pynini.Fst
    decimal: pynini.Fst
    and_fraction: pynini.Fst
    fraction: pynini.Fst
    count_a: pynini.Fst
    compressed_abc: pynini.Fst  # "two fifty" → "250", "one oh five" → "105"
    compressed_abc_million: pynini.Fst  # "two fifty" → "2.50"
    compressed_pair: pynini.Fst  # "twelve fifty" → "1250"
    compressed_ab_decimal: pynini.Fst  # "two five" → "2.5"
    digits_small: pynini.Fst  # "1".."999"
    digits_big: pynini.Fst  # "1000"+, "250,000", "250 000" (weighted: dot grouping is unusual)
    digits_decimal: pynini.Fst  # "1.2", "1,2", "1,25"
    digits_decimal_scaled: pynini.Fst  # also "1,250" as a decimal comma, only before a scale suffix ("R1,250m")


@lru_cache(maxsize=1)
def number_fsts() -> NumberFsts:
    delete = pynutil.delete
    insert = pynutil.insert
    sp = delete(" ")

    en99 = en_words_1_99()
    af99 = af_words_1_99()
    all99 = {**en99, **af99}

    units = _sm([(w, str(v)) for w, v in {**EN_UNITS, **AF_UNITS}.items()])
    one99 = _sm([(w, str(v)) for w, v in all99.items()])
    pad2 = _sm([(w, f"{v:02d}") for w, v in all99.items()])
    ten99 = _sm([(w, str(v)) for w, v in all99.items() if v >= 10])
    digit_word = _sm([(w, str(v)) for w, v in {**EN_UNITS, **AF_UNITS, **EN_ZERO, **AF_ZERO}.items()])

    # Hundreds: "[a|one|two…] hundred [and] [1–99]"; Afrikaans "[twee]honderd [en] [1–99]", solid or spaced.
    en_count = _sm([(w, str(v)) for w, v in EN_UNITS.items()] + [("a", "1")])
    en_hundred = (en_count + delete(" hundred")) | pynini.cross("hundred", "1")
    af_hundred = _sm(
        [(f"{u} honderd", str(v)) for u, v in AF_UNITS.items()]
        + [(f"{u}honderd", str(v)) for u, v in AF_UNITS.items()]
        + [("honderd", "1"), ("'n honderd", "1"), ("n honderd", "1")]
    )
    and_ = (delete(" and") | delete(" en")).ques
    rest = insert("00") | (and_ + sp + pad2)
    hundreds = (en_hundred | af_hundred) + rest
    m999 = (hundreds | one99).optimize()

    # Hundred-counts 11–99 hundred (not round tens): "fifteen hundred", "twaalfhonderd".
    hc_words = [(w, str(v)) for w, v in en99.items() if v >= 11 and v % 10]
    hc_en = _sm(hc_words) + delete(" hundred")
    hc_af = _sm([(f"{w}{j}honderd", str(v)) for w, v in af99.items() if v >= 11 and v % 10 for j in (" ", "")])
    hcount = ((hc_en | hc_af) + rest).optimize()

    # Decimals: "one point two", "point five", "een komma twee vyf", "nought point five", "one point twenty five".
    point = pynini.union(*(pynini.cross(w, ".") for w in DECIMAL_POINT_WORDS))
    frac = (digit_word + _closure(sp + digit_word, 0, 2)) | ten99
    zero_int = _sm([(w, "0") for w in {**EN_ZERO, **AF_ZERO}])
    decimal = (((m999 | zero_int) + sp) | insert("0")) + point + sp + frac
    and_fraction = m999 + pynini.union(*(pynini.cross(k, v) for k, v in AND_FRACTIONS.items()))
    decimal = (decimal | and_fraction).optimize()

    fraction = _sm(FRACTIONS.items())
    count_a = _sm(COUNT_A.items())

    oh_unit = pynini.cross("oh", "0") + sp + units
    teens = _sm([(w, str(v)) for w, v in all99.items() if 11 <= v <= 19])
    compressed_abc = (units + sp + (ten99 | oh_unit)).optimize()
    compressed_abc_million = (units + sp + insert(".") + (ten99 | oh_unit)).optimize()
    compressed_pair = (teens + sp + (ten99 | oh_unit)).optimize()
    compressed_ab_decimal = (units + sp + insert(".") + units).optimize()

    d = pynini.union(*"0123456789")
    nz = pynini.union(*"123456789")
    digits_small = (nz + _closure(d, 0, 2)).optimize()
    grouped = nz + _closure(d, 0, 2) + _closure((delete(",") | delete(" ")) + _closure(d, 3, 3), 1, 4)
    grouped_dot = pynutil.add_weight(nz + _closure(d, 0, 2) + _closure(delete(".") + _closure(d, 3, 3), 1, 4), 1.0)
    digits_big = (nz + _closure(d, 3, 11) | grouped | grouped_dot).optimize()
    int_any = (nz + _closure(d, 0, 11)) | "0"
    dec_point = int_any + "." + _closure(d, 1, 3)
    dec_comma = int_any + pynini.cross(",", ".") + _closure(d, 1, 2)
    dec_comma3 = pynutil.add_weight(int_any + pynini.cross(",", ".") + _closure(d, 3, 3), 1.0)
    digits_decimal = (dec_point | dec_comma).optimize()
    digits_decimal_scaled = (dec_point | dec_comma | dec_comma3).optimize()

    return NumberFsts(
        units=units,
        ten99=ten99,
        digit_word=digit_word,
        m999=m999,
        hcount=hcount,
        decimal=decimal,
        and_fraction=and_fraction.optimize(),
        fraction=fraction,
        count_a=count_a,
        compressed_abc=compressed_abc,
        compressed_abc_million=compressed_abc_million,
        compressed_pair=compressed_pair,
        compressed_ab_decimal=compressed_ab_decimal,
        digits_small=digits_small,
        digits_big=digits_big,
        digits_decimal=digits_decimal,
        digits_decimal_scaled=digits_decimal_scaled,
    )
