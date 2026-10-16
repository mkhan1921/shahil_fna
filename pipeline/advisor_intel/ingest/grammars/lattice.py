"""Applying the grammars over ASR alternatives (§5.3).

A span's words become a small word lattice: one position per word, one arc per candidate text (the
1-best and each ``Word.alts`` entry), weighted by −ln(score). Composing the lattice with a grammar keeps
every path the grammar accepts; each path's cost is the ASR cost plus the grammar cost. Paths are
grouped by the reading they produce (the cheapest derivation per lattice string, summed over lattice
strings), and the masses are normalised into ``Reading.weight``.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import pynini

from ...models.transcript import Reading, SpanKind
from .age import age_grammar
from .amount import amount_grammar
from .catalog import ProductName
from .product import product_grammar
from .tokens import normalize_token

N_SHORTEST = 64
MAX_READINGS = 32
MIN_SCORE = 1e-6

Arc = tuple[str, float]  # (text, score in (0, 1])


@dataclass
class GrammarResult:
    """Readings of one span under one grammar."""

    kind: SpanKind
    readings: list[Reading] = field(default_factory=list)
    accepted: bool = False
    n_paths: int = 0
    best_input: str | None = None  # the lattice string of the best path


def grammar_for(kind: SpanKind, extra_products: tuple[ProductName, ...] = ()) -> pynini.Fst:
    if kind == "amount":
        return amount_grammar()
    if kind == "age":
        return age_grammar()
    return product_grammar(extra_products)


def build_lattice(positions: Sequence[Sequence[Arc]]) -> pynini.Fst | None:
    """A word lattice: positions joined by single spaces, each a union of weighted candidate texts."""
    parts: list[pynini.Fst] = []
    for arcs in positions:
        best: dict[str, float] = {}
        for text, score in arcs:
            t = normalize_token(text)
            if not t:
                continue
            cost = -math.log(min(1.0, max(float(score), MIN_SCORE)))
            best[t] = min(cost, best.get(t, math.inf))
        if not best:
            continue
        parts.append(pynini.union(*(pynini.accep(pynini.escape(t), weight=c) for t, c in best.items())))
    if not parts:
        return None
    lattice = parts[0]
    for p in parts[1:]:
        lattice = lattice + pynini.accep(" ") + p
    lattice = lattice.optimize()
    lattice.arcsort("olabel")
    return lattice


def compose(lattice: pynini.Fst, grammar: pynini.Fst) -> pynini.Fst | None:
    out = pynini.compose(lattice, grammar)
    return out if out.num_states() > 0 and out.start() != pynini.NO_STATE_ID else None


def accepts(positions: Sequence[Sequence[Arc]], kind: SpanKind, extra_products: tuple[ProductName, ...] = ()) -> bool:
    lattice = build_lattice(positions)
    return lattice is not None and compose(lattice, grammar_for(kind, extra_products)) is not None


def _paths(composed: pynini.Fst, nshortest: int) -> list[tuple[str, str, float]]:
    best = pynini.shortestpath(composed, nshortest=nshortest)
    out = []
    it = best.paths(input_token_type="byte", output_token_type="byte")
    while not it.done():
        out.append((it.istring(), it.ostring(), float(str(it.weight()))))
        it.next()
    return out


def parse_output(out: str) -> tuple[str, dict[str, list[str]]]:
    """'A;v=250*1000;s=implied_thousand;' → ('A', {'v': ['250*1000'], 's': ['implied_thousand']})."""
    parts = [p for p in out.split(";") if p]
    tag, fields = parts[0], defaultdict(list)
    for p in parts[1:]:
        k, _, v = p.partition("=")
        fields[k].append(v)
    return tag, fields


def evaluate(terms: list[str]) -> float:
    """Sum of 'mantissa*multiplier' terms, exactly."""
    total = Decimal(0)
    for term in terms:
        m, _, x = term.partition("*")
        try:
            total += Decimal(m) * (Decimal(x) if x else Decimal(1))
        except InvalidOperation as e:  # pragma: no cover - the grammar only emits numbers
            raise ValueError(f"bad grammar output term {term!r}") from e
    return float(total)


ReadingKey = tuple


def reading_key(kind: SpanKind, out: str) -> ReadingKey:
    _, f = parse_output(out)
    if kind == "amount":
        return (
            "amount",
            round(evaluate(f["v"]), 6),
            (f.get("c") or [None])[0],
            (f.get("p") or ["none"])[0],
            (f.get("s") or [None])[0],
            bool(f.get("r")),
        )
    if kind == "age":
        return ("age", round(evaluate(f["v"]), 6), (f.get("k") or [None])[0])
    return ("product", (f.get("t") or ["institution"])[0], (f.get("pv") or [None])[0], (f.get("k") or [None])[0])


def reading_from_key(key: ReadingKey, weight: float) -> Reading:
    if key[0] == "amount":
        _, value, currency, period, scale, is_rate = key
        return Reading(value=value, currency=currency, period=period, scale_source=scale, weight=weight, is_rate=is_rate)
    if key[0] == "age":
        _, value, lex = key
        return Reading(value=value, lexicon_key=lex, weight=weight)
    _, ptype, provider, lex = key
    return Reading(value=ptype, provider=provider, lexicon_key=lex, weight=weight)


def readings_for_lattice(lattice: pynini.Fst | None, kind: SpanKind, *, extra_products: tuple[ProductName, ...] = (),
                         nshortest: int = N_SHORTEST) -> GrammarResult:
    if lattice is None:
        return GrammarResult(kind=kind)
    composed = compose(lattice, grammar_for(kind, extra_products))
    if composed is None:
        return GrammarResult(kind=kind)
    paths = _paths(composed, nshortest)
    best_cost: dict[tuple[str, ReadingKey], float] = {}
    for istring, ostring, cost in paths:
        k = (istring, reading_key(kind, ostring))
        best_cost[k] = min(cost, best_cost.get(k, math.inf))
    mass: dict[ReadingKey, float] = defaultdict(float)
    for (_, key), cost in best_cost.items():
        mass[key] += math.exp(-cost)
    total = sum(mass.values())
    ranked = sorted(mass.items(), key=lambda kv: -kv[1])[:MAX_READINGS]
    readings = [reading_from_key(k, m / total) for k, m in ranked]
    best_input = min(paths, key=lambda p: p[2])[0] if paths else None
    return GrammarResult(kind=kind, readings=readings, accepted=True, n_paths=len(paths), best_input=best_input)


def lattice_readings(positions: Sequence[Sequence[Arc]], kind: SpanKind, *, extra_products: tuple[ProductName, ...] = (),
                     nshortest: int = N_SHORTEST) -> GrammarResult:
    """Every reading the grammar accepts over a word lattice, normalised (§5.3)."""
    return readings_for_lattice(build_lattice(positions), kind, extra_products=extra_products, nshortest=nshortest)


def parse(text: str, kind: SpanKind, *, extra_products: tuple[ProductName, ...] = ()) -> GrammarResult:
    """Readings of one string (no alternatives), e.g. ``parse("two fifty", "amount")``."""
    words = [w for w in text.split() if w]
    return lattice_readings([[(w, 1.0)] for w in words], kind, extra_products=extra_products)


def merge_readings(primary: list[Reading], secondary: list[Reading], secondary_share: float) -> list[Reading]:
    """Mix two normalised reading sets; readings present in both add their masses (§5.3 ITN candidate)."""
    mass: dict[tuple, float] = defaultdict(float)
    proto: dict[tuple, Reading] = {}
    for share, rs in ((1.0 - secondary_share if primary else 0.0, primary), (secondary_share if primary else 1.0, secondary)):
        for r in rs:
            k = reading_identity(r)
            mass[k] += share * r.weight
            proto.setdefault(k, r)
    total = sum(mass.values()) or 1.0
    out = [proto[k].model_copy(update={"weight": m / total}) for k, m in mass.items()]
    return sorted(out, key=lambda r: -r.weight)[:MAX_READINGS]


def reading_identity(r: Reading) -> tuple:
    """What makes two readings the same reading (weight aside)."""
    v = round(r.value, 6) if isinstance(r.value, float) else r.value
    return (v, r.currency, r.period, r.scale_source, r.is_rate, r.lexicon_key, r.provider)


def same_value(a: Reading, b: Reading, rel_tol: float = 1e-6) -> bool:
    """Two readings name the same quantity (amounts and ages) or the same product and provider."""
    if isinstance(a.value, str) or isinstance(b.value, str):
        return a.value == b.value and (a.provider == b.provider or not a.provider or not b.provider)
    return math.isclose(float(a.value), float(b.value), rel_tol=rel_tol, abs_tol=1e-9) and a.is_rate == b.is_rate
