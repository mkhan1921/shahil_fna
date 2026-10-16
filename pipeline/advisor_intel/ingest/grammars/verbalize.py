"""Numbers to spoken English, the inverse of the grammars (§5.3, §5.4).

Used to render the per-call biasing phrases ("one point two million", "one comma two mil", "twelve hundred
thousand"), the phrases a constrained second pass may output, ScriptedASR's emulated provider ITN, and
human-readable labels for readings. Spoken forms follow South African English ("two hundred and fifty").
"""

from __future__ import annotations

from decimal import Decimal

from ...models.transcript import Reading
from .catalog import PRODUCTS, PROVIDERS

ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
    "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
]  # fmt: skip
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
SCALES = ((10**9, "billion"), (10**6, "million"), (10**3, "thousand"))
CURRENCY_SYMBOLS = {"ZAR": "R", "USD": "$", "GBP": "£", "EUR": "€"}
PERIOD_PHRASES = {"monthly": "a month", "annual": "a year", "once": "once off"}


def _under_1000(n: int, and_word: bool = True) -> str:
    if n < 20:
        return ONES[n]
    if n < 100:
        t, u = divmod(n, 10)
        return TENS[t] + (f" {ONES[u]}" if u else "")
    h, r = divmod(n, 100)
    head = f"{ONES[h]} hundred"
    return head if not r else head + (" and " if and_word else " ") + _under_1000(r)


def int_to_words(n: int, *, and_word: bool = True) -> str:
    """1,250,000 → 'one million two hundred and fifty thousand'."""
    if n == 0:
        return "zero"
    if n < 0:
        return "minus " + int_to_words(-n, and_word=and_word)
    parts = []
    for value, name in SCALES:
        if n >= value:
            q, n = divmod(n, value)
            parts.append(f"{int_to_words(q, and_word=and_word) if q >= 1000 else _under_1000(q, and_word)} {name}")
    if n:
        parts.append(("and " if parts and n < 100 and and_word else "") + _under_1000(n, and_word))
    return " ".join(parts)


def number_to_words(x: float | Decimal, *, point: str = "point", and_word: bool = True) -> str:
    """2.5 → 'two point five'; 1.25 → 'one point two five'; 42 → 'forty two'."""
    d = Decimal(str(x)).normalize()
    if d == d.to_integral_value():
        return int_to_words(int(d), and_word=and_word)
    sign = d.as_tuple().sign
    text = format(abs(d), "f")
    whole, _, frac = text.partition(".")
    words = int_to_words(int(whole), and_word=and_word) + f" {point} " + " ".join(ONES[int(c)] for c in frac)
    return ("minus " if sign else "") + words


def _dedupe(forms: list[str]) -> list[str]:
    seen, out = set(), []
    for f in forms:
        if f and f not in seen:
            seen.add(f)
            out.append(f)
    return out


def amount_spoken_forms(value: float, *, max_forms: int = 8) -> list[str]:
    """Spoken forms of an amount, most usual first.

    1,200,000 → "one million two hundred thousand", "one point two million", "one point two mil",
    "one comma two mil", "twelve hundred thousand", …; 250,000 → "two hundred and fifty thousand",
    "two fifty thousand", "two fifty k", "two fifty"; 1,500 → "one thousand five hundred", "fifteen hundred".
    """
    d = Decimal(str(round(float(value), 2))).normalize()
    if d <= 0:
        return []
    forms: list[str] = []
    if d != d.to_integral_value():
        return _dedupe([number_to_words(d), number_to_words(d, point="comma")])[:max_forms]
    n = int(d)
    forms.append(int_to_words(n))
    if n >= 1_000_000:
        m = (Decimal(n) / Decimal(1_000_000)).normalize()
        tail: list[str] = []
        if m == m.to_integral_value() or len(str(m).split(".")[1]) <= 2:
            words, comma = number_to_words(m), number_to_words(m, point="comma")
            forms += [f"{words} million", f"{words} mil", f"{comma} mil"]
            tail.append(f"{comma} million")
            if m * 2 == (m * 2).to_integral_value() and m != m.to_integral_value():
                tail.append(f"{int_to_words(int(m))} and a half million")
        if n % 100_000 == 0 and 1_100_000 <= n <= 9_900_000 and (n // 100_000) % 10:
            forms.append(f"{int_to_words(n // 100_000)} hundred thousand")
        forms += tail
    elif n >= 1000:
        k, r = divmod(n, 1000)
        if r == 0:
            forms.append(f"{_under_1000(k)} k")
            if 100 < k < 1000 and k % 100 >= 10:
                forms += [f"{ONES[k // 100]} {_under_1000(k % 100)} thousand", f"{ONES[k // 100]} {_under_1000(k % 100)}"]
            if k <= 20:
                forms.append("a bar" if k == 1 else f"{_under_1000(k)} bar")
            if k < 100:
                forms.append(f"{_under_1000(k)} grand")
        if n % 100 == 0 and n < 10_000 and (n // 100) % 10:
            forms.append(f"{int_to_words(n // 100)} hundred")
        if r == 500 and k < 100:
            forms.append(f"{_under_1000(k)} and a half thousand")
    else:
        if 100 < n < 1000 and n % 100 >= 10:
            forms.append(f"{ONES[n // 100]} {_under_1000(n % 100)}")
    forms.append(int_to_words(n, and_word=False))
    return _dedupe(forms)[:max_forms]


def age_spoken_forms(age: float) -> list[str]:
    """60 → ['sixty']; 65 → ['sixty five']."""
    return [int_to_words(int(round(age)))]


def written_amount(value: float, currency: str | None = "ZAR") -> str:
    """250000 → 'R250 000'; 1.5 → 'R1.5'; None currency → '250 000'."""
    d = Decimal(str(round(float(value), 2))).normalize()
    whole, _, frac = format(d, "f").partition(".")
    grouped = f"{int(whole):,}".replace(",", " ")
    return CURRENCY_SYMBOLS.get(currency or "", "") + grouped + (f".{frac}" if frac else "")


def format_reading(r: Reading, kind: str | None = None) -> str:
    """A short label for a reading, unique enough to offer as a choice: 'R250 000 a month', '11.5%', 'age 58.5 (late fifties)'."""
    if kind is None:
        kind = "product" if isinstance(r.value, str) else "age" if r.scale_source is None and not r.is_rate else "amount"
    if kind == "product" or isinstance(r.value, str):
        return str(r.value) + (f" ({r.provider})" if r.provider else "")
    if r.is_rate:
        return f"{Decimal(str(r.value)).normalize():f}%" + (f" {PERIOD_PHRASES[r.period]}" if r.period in PERIOD_PHRASES else "")
    if kind == "age":
        return f"age {Decimal(str(r.value)).normalize():f}" + (f" ({r.lexicon_key})" if r.lexicon_key else "")
    label = written_amount(float(r.value), r.currency or "ZAR")
    return label + (f" {PERIOD_PHRASES[r.period]}" if r.period in PERIOD_PHRASES else "")


def reading_spoken_forms(r: Reading, kind: str) -> list[str]:
    """Phrases a decoder may output for this reading (the second pass's allowed outputs)."""
    if kind == "product":
        forms = [s for p in PRODUCTS if p.product_type == r.value for s in p.surfaces[:3]]
        providers = [s for pv in PROVIDERS if pv.name == r.provider for s in pv.surfaces[:2]]
        return _dedupe([f"{pv} {f}" for pv in providers for f in forms[:2]] + forms + providers)
    if kind == "age":
        forms = age_spoken_forms(float(r.value))
        return _dedupe(([r.lexicon_key] if r.lexicon_key and " " in r.lexicon_key else []) + forms)
    base = amount_spoken_forms(float(r.value))
    if r.is_rate:
        return _dedupe([f"{number_to_words(r.value)} percent"])
    suffix = f" {PERIOD_PHRASES[r.period]}" if r.period in PERIOD_PHRASES else ""
    return _dedupe([f + suffix for f in base])
