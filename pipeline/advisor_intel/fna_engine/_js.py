"""JavaScript semantics the TypeScript engine relies on, so the port produces the same numbers and text (§12.7).

``Math.round`` rounds halves towards +∞, ``toFixed`` rounds the exact binary value half away from zero,
``String(n)`` prints the shortest round-trip digits, ``toLocaleString('en-ZA')`` groups with a no-break
space, ``reduce`` adds left to right (Python 3.12's ``sum`` compensates), and ``Math.pow`` returns NaN or
Infinity where Python raises. Dates follow V8 in a process running in UTC, as the parity harness runs it.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache

INF = math.inf
NAN = math.nan
MS_PER_DAY = 86_400_000
_EPOCH = date(1970, 1, 1)
_EXACT_INT = 2.0**53

# --------------------------------------------------------------------------- arithmetic


def pw(base: float, exp: float) -> float:
    """``Math.pow``: NaN for a negative base with a fractional exponent or a NaN exponent, ±Infinity on overflow."""
    if base > 0 and exp - exp == 0:  # the common case: positive base, finite exponent
        try:
            return base**exp
        except OverflowError:
            return INF
    if exp != exp or (abs(base) == 1 and exp in (INF, -INF)):
        return NAN
    try:
        return math.pow(base, exp)
    except ValueError:
        if base == 0:  # 0 ** negative
            odd = float(exp).is_integer() and exp % 2 == 1
            return -INF if odd and math.copysign(1.0, base) < 0 else INF
        return NAN
    except OverflowError:
        odd = float(exp).is_integer() and exp % 2 == 1
        return -INF if base < 0 and odd else INF


def fsum_js(values: Iterable[float]) -> float:
    """Left-to-right addition, as ``reduce((a, b) => a + b, 0)``."""
    t = 0.0
    for v in values:
        t += v
    return t


def js_round(x: float) -> float:
    """``Math.round``: ties go towards +∞ and results in [-0.5, 0) are -0."""
    if x != x or x in (INF, -INF):
        return x
    f = math.floor(x)
    r = f + 1 if x - f >= 0.5 else f
    if r == 0:
        return -0.0 if x < 0 or (x == 0 and math.copysign(1.0, x) < 0) else 0.0
    return float(r)


def js_min(*xs: float) -> float:
    """``Math.min``: NaN wins."""
    for x in xs:
        if x != x:
            return NAN
    return min(xs)


def js_max(*xs: float) -> float:
    """``Math.max``: NaN wins."""
    for x in xs:
        if x != x:
            return NAN
    return max(xs)


# --------------------------------------------------------------------------- number → text


def _shortest(x: float) -> tuple[str, int]:
    """Shortest round-trip digits ``s`` and exponent ``n`` with x = 0.s × 10^n, for x > 0 (ECMA-262 Number::toString)."""
    t = Decimal(repr(x)).normalize().as_tuple()
    s = "".join(map(str, t.digits))
    return s, len(s) + int(t.exponent)


def js_str(x: float | bool | None) -> str:
    """``String(x)``, i.e. a number interpolated into a template literal."""
    if x.__class__ is int:
        return str(x)
    if x.__class__ is float and -_EXACT_INT < x < _EXACT_INT and x == int(x):
        return str(int(x))  # whole numbers, -0 included ("0")
    if isinstance(x, bool):
        return "true" if x else "false"
    if x is None:
        return "null"
    x = float(x)
    if x != x:
        return "NaN"
    if x in (INF, -INF):
        return "Infinity" if x > 0 else "-Infinity"
    if x == 0:
        return "0"
    if x < 0:
        return "-" + js_str(-x)
    if x < _EXACT_INT and x == math.floor(x):
        return str(int(x))
    s, n = _shortest(x)
    k = len(s)
    if k <= n <= 21:
        return s + "0" * (n - k)
    if 0 < n <= 21:
        return s[:n] + "." + s[n:]
    if -6 < n <= 0:
        return "0." + "0" * (-n) + s
    e = n - 1
    mantissa = s if k == 1 else s[0] + "." + s[1:]
    return f"{mantissa}e{'+' if e >= 0 else '-'}{abs(e)}"


def to_fixed(x: float, digits: int) -> str:
    """``x.toFixed(digits)``: the exact binary value rounded, halves away from zero.

    Python's formatting also rounds the exact value, but halves to even; the two differ only on exact
    halves (0.25 → "0.3" in JavaScript), which are detected with integer arithmetic."""
    if x != x:
        return "NaN"
    if abs(x) >= 1e21:
        return js_str(x)
    if x == 0:
        return f"{0.0:.{digits}f}"  # (-0).toFixed() has no sign
    num, den = float(x).as_integer_ratio()
    twice = 2 * abs(num) * 10**digits
    if twice % den == 0 and (twice // den) % 2 == 1:
        whole = str((twice // den + 1) // 2).rjust(digits + 1, "0")
        text = whole if digits == 0 else f"{whole[:-digits]}.{whole[-digits:]}"
        return "-" + text if x < 0 else text
    return f"{x:.{digits}f}"


_NBSP = " "


def _group(whole: str) -> str:
    return f"{int(whole):,}".replace(",", _NBSP)


def en_za(x: float) -> str:
    """``x.toLocaleString('en-ZA')`` (ICU): no-break-space groups, decimal comma, at most 3 decimals rounded half away from zero."""
    if x != x:
        return "NaN"
    sign = "-" if x < 0 or (x == 0 and math.copysign(1.0, x) < 0) else ""
    a = abs(x)
    if a == INF:
        return sign + "∞"
    if a >= _EXACT_INT:  # a whole number; ICU prints its shortest round-trip digits, not the exact binary value
        s, n = _shortest(a)
        return sign + _group(s + "0" * (n - len(s)))
    if a == math.floor(a):
        return sign + f"{int(a):,}".replace(",", _NBSP)
    q = Decimal(repr(a)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    whole, _, frac = f"{q:f}".partition(".")
    frac = frac.rstrip("0")
    return sign + _group(whole) + ("," + frac if frac else "")


def rand(x: float) -> str:
    """``R${Math.round(x).toLocaleString('en-ZA')}``, the engine's money format."""
    return "R" + en_za(js_round(x))


# --------------------------------------------------------------------------- strings

# ECMAScript WhiteSpace and LineTerminator code points (String.prototype.trim and the \s class).
_JS_SPACE = "\t\n\v\f\r                  　﻿"
_JS_SPACE_RE = re.compile(f"[{re.escape(_JS_SPACE)}]+")


def js_trim(s: str) -> str:
    return s.strip(_JS_SPACE)


def strip_js_space(s: str) -> str:
    """``s.replace(/\\s+/g, '')``."""
    return _JS_SPACE_RE.sub("", s)


# --------------------------------------------------------------------------- dates


class Now:
    """A JavaScript ``Date`` in a UTC process: the instant in ms and its calendar date."""

    __slots__ = ("day", "month", "ms", "year")

    def __init__(self, ms: int):
        self.ms = ms
        d = _EPOCH + timedelta(milliseconds=ms)  # date + timedelta keeps whole days, i.e. floors
        self.year, self.month, self.day = d.year, d.month, d.day

    @classmethod
    def of(cls, today: date | datetime | Now | None) -> Now:
        """``date`` → midnight UTC; a naive ``datetime`` is read as UTC; ``None`` → the current instant."""
        if isinstance(today, Now):
            return today
        if today is None:
            today = datetime.now(UTC)
        if isinstance(today, datetime):
            if today.tzinfo is not None:
                today = today.astimezone(UTC).replace(tzinfo=None)
            delta = today - datetime(1970, 1, 1)
            return cls(delta.days * MS_PER_DAY + delta.seconds * 1000 + delta.microseconds // 1000)
        return cls((today - _EPOCH).days * MS_PER_DAY)

    @property
    def date(self) -> date:
        return date(self.year, self.month, self.day)

    def iso(self) -> str:
        return (datetime(1970, 1, 1) + timedelta(milliseconds=self.ms)).isoformat(timespec="milliseconds") + "Z"


# `${s}T00:00:00` is a valid ECMAScript date-time only for YYYY, YYYY-MM or YYYY-MM-DD (or ±YYYYYY years);
# V8 accepts days up to 31 and rolls them into the next month.
_LOCAL_DATE = re.compile(r"(\d{4}|[+-]\d{6})(?:-(\d{2})(?:-(\d{2}))?)?", re.ASCII)
_ISO_DATE_TIME = re.compile(
    r"(\d{4}|[+-]\d{6})(?:-(\d{2})(?:-(\d{2}))?)?"
    r"(?:[Tt](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?(Z|[+-]\d{2}:\d{2})?)?",
    re.ASCII,
)


def _make_date(y: str, m: str | None, d: str | None) -> date | None:
    year, month, day = int(y), int(m or 1), int(d or 1)
    if not (1 <= month <= 12 and 1 <= day <= 31 and 1 <= year <= 9999):
        return None  # years outside 1–9999 are valid in JavaScript but not representable here
    try:
        return date(year, month, 1) + timedelta(days=day - 1)
    except OverflowError:
        return None


@lru_cache(maxsize=16384)
def parse_local_date(s: str) -> date | None:
    """The calendar date of ``new Date(`${s}T00:00:00`)``, or None where JavaScript gives an invalid date."""
    m = _LOCAL_DATE.fullmatch(s) if s else None
    return _make_date(*m.groups()) if m else None


def date_ms(d: date) -> int:
    return (d - _EPOCH).days * MS_PER_DAY


@lru_cache(maxsize=4096)
def parse_date_ms(s: str) -> float:
    """``new Date(s).getTime()`` for ISO 8601 strings (date-only forms are UTC); NaN otherwise.

    V8 also accepts free-form dates such as "10 May 2016"; the engine only ever stores ISO dates, so
    those read as invalid here (a will date in that form then raises no "will is N years old" finding)."""
    m = _ISO_DATE_TIME.fullmatch(js_trim(s))
    if not m:
        return NAN
    y, mo, d, hh, mi, ss, frac, tz = m.groups()
    day = _make_date(y, mo, d)
    if day is None:
        return NAN
    ms = date_ms(day)
    if hh is not None:
        h, minute, sec, milli = int(hh), int(mi), int(ss or 0), int((frac or "0")[:3].ljust(3, "0"))
        if h > 24 or minute > 59 or sec > 59 or (h == 24 and (minute or sec or milli)):
            return NAN
        ms += ((h * 60 + minute) * 60 + sec) * 1000 + milli
        if tz and tz != "Z":
            sign = 1 if tz[0] == "+" else -1
            ms -= sign * (int(tz[1:3]) * 60 + int(tz[4:6])) * 60_000
    return float(ms)
