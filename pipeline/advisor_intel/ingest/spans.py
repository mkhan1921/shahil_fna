"""Numeric and product spans (§5.3): find amount, age and product mentions and attach their grammar readings.

For each segment the finder looks at every word's 1-best spoken form *and* its alternatives, so a number
heard only in the alternatives ("for" / "four" thousand) is still found; a weak alternative may sit inside
a span but cannot start or end one. Each run of number-ish words is cut into grammar-accepted windows by a
small dynamic programme that covers the most number words with the fewest windows, never leaves a scale
word orphaned after its number and never splits "fifty | five". Each window's lattice (1-best plus
``Word.alts``) is composed with the grammar and every accepted path is kept. The provider's own ITN
(``Word.text`` when ``raw_spoken`` is set) is parsed too and kept as one more candidate (20 % of the
mass), never alone: a span whose spoken form no grammar path accepts is ``accepted=False`` and flagged
``low_asr`` for the second pass (§5.6).

Product spans are found first, so "regulation twenty eight" and "ten x" are products, not amounts. Numbers
that are years ("in twenty nineteen"), counts and durations ("two kids", "eight years") or clock times
("at two thirty") are left alone: they are not amounts. A number is an age when the grammar says so
("late fifties", "grade nine", "forty years old") or the words before it do ("retire at sixty", "she's 14",
"I turn fifty", "ek is vyf en vyftig"). A window never runs past a sentence end the provider punctuated, and past
a comma only when a scale word continues the number, so "sixty five, fifty two" stays two numbers while
"one million, two hundred and fifty thousand" stays one.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from ..models.transcript import NumericSpan, Reading, Segment, SpanKind, Transcript, Word
from .grammars import ProductName, merge_readings
from .grammars.age import GRADE_WORDS, STRONG_AGE_MARKERS, age_marker_vocabulary
from .grammars.amount import GLUE_WORDS, period_vocabulary, scale_vocabulary
from .grammars.catalog import edge_tokens, product_vocabulary
from .grammars.lattice import Arc, GrammarResult, accepts, lattice_readings, parse
from .grammars.numbers import number_vocabulary
from .grammars.tokens import FILLERS, normalize_token

ITN_SHARE = 0.2  # share of a span's reading mass given to the provider's ITN form
ALT_ANCHOR = 0.35  # an alternative this close to the 1-best score can start a span on its own ("for" heard, "four" said)
MAX_WINDOW = 14  # words
SCALE_AMBIGUITY_RATIO = 5.0
MIN_AMBIGUOUS_WEIGHT = 0.1

_DIGITS = re.compile(r"^(?:r|zar|us\$|\$|£|€)?\d[\d,.\s]*(?:k|m|mn|mil|bn|%)?$")
NUMBER_WORDS = number_vocabulary()
SCALE_WORDS = scale_vocabulary()
PERIOD_WORDS = period_vocabulary()
AGE_WORDS = age_marker_vocabulary()
STRONG_SCALES = frozenset({"thousand", "duisend", "million", "miljoen", "billion", "miljard", "mil", "mill", "grand", "bar", "bars"})

NEVER_START = frozenset(
    {
        "and",
        "of",
        "per",
        "en",
        "comma",
        "komma",
        "punt",
        "percent",
        "cent",
        "%",
        "persent",
        "procent",
        "pc",
        "month",
        "year",
        "annum",
        "mth",
        "yr",
        "maand",
        "jaar",
        "monthly",
        "yearly",
        "annually",
        "maandeliks",
        "jaarliks",
        "pm",
        "pa",
        "rand",
        "rands",
        "randte",
        "dollars",
        "dollar",
        "pounds",
        "pound",
        "sterling",
        "quid",
        "euros",
        "euro",
        "bucks",
        "pond",
        "old",
        "oud",
        "ish",
        "years",
        "yrs",
        "off",
        "sum",
        "go",
        "every",
        "each",
        "elke",
        "once",
        "lump",
        "one go",
        "k",
        "m",
    }
)
NEVER_END = frozenset(
    {
        "and",
        "of",
        "per",
        "en",
        "a",
        "an",
        "'n",
        "n",
        "point",
        "comma",
        "komma",
        "punt",
        "r",
        "$",
        "£",
        "€",
        "aged",
        "turning",
        "turns",
        "turned",
        "early",
        "mid",
        "middle",
        "late",
        "vroeë",
        "vroee",
        "middel",
        "laat",
        "grade",
        "graad",
        "standard",
        "standerd",
        "in",
        "every",
        "each",
        "elke",
        "us",
        "lump",
        "once",
    }
)
AGE_PREV_1 = frozenset({"aged", "age", "turning", "turns", "turned", "turn", "ouderdom"})
AGE_PREV_2 = frozenset(
    {
        ("retire", "at"),
        ("retiring", "at"),
        ("retirement", "at"),
        ("at", "age"),
        ("age", "of"),
        ("till", "age"),
        ("until", "age"),
        ("to", "age"),
        ("retire", "by"),
        ("work", "until"),
        ("work", "till"),
        ("aftree", "op"),
        ("i", "am"),
        ("ek", "is"),  # Afrikaans: "ek is vyf en vyftig"
        ("ek", "word"),  # "ek word sestig" (I turn sixty)
        ("hy", "is"),
        ("sy", "is"),
        ("hy", "word"),
        ("sy", "word"),
    }
)
AGE_PRONOUNS = frozenset({"i'm", "he's", "she's", "they're", "we're", "you're", "hy's", "sy's"})
PERSON_WORDS = frozenset(
    {
        "son",
        "daughter",
        "boy",
        "girl",
        "kid",
        "child",
        "wife",
        "husband",
        "mother",
        "mom",
        "mum",
        "dad",
        "father",
        "he",
        "she",
        "brother",
        "sister",
        "seun",
        "dogter",
        "hy",
        "sy",
    }
)
YEAR_PREV = frozenset({"in", "by", "since", "from", "until", "till", "of", "before", "after", "around", "year", "jaar", "sedert"})
COUNT_NOUNS = frozenset(
    {
        "years",
        "year",
        "months",
        "weeks",
        "week",
        "days",
        "day",
        "kids",
        "children",
        "child",
        "sons",
        "daughters",
        "times",
        "people",
        "cars",
        "houses",
        "policies",
        "funds",
        "accounts",
        "grandchildren",
        "dependants",
        "dependents",
        "hours",
        "minutes",
        "jaar",
        "maande",
        "kinders",
        "keer",
        "percent",
        "%",
        "ago",
        "of",
        "things",
        "items",
        "members",
        "steps",
    }
)
TIME_PREV = frozenset({"at", "by", "till", "until", "from", "before", "after", "om"})
TIME_NEXT = frozenset({"am", "pm", "o'clock", "oclock", "uur"})
_CLOCK_HOURS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve")
HOUR_WORDS = frozenset([*_CLOCK_HOURS, *(str(h) for h in range(1, 13))])
MINUTE_WORDS = frozenset([
    "oh", "zero", *_CLOCK_HOURS, "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
    "forty", "fifty", "quarter", "half",
]) | {f"{m:02d}" for m in range(60)}  # fmt: skip


@dataclass
class Tok:
    """A word as the finder sees it."""

    arcs: list[Arc]  # spoken 1-best first, then alternatives
    itn: str | None  # the provider's ITN text when it differs from the spoken form
    conf: float
    norm: str  # normalised spoken 1-best
    variants: frozenset[str]  # normalised single tokens of every arc and the ITN text

    @property
    def filler(self) -> bool:
        return self.norm in FILLERS


def tok_from_word(w: Word) -> Tok:
    spoken = w.raw_spoken or w.text
    arcs: list[Arc] = [(spoken, max(w.conf, 1e-3))] + [(t, s) for t, s in w.alts if t]
    itn = w.text if w.raw_spoken and w.raw_spoken.strip() != w.text.strip() else None
    variants: set[str] = set()
    for t, _ in arcs + ([(itn, 1.0)] if itn else []):
        variants.update(normalize_token(t).split())
    return Tok(arcs=arcs, itn=itn, conf=w.conf, norm=normalize_token(spoken), variants=frozenset(variants))


def tok_from_text(text: str, conf: float = 1.0) -> Tok:
    norm = normalize_token(text)
    return Tok(arcs=[(text, conf)], itn=None, conf=conf, norm=norm, variants=frozenset(norm.split()))


def _numberish(v: str) -> bool:
    return v in NUMBER_WORDS or bool(_DIGITS.match(v))


WEAK_ANCHORS = frozenset({"oh"})  # "oh" is a digit only inside a number ("one oh five"), an interjection on its own


def _strong_token(v: str) -> bool:
    return (_numberish(v) or v in STRONG_SCALES or v in STRONG_AGE_MARKERS) and v not in WEAK_ANCHORS


def _supported(t: Tok) -> set[str]:
    """Normalised tokens of the 1-best, the ITN form and the alternatives scored within ``ALT_ANCHOR`` of the 1-best."""
    out = set(t.norm.split())
    if t.itn:
        out.update(normalize_token(t.itn).split())
    best = t.arcs[0][1] if t.arcs else 1.0
    for a, s in t.arcs[1:]:
        if s >= ALT_ANCHOR * best:
            out.update(normalize_token(a).split())
    return out


def _strong(t: Tok) -> bool:
    """A word that can anchor a numeric span: its 1-best or ITN form is a number, or an alternative with real support is."""
    return any(_strong_token(v) for v in _supported(t))


def _member_token(v: str) -> bool:
    return (
        _numberish(v) or v in SCALE_WORDS or v in PERIOD_WORDS or v in GLUE_WORDS or v in AGE_WORDS or v in ("per", "of", "us", "'n", "n")
    )


def _numeric_member(t: Tok) -> bool:
    """A word that can sit inside a numeric span (any alternative counts: the lattice weighs it)."""
    return t.filler or any(_member_token(v) for v in t.variants)


def _can_start(t: Tok) -> bool:
    """Span edges need real support: a weak homophone alternative ("for" / "four") must not stretch a span."""
    return not t.filler and any(_member_token(v) and v not in NEVER_START for v in _supported(t))


def _can_end(t: Tok) -> bool:
    return not t.filler and any(_member_token(v) and v not in NEVER_END for v in _supported(t))


def _positions(toks: Sequence[Tok]) -> list[list[Arc]]:
    """Lattice positions of a window, fillers dropped ("two hundred, uh, fifty")."""
    return [t.arcs for t in toks if not t.filler]


def _itn_text(toks: Sequence[Tok]) -> str | None:
    if not any(t.itn for t in toks):
        return None
    return " ".join(t.itn or t.arcs[0][0] for t in toks if not t.filler)


@dataclass
class Found:
    start: int
    end: int  # exclusive
    kind: SpanKind
    result: GrammarResult  # readings over the spoken lattice (possibly not accepted)
    itn: str | None = None
    readings: list[Reading] | None = None  # final readings (spoken + ITN)
    accepted: bool = True


def _grade_r(toks: Sequence[Tok], k: int) -> bool:
    """'grade R' (the reception year): the 'r' is the number of this age phrase, not a currency prefix."""
    return toks[k].norm == "r" and k > 0 and toks[k - 1].norm in GRADE_WORDS


def _strong_at(toks: Sequence[Tok], k: int) -> bool:
    return _strong(toks[k]) or _grade_r(toks, k)


_HARD_END = (".", "?", "!", ";", ":")
CONTINUATION = STRONG_SCALES | {"hundred", "honderd"}  # after a pause, these words carry a number on ("…, two hundred thousand")


def _hard_barrier(t: Tok) -> bool:
    """A word the provider punctuated as a sentence end: a numeric span may end on it, never run past it."""
    raw = t.arcs[0][0].rstrip()
    return raw.endswith(_HARD_END) and not raw.endswith("...")


def _soft_barrier(t: Tok) -> bool:
    """A comma or a trailing-off: a pause. A span runs past it only when what follows continues the number with a
    scale word ("one million, two hundred thousand"), not when a new number starts ("sixty five, fifty two")."""
    raw = t.arcs[0][0].rstrip()
    return raw.endswith((",", "..."))


def _runs(toks: Sequence[Tok], member, strong, taken: set[int]) -> list[tuple[int, int]]:
    """Maximal stretches of member tokens outside ``taken`` that hold at least one strong token (``strong`` takes an index)."""
    runs, i = [], 0
    while i < len(toks):
        if i in taken or not member(toks[i]):
            i += 1
            continue
        j = i
        while j < len(toks) and j not in taken and member(toks[j]):
            j += 1
        if any(strong(k) for k in range(i, j)):
            runs.append((i, j))
        i = j
    return runs


def find_products(toks: Sequence[Tok], extra: tuple[ProductName, ...] = ()) -> list[Found]:
    vocab = product_vocabulary(extra)
    starts, ends = edge_tokens(extra)
    found = []
    for a, b in _runs(toks, lambda t: any(v in vocab for v in t.variants), lambda k: True, set()):
        i = a
        while i < b:
            hit = None
            if toks[i].variants & starts:
                for j in range(min(b, i + MAX_WINDOW), i, -1):
                    if not toks[j - 1].variants & ends:
                        continue
                    if accepts(_positions(toks[i:j]), "product", extra):
                        hit = j
                        break
            if hit:
                res = lattice_readings(_positions(toks[i:hit]), "product", extra_products=extra)
                found.append(Found(i, hit, "product", res, readings=res.readings))
                i = hit
            else:
                i += 1
    return found


def _context(toks: Sequence[Tok], i: int, n: int) -> list[str]:
    return [t.norm for t in toks[max(0, i - n) : i] if not t.filler]


AGE_ADVERBS = frozenset(
    {"only", "just", "about", "almost", "nearly", "around", "roughly", "already", "now", "still", "barely", "net", "omtrent"}
)
NOT_NAMES = frozenset({"that", "this", "there", "what", "which", "it", "the", "and", "so", "but", "then", "now", "here", "who"})


def _is_name(tok: Tok) -> bool:
    """A Titlecase word that is not a sentence starter or a product: 'Kagiso is seven'."""
    raw = tok.arcs[0][0].strip(",.?!\"'")
    return len(raw) >= 3 and raw[0].isupper() and raw[1:].islower() and tok.norm not in NOT_NAMES and tok.norm not in product_vocabulary()


def _is_age_context(toks: Sequence[Tok], i: int, j: int) -> bool:
    prev = _context(toks, i, 3)
    if prev and prev[-1] in AGE_PREV_1:
        return True
    if len(prev) >= 2 and tuple(prev[-2:]) in AGE_PREV_2:
        return True
    if prev and prev[-1] in AGE_PRONOUNS:
        return True
    if len(prev) >= 2 and prev[-2] in AGE_PRONOUNS and prev[-1] in AGE_ADVERBS:  # "I'm only thirty seven"
        return True
    if len(prev) >= 3 and tuple(prev[-3:-1]) in AGE_PREV_2 and prev[-1] in AGE_ADVERBS:  # "I am almost fifty", "ek is nou sestig"
        return True
    if len(prev) >= 2 and prev[-1] in ("is", "turns", "was", "turned") and prev[-2] in PERSON_WORDS:
        return True
    before = [t for t in toks[max(0, i - 3) : i] if not t.filler]
    return len(before) >= 2 and before[-1].norm in ("is", "turns", "was", "turned") and _is_name(before[-2])


def _bare(r: Reading) -> bool:
    return (
        r.currency is None
        and r.period == "none"
        and not r.is_rate
        and r.scale_source in ("literal", "digits", "implied_thousand", "implied_million")
    )


SCHEDULING_WORDS = frozenset(
    {
        "meet",
        "meeting",
        "call",
        "see",
        "speak",
        "talk",
        "chat",
        "start",
        "starts",
        "finish",
        "appointment",
        "book",
        "booked",
        "back",
        "tomorrow",
        "today",
        "tonight",
        "morning",
        "afternoon",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "o'clock",
        "vergadering",
        "bel",
    }
)


def _is_clock_time(toks: Sequence[Tok], i: int, j: int) -> bool:
    """'two thirty pm', 'seven o'clock', 'let's meet at two thirty' — but not 'the bond is sitting at two fifteen'."""
    words = [t.norm for t in toks[i:j] if not t.filler]
    prev = _context(toks, i, 4)
    nxt = [t.norm for t in toks[j : j + 3] if not t.filler]
    marked = bool(words) and words[-1] in ("am", "pm")
    core = words[:-1] if marked else words
    clock = 1 <= len(core) <= 3 and core[0] in HOUR_WORDS and all(w in MINUTE_WORDS for w in core[1:])
    if not clock:
        return False
    if marked or (nxt and nxt[0] in TIME_NEXT):
        return True
    scheduled = any(w in SCHEDULING_WORDS for w in prev + nxt)
    return bool(prev) and prev[-1] in TIME_PREV and len(core) >= 2 and scheduled


def _skip_amount(toks: Sequence[Tok], i: int, j: int, res: GrammarResult) -> bool:
    """Years, counts, durations and clock times are numbers but not amounts."""
    nxt = toks[j].norm if j < len(toks) else ""
    if _is_clock_time(toks, i, j):
        return True
    if j - i == 2 and toks[i].norm in ("a", "an") and nxt == "of":  # "a bar of soap", "a grand of"
        return True
    if not res.readings or not all(_bare(r) for r in res.readings):
        return False
    literal = [r for r in res.readings if r.scale_source in ("literal", "digits")]
    prev = _context(toks, i, 1)
    year = literal and any(1900 <= float(r.value) <= 2100 and float(r.value).is_integer() for r in literal)
    if year and ((prev and prev[-1] in YEAR_PREV) or any(_DIGITS.match(v) and len(v) == 4 for v in toks[i].variants)):
        return True
    if nxt in COUNT_NOUNS or nxt.split(" ")[0] in COUNT_NOUNS:
        return True
    return j - i == 1 and bool(literal) and float(literal[0].value) < 10  # "one more thing", "two of them": counts, pronouns


_UNITS = frozenset(["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"])
_TEENS = frozenset(["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"])
_DECADES = frozenset(["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"])


def spoken_year_positions(toks: Sequence[Tok]) -> set[int]:
    """Positions of spoken years ("twenty twenty three", "twenty nineteen", "nineteen oh five"), which are not amounts.

    "twenty …" forms are never well-formed amounts, so they are always years; "nineteen …" forms are
    years only after a year word ("in nineteen ninety"), since "nineteen ninety nine" is also a price.
    """
    out: set[int] = set()
    i, n = 0, len(toks)
    while i + 1 < n:
        head, tail, end = toks[i].norm, toks[i + 1].norm, None
        if head in ("twenty", "nineteen"):
            if tail == "oh" and i + 2 < n and toks[i + 2].norm in _UNITS:
                end = i + 3
            elif tail in _TEENS:
                end = i + 2
            elif tail in _DECADES:
                end = i + 3 if i + 2 < n and toks[i + 2].norm in _UNITS else i + 2
        prev = _context(toks, i, 1)
        if end is not None and (head == "twenty" or (prev and prev[-1] in YEAR_PREV)):
            out.update(range(i, end))
            i = end
        else:
            i += 1
    return out


AGE_CHAIN = frozenset(
    {"or", "maybe", "to", "and", "probably", "perhaps", "even", "say", "like", "-", "of", "nee", "dalk"}
)  # "of" is Afrikaans "or"


def _chained_age(toks: Sequence[Tok], i: int, found: list[Found]) -> bool:
    """'retire at sixty five, maybe sixty': a number right after an age span, joined by a hedge, is an age too."""
    if not found or found[-1].kind != "age" or i - found[-1].end > 3:
        return False
    return all(t.filler or t.norm in AGE_CHAIN for t in toks[found[-1].end : i])


MAX_AGE_WINDOW = 5  # age phrases are short ("in her late fifties", "forty two years old")


def _cover_run(toks: Sequence[Tok], a: int, b: int) -> list[tuple[int, int, bool, bool]]:
    """Cut a run into grammar-accepted windows: cover the most number words, then use the fewest windows.

    A small dynamic programme over the run, so "a hundred thousand and fifty five thousand" is two amounts
    rather than 100,055 and an orphaned "thousand". Returns (start, end, amount_ok, age_ok) per window.
    """
    n = b - a
    strong = [_strong_at(toks, k) for k in range(a, b)]
    has_number = [any(_numberish(v) and v not in WEAK_ANCHORS and v not in STRONG_SCALES for v in _supported(toks[k])) for k in range(a, b)]
    hard = [_hard_barrier(toks[k]) for k in range(a, b)]
    soft = [_soft_barrier(toks[k]) for k in range(a, b)]
    continues = [any(v in CONTINUATION for v in toks[k].variants) for k in range(a, b)]
    cache: dict[tuple[int, int], tuple[bool, bool]] = {}

    def crosses_a_pause(i: int, j: int) -> bool:
        if any(hard[i : j - 1]):
            return True
        return any(soft[k] and not any(continues[k + 1 : j]) for k in range(i, j - 1))

    def window_has_age_marker(i: int, j: int) -> bool:
        return any(v in STRONG_AGE_MARKERS for t in toks[a + i : a + j] for v in t.variants)

    def splits_compound(i: int) -> bool:
        """A window starting at a unit right after a decade would split "fifty | five" (alternatives count)."""
        return i > 0 and toks[a + i].norm in _UNITS and any(v in _DECADES for v in _supported(toks[a + i - 1]))

    def ok(i: int, j: int) -> tuple[bool, bool]:
        if (i, j) not in cache:
            pos = _positions(toks[a + i : a + j])
            amount = bool(pos) and accepts(pos, "amount")
            age = bool(pos) and j - i <= MAX_AGE_WINDOW and accepts(pos, "age")
            cache[(i, j)] = (amount, age)
        return cache[(i, j)]

    best: list[tuple[float, tuple]] = [(0.0, ())] * (n + 1)
    for i in range(n - 1, -1, -1):
        choice: tuple[float, tuple] = (best[i + 1][0], ("skip",))
        if _can_start(toks[a + i]) and not splits_compound(i):
            for j in range(min(n, i + MAX_WINDOW), i, -1):
                if crosses_a_pause(i, j):
                    continue  # "sixty five, fifty two" is two numbers; "one million, two hundred thousand" is one
                if not (_can_end(toks[a + j - 1]) or _grade_r(toks, a + j - 1)):
                    continue
                amount_ok, age_ok = ok(i, j)
                if not (amount_ok or age_ok):
                    continue
                if i > 0 and has_number[i - 1] and not any(has_number[i:j]) and not window_has_age_marker(i, j):
                    continue  # a scale word straight after a number belongs to it ("… fifty five thousand"), never alone
                score = 2 * sum(strong[i:j]) - 1 + 0.01 * (j - i) + best[j][0]  # ties go to the longer window ("grade nine")
                if score > choice[0]:
                    choice = (score, ("window", j, amount_ok, age_ok))
        best[i] = choice
    out, i = [], 0
    while i < n:
        step = best[i][1]
        if step[0] == "skip":
            i += 1
        else:
            out.append((a + i, a + step[1], step[2], step[3]))
            i = step[1]
    return out


def find_numbers(toks: Sequence[Tok], taken: set[int] | None = None) -> list[Found]:
    """Amount and age spans (and unaccepted numeric stretches) outside ``taken`` positions."""
    taken = taken or set()
    found: list[Found] = []
    for a, b in _runs(toks, _numeric_member, lambda k: _strong_at(toks, k), taken):
        covered: set[int] = set()
        for i, j, amount_ok, age_ok in _cover_run(toks, a, b):
            window_words = {v for t in toks[i:j] for v in t.variants}
            is_age = age_ok and (
                not amount_ok or bool(window_words & STRONG_AGE_MARKERS) or _is_age_context(toks, i, j) or _chained_age(toks, i, found)
            )
            kind: SpanKind = "age" if is_age else "amount"
            res = lattice_readings(_positions(toks[i:j]), kind)
            covered.update(range(i, j))
            if kind == "amount" and _skip_amount(toks, i, j, res):
                continue
            found.append(Found(i, j, kind, res))
        # Strong number words no accepted window covers: an unaccepted span for the second pass.
        k = a
        while k < b:
            if k in covered or not _strong_at(toks, k):
                k += 1
                continue
            m = k + 1
            while m < b and m not in covered and (_strong_at(toks, m) or (m + 1 < b and m + 1 not in covered and _strong_at(toks, m + 1))):
                m += 1
            kind = "age" if any(v in STRONG_AGE_MARKERS for t in toks[k:m] for v in t.variants) else "amount"
            found.append(Found(k, m, kind, GrammarResult(kind=kind), accepted=False))
            k = m
    return sorted(found, key=lambda f: f.start)


def finalize(found: Found, toks: Sequence[Tok], extra: tuple[ProductName, ...] = ()) -> Found:
    """Add the provider ITN candidate and settle ``accepted``."""
    itn = _itn_text(toks[found.start : found.end])
    found.itn = itn
    spoken = found.result.readings if found.result.accepted else []
    itn_readings = parse(itn, found.kind, extra_products=extra).readings if itn else []
    if spoken:
        found.readings = merge_readings(spoken, itn_readings, ITN_SHARE) if itn_readings else spoken
        found.accepted = True
    else:
        found.readings = merge_readings([], itn_readings, ITN_SHARE) if itn_readings else []
        found.accepted = False
    return found


def scale_ambiguous(readings: Sequence[Reading]) -> bool:
    """Amount readings carrying real weight that differ in scale (≥ 5×)."""
    vals = [
        float(r.value)
        for r in readings
        if isinstance(r.value, float) and not r.is_rate and r.weight >= MIN_AMBIGUOUS_WEIGHT and r.value > 0
    ]
    return len(vals) >= 2 and max(vals) / min(vals) >= SCALE_AMBIGUITY_RATIO


def find_in_tokens(toks: Sequence[Tok], extra: tuple[ProductName, ...] = ()) -> list[Found]:
    """Product, amount and age spans of one token sequence, each with final readings."""
    products = find_products(toks, extra)
    taken = {k for f in products for k in range(f.start, f.end)} | spoken_year_positions(toks)
    numbers = find_numbers(toks, taken)
    return [finalize(f, toks, extra) for f in sorted(products + numbers, key=lambda f: f.start)]


def segment_spans(segment: Segment, *, extra_products: tuple[ProductName, ...] = ()) -> list[NumericSpan]:
    """The spans of one segment (span ids are assigned by ``annotate_transcript``)."""
    toks = [tok_from_word(w) for w in segment.words]
    out = []
    for f in find_in_tokens(toks, extra_products):
        words = segment.words[f.start : f.end]
        flags = set()
        if not f.accepted:
            flags.add("low_asr")
        if f.kind == "amount" and scale_ambiguous(f.readings or []):
            flags.add("scale_ambiguous")
        out.append(
            NumericSpan(
                span_id="",
                segment_id=segment.segment_id,
                kind=f.kind,
                word_start=f.start,
                word_end=f.end,
                surface=" ".join(w.text for w in words),
                readings=f.readings or [],
                provider_itn=f.itn,
                accepted=f.accepted,
                mean_conf=sum(w.conf for w in words) / len(words) if words else 0.0,
                flags=flags,
            )
        )
    return out


def span_interval(t: Transcript | Segment, span: NumericSpan) -> tuple[float, float]:
    seg = t if isinstance(t, Segment) else t.segment(span.segment_id)
    words = seg.words[span.word_start : span.word_end]
    return (words[0].t_start, words[-1].t_end) if words else (seg.t_start, seg.t_end)


def _overlap_with_others(transcript: Transcript, seg: Segment, t0: float, t1: float) -> float:
    total = 0.0
    for other in transcript.segments:
        if other.speaker_label == seg.speaker_label or other.t_end <= t0 or other.t_start >= t1:
            continue
        for w in other.words:
            total += max(0.0, min(t1, w.t_end) - max(t0, w.t_start))
    return total


def annotate_transcript(transcript: Transcript, *, extra_products: Sequence[ProductName] = (), crosstalk_min_s: float = 0.1) -> Transcript:
    """Find every span, number them n0001…, flag crosstalk over them and fill ``Word.numeric_candidates``."""
    extra = tuple(extra_products)
    spans: list[NumericSpan] = []
    segments = []
    for seg in transcript.segments:
        seg = seg.model_copy(deep=True)
        for w in seg.words:
            w.numeric_candidates = []
        for s in segment_spans(seg, extra_products=extra):
            if s.kind in ("amount", "age"):
                values = []
                for r in s.readings:
                    v = float(r.value)
                    if not any(math.isclose(v, x) for x in values):
                        values.append(v)
                for w in seg.words[s.word_start : s.word_end]:
                    if any(_numberish(v) for v in tok_from_word(w).variants):
                        w.numeric_candidates = values
            spans.append(s)
        segments.append(seg)
    out = transcript.model_copy(update={"segments": segments})
    for k, s in enumerate(spans, start=1):
        s.span_id = f"n{k:04d}"
        seg = out.segment(s.segment_id)
        t0, t1 = span_interval(seg, s)
        if _overlap_with_others(out, seg, t0, t1) >= crosstalk_min_s:
            s.flags.add("crosstalk")
    out.numeric_spans = spans
    return out


def canonical_text(text: str, *, extra_products: Sequence[ProductName] = ()) -> str:
    """Text with every recognised span replaced by its top reading, for deterministic span alignment (§7.3).

    "we put two hundred and fifty thousand in the Allan Gray RA" → "we put 250000 in the retirement-annuity@Allan Gray".
    """
    words = text.split()
    toks = [tok_from_text(w) for w in words]
    out, i = [], 0
    for f in find_in_tokens(toks, tuple(extra_products)):
        out.extend(normalize_token(w) for w in words[i : f.start])
        top = (f.readings or [None])[0]
        if top is None:
            out.extend(normalize_token(w) for w in words[f.start : f.end])
        elif f.kind == "product":
            out.append(str(top.value) + (f"@{top.provider}" if top.provider else ""))
        else:
            v = float(top.value)
            out.append(f"{int(v)}" if v.is_integer() else f"{v:g}")
        i = f.end
    out.extend(normalize_token(w) for w in words[i:])
    return " ".join(t for t in out if t)
