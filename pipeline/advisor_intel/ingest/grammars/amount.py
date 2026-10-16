"""The weighted ``amount`` grammar (§5.3).

Input: normalised tokens. Output: a tagged form that ``lattice.parse_output`` turns into a ``Reading``:

    A;v=<mantissa>[*<multiplier>];[v=…;]s=<scale_source>;[c=<currency>;][p=<period>;][r=1;]

The value is the sum of the ``v`` terms, so "two million five hundred thousand" is ``v=2*1000000;v=500*1000``.
The grammar makes every reading well-formed; it does not choose between them. A number with no scale word
gets its *implied* readings too: "forty two" → 42 (literal) and 42,000 (implied thousand); "two fifty" →
250, 250,000 and 2,500,000; "one point two" → 1.2 million, 1,200 and 1.2. Choosing among them is scale
resolution (§7.4), which sees context the grammar cannot.

Weights are costs (negative natural logs) in the tropical semiring; explicit forms cost nothing and
implied readings carry the v1 priors in ``COSTS``. Rates ("eleven and a half percent") keep the
spoken percentage as the value with ``r=1``; the normaliser divides by 100 for the FnaDocument (§8.3).
"""

from __future__ import annotations

from functools import lru_cache

import pynini
from pynini.lib import pynutil

from .numbers import number_fsts

# Cost (−ln p) of each reading of a number spoken without a scale word. Version-pinned with GRAMMAR_VERSION.
COSTS: dict[str, dict[str, float]] = {
    "int": {"literal": 0.3, "implied_thousand": 1.0},  # "forty two"
    "hcount": {"literal": 0.2, "implied_thousand": 1.5},  # "fifteen hundred" (→ 1.5 million when thousands are implied)
    "decimal": {"implied_million": 0.5, "implied_thousand": 1.0, "literal": 1.6},  # "one point two", "two and a half"
    "abc": {"literal": 0.9, "implied_thousand": 0.9, "implied_million": 1.1},  # "two fifty"
    "pair": {"literal": 0.8, "implied_thousand": 1.2},  # "twelve fifty"
    "ab": {"implied_thousand": 1.4, "implied_million": 1.2},  # "two five"
    "digits_int": {"literal": 0.2, "implied_thousand": 1.4},  # "250" from ITN
    "digits_decimal": {"implied_million": 0.7, "implied_thousand": 1.0, "literal": 1.0},  # "2.5"
}
MULTIPLIERS = {"implied_thousand": "1000", "implied_million": "1000000"}

# Scale words: spoken form → (multiplier, scale_source). Formal words may start multi-group numbers.
FORMAL_SCALES: dict[str, tuple[str, str]] = {
    "thousand": ("1000", "thousand"),
    "duisend": ("1000", "duisend"),
    "million": ("1000000", "million"),
    "miljoen": ("1000000", "miljoen"),
    "billion": ("1000000000", "billion"),
    "miljard": ("1000000000", "miljard"),
}
SLANG_SCALES: dict[str, tuple[str, str]] = {
    "k": ("1000", "k"),
    "grand": ("1000", "grand"),
    "bar": ("1000", "bar"),  # SA slang: "a bar" = R1,000
    "bars": ("1000", "bar"),
    "mil": ("1000000", "mil"),
    "mill": ("1000000", "mil"),
    "m": ("1000000", "m"),
    "mn": ("1000000", "m"),
    "bn": ("1000000000", "bn"),
}
SCALE_CURRENCY = {"bar": "ZAR"}  # the scale word fixes the currency
ATTACHED_SCALES = ("k", "m", "mn", "mil", "bn")  # may follow digits with no space: "250k", "1,2m"

CURRENCY_PREFIXES = {"r": "ZAR", "zar": "ZAR", "$": "USD", "us$": "USD", "usd": "USD", "£": "GBP", "gbp": "GBP", "€": "EUR", "eur": "EUR"}
CURRENCY_WORDS: dict[str, list[tuple[str, float]]] = {
    "rand": [("ZAR", 0.0)],
    "rands": [("ZAR", 0.0)],
    "randte": [("ZAR", 0.0)],
    "zar": [("ZAR", 0.0)],
    "bucks": [("ZAR", 0.3), ("USD", 1.0)],  # SA slang for rand, sometimes dollars
    "dollars": [("USD", 0.0)],
    "dollar": [("USD", 0.0)],
    "us dollars": [("USD", 0.0)],
    "usd": [("USD", 0.0)],
    "pounds": [("GBP", 0.0)],
    "pound": [("GBP", 0.0)],
    "pounds sterling": [("GBP", 0.0)],
    "sterling": [("GBP", 0.0)],
    "quid": [("GBP", 0.0)],
    "gbp": [("GBP", 0.0)],
    "pond": [("GBP", 0.0)],
    "euros": [("EUR", 0.0)],
    "euro": [("EUR", 0.0)],
    "eur": [("EUR", 0.0)],
}
PERIOD_WORDS: dict[str, str] = {
    **{
        w: "monthly"
        for w in (
            "a month",
            "per month",
            "pm",
            "p m",
            "monthly",
            "every month",
            "each month",
            "a mth",
            "per mth",
            "per maand",
            "'n maand",
            "n maand",
            "elke maand",
            "maandeliks",
        )
    },
    **{
        w: "annual"
        for w in (
            "a year",
            "per year",
            "per annum",
            "pa",
            "p a",
            "annually",
            "yearly",
            "every year",
            "each year",
            "a yr",
            "per yr",
            "per jaar",
            "'n jaar",
            "n jaar",
            "elke jaar",
            "jaarliks",
        )
    },
    **{w: "once" for w in ("once off", "lump sum", "in one go")},
}
PERCENT_WORDS = (" percent", " per cent", "%", " %", " persent", " procent", " pc")
GLUE_WORDS = frozenset({"and", "a", "an", "of", "point", "comma", "komma", "punt", "half", "quarter", "quarters", "three", "en", "'n", "n", "halwe", "kwart"})


def _union(fsts) -> pynini.Fst:
    fsts = list(fsts)
    return pynini.union(*fsts) if len(fsts) > 1 else fsts[0]


def _scale(table: dict[str, tuple[str, str]], *, attached: bool = False, af_solid: bool = False) -> pynini.Fst:
    """' thousand' → '*1000;s=thousand;' (plus the currency a scale word implies)."""
    arms = []
    for word, (mult, src) in table.items():
        out = f"*{mult};s={src};" + (f"c={SCALE_CURRENCY[word]};" if word in SCALE_CURRENCY else "")
        joints = [" "]
        if attached and word in ATTACHED_SCALES:
            joints.append("")
        if af_solid and word in ("duisend", "miljoen", "miljard"):
            joints.append("")
        for j in joints:
            arms.append(pynini.cross(f"{j}{word}", out))
    return _union(arms)


def _bare(mantissa: pynini.Fst, costs: dict[str, float], *, million_mantissa: pynini.Fst | None = None) -> pynini.Fst:
    """A number spoken with no scale word, with its literal and implied readings."""
    insert = pynutil.insert
    arms = []
    for reading, cost in costs.items():
        if reading == "literal":
            body = mantissa + insert(";s=literal;")
        elif reading == "implied_million" and million_mantissa is not None:
            body = million_mantissa + insert(f"*{MULTIPLIERS[reading]};s={reading};")
        else:
            body = mantissa + insert(f"*{MULTIPLIERS[reading]};s={reading};")
        arms.append(pynutil.add_weight(insert("v=") + body, cost))
    return _union(arms)


@lru_cache(maxsize=1)
def amount_grammar() -> pynini.Fst:
    """The compiled amount grammar (cached; arc-sorted for composition)."""
    n = number_fsts()
    insert, delete = pynutil.insert, pynutil.delete
    sp = delete(" ")

    formal = _scale(FORMAL_SCALES, af_solid=True)
    slang = _scale(SLANG_SCALES)
    slang_attached = _scale(SLANG_SCALES, attached=True)
    formal_attached = _scale(FORMAL_SCALES)

    # Multi-group cardinals with formal scale words, largest first: "two million five hundred thousand and fifty".
    def group(word_mult: str, first: bool) -> pynini.Fst:
        words = {w: v for w, v in FORMAL_SCALES.items() if v[0] == word_mult}
        arms = []
        for w, (mult, src) in words.items():
            out = f"*{mult};" + (f"s={src};" if first else "")
            for j in ([" ", ""] if w in ("duisend", "miljoen", "miljard") else [" "]):
                arms.append(pynini.cross(f"{j}{w}", out))
        return insert("v=") + n.m999 + _union(arms)

    and_ = (delete(" and") | delete(" en")).ques
    final = and_ + sp + insert("v=") + n.m999 + insert(";")
    t_rest = (and_ + sp + group("1000", False)).ques
    m_rest = (and_ + sp + group("1000000", False)).ques
    multi = _union(
        [
            group("1000000000", True) + m_rest + t_rest + final.ques,
            group("1000000", True) + t_rest + final.ques,
            group("1000", True) + final.ques,
        ]
    )
    # "duisend", "thousand", "a thousand": a scale word with an implied count of one.
    bare_scale = _union(
        [
            pynini.cross("duisend", "v=1*1000;s=duisend;"),
            pynutil.add_weight(pynini.cross("thousand", "v=1*1000;s=thousand;"), 0.5),
            pynini.cross("miljoen", "v=1*1000000;s=miljoen;"),
        ]
    )

    # One mantissa and one scale word: "two point five mil", "half a bar", "twelve hundred thousand", "a million".
    special = n.hcount | n.decimal | n.count_a | n.fraction | n.compressed_abc
    single = insert("v=") + ((special + formal) | ((n.m999 | special) + slang))

    bare_words = _union(
        [
            _bare(n.m999, COSTS["int"]),
            _bare(n.hcount, COSTS["hcount"]),
            _bare(n.decimal, COSTS["decimal"]),
            _bare(n.compressed_abc, COSTS["abc"], million_mantissa=n.compressed_abc_million),
            _bare(n.compressed_pair, COSTS["pair"]),
            _bare(n.compressed_ab_decimal, COSTS["ab"]),
        ]
    )
    words = multi | bare_scale | single | bare_words

    digits_scaled = insert("v=") + (n.digits_small | n.digits_decimal_scaled) + (slang_attached | formal_attached)
    digits_bare = _union(
        [
            _bare(n.digits_small, COSTS["digits_int"]),
            _bare(n.digits_decimal, COSTS["digits_decimal"]),
            insert("v=") + n.digits_big + insert(";s=digits;"),
        ]
    )
    digits = digits_scaled | digits_bare

    prefix = _union(pynini.cross(k, f"c={v};") for k, v in CURRENCY_PREFIXES.items()) + sp.ques
    cur_suffix = sp + _union(
        pynutil.add_weight(pynini.cross(w, f"c={c};"), cost) for w, options in CURRENCY_WORDS.items() for c, cost in options
    )
    period = sp + _union(pynini.cross(w, f"p={p};") for w, p in PERIOD_WORDS.items())

    money = ((prefix + digits) | digits | words) + cur_suffix.ques + period.ques

    rate_num = n.m999 | n.decimal | n.digits_small | n.digits_decimal
    percent = _union(pynini.cross(w, ";s=percent;r=1;") for w in PERCENT_WORDS)
    rate = insert("v=") + rate_num + percent + period.ques

    fst = (insert("A;") + (money | rate)).optimize()
    fst.arcsort("ilabel")
    return fst


def scale_vocabulary() -> frozenset[str]:
    """Single tokens that mark an amount (scale, currency, period and rate words)."""
    words: set[str] = set(FORMAL_SCALES) | set(SLANG_SCALES) | set(CURRENCY_PREFIXES)
    for w in CURRENCY_WORDS:
        words.update(w.split())
    words.update({"percent", "cent", "%", "persent", "procent"})
    return frozenset(words)


def period_vocabulary() -> frozenset[str]:
    words: set[str] = set()
    for w in PERIOD_WORDS:
        words.update(w.split())
    return frozenset(words)
