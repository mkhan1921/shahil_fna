"""Hedge lookup (spec §7.3 step 5): surface hedge terms → lexicon strengths, a calibration feature (§9.2)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from pydantic import BaseModel

from ..models.facts import Hedge
from .loader import Lexicon
from .phrases import canon


class HedgeResult(BaseModel):
    """The fact's hedges, plus any lexicon miss or near-miss among them (logged for the weekly review)."""

    hedges: list[Hedge]
    flags: list[str] = []  # lexicon_miss / lexicon_near_miss
    lookups: list[dict[str, Any]] = []

    @property
    def strength_max(self) -> float:
        return hedge_strength_max(self.hedges)


def hedge_strength_max(hedges: Sequence[Hedge]) -> float:
    """The §9.2 feature ``hedge_strength_max``: the strongest hedge on the fact, 0 when it has none."""
    return max((h.strength for h in hedges), default=0.0)


def lookup_hedges(terms: Iterable[str], lex: Lexicon) -> HedgeResult:
    """Map the extractor's surface hedge terms ('thinking of', 'I reckon') to lexicon entries.

    A term the lexicon does not know takes the nearest key (similarity >= 0.8) or
    ``params.hedge_default_strength`` and is logged; repeated terms are kept once.
    """
    hedges: list[Hedge] = []
    flags: list[str] = []
    lookups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for term in terms:
        c = canon(term)
        if not c or c in seen:
            continue
        seen.add(c)
        lk = lex.hedge_lookup(term)
        hedges.append(Hedge(term=term, strength=float(lk.value or 0.0), lexicon_key=lk.key or ""))
        lookups.append(lk.record())
        if lk.flag and lk.flag not in flags:
            flags.append(lk.flag)
    return HedgeResult(hedges=hedges, flags=flags, lookups=lookups)


def find_hedges(text: str, lex: Lexicon) -> list[Hedge]:
    """Lexicon hedge terms present in a text, longest first and non-overlapping, in reading order.

    Stage 2 uses it for the 'no hedge removal' check on rewrites (§6.3); extraction can use it to
    catch hedges the model did not list.
    """
    toks = canon(text).split()
    used = [False] * len(toks)
    found: list[tuple[int, Hedge]] = []
    keys = sorted(lex.sections["hedge_strength"].by_canon.items(), key=lambda kv: -len(kv[0].split()))
    for kc, key in keys:
        kt = kc.split()
        n = len(kt)
        for i in range(len(toks) - n + 1):
            if toks[i : i + n] == kt and not any(used[i : i + n]):
                used[i : i + n] = [True] * n
                found.append((i, Hedge(term=kc, strength=float(lex.data.hedge_strength[key]), lexicon_key=key)))
    return [h for _, h in sorted(found, key=lambda x: x[0])]


__all__ = ["HedgeResult", "find_hedges", "hedge_strength_max", "lookup_hedges"]
