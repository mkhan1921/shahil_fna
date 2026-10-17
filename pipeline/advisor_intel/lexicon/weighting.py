"""Hypothesis weighting (spec §7.3 step 7).

If the client gave no weights, the weight is split equally; then recency applies: the hypotheses
from a revising statement share 0.8 and the ones it revised share 0.2 (``recency_weight`` in the
lexicon, so the split is tunable). When scale resolution (§7.4) splits one spoken amount into
several readings, those readings share their parent hypothesis's weight in proportion to their
scale weights, so an ambiguous "two fifty" never outweighs a second, unambiguous option.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from ..models.dists import Quantity
from ..models.extraction import RawHypothesis
from ..models.facts import Hypothesis
from .loader import Lexicon
from .normalize import Normalized


def _client_split(weights: Sequence[float]) -> list[float]:
    """Client-given weights where present (0 = none given), the rest of the mass split equally."""
    n = len(weights)
    given = [w for w in weights if w > 0]
    if not given:
        return [1.0 / n] * n
    total = sum(given)
    if len(given) == n:
        return [w / total for w in weights]
    if total < 1.0:
        rest = (1.0 - total) / (n - len(given))
        return [w if w > 0 else rest for w in weights]
    # The client's weights already cover everything: unweighted options get the smallest given weight.
    filled = [w if w > 0 else min(given) for w in weights]
    s = sum(filled)
    return [w / s for w in filled]


def assign_weights(
    raw_hypotheses: Sequence[RawHypothesis],
    lex: Lexicon,
    *,
    groups: Sequence[int] | None = None,
    scale_weights: Sequence[float] | None = None,
) -> list[float]:
    """Within-slot weights for the hypotheses that survived normalisation and the bounds check (§7.3).

    ``groups[i]`` names the extractor hypothesis item ``i`` came from (scale resolution can split one
    into several readings) and ``scale_weights[i]`` is that reading's share. Without them every item
    is its own hypothesis. The result sums to 1 and is aligned with the input.
    """
    n = len(raw_hypotheses)
    if n == 0:
        return []
    groups = list(groups) if groups is not None else list(range(n))
    scale = [max(0.0, float(s)) for s in scale_weights] if scale_weights is not None else [1.0] * n
    if len(groups) != n or len(scale) != n:
        raise ValueError("groups and scale_weights must align with raw_hypotheses")
    parents: dict[int, tuple[float, bool]] = {}
    for h, g in zip(raw_hypotheses, groups):
        parents.setdefault(g, (max(0.0, float(h.weight)), bool(h.revises)))
    order = list(parents)
    pw = dict(zip(order, _client_split([parents[g][0] for g in order])))
    revising = [g for g in order if parents[g][1]]
    revised = [g for g in order if not parents[g][1]]
    if revising and revised:
        rw = lex.recency_weights()
        for members, share in ((revising, rw.revising), (revised, rw.revised)):
            s = sum(pw[g] for g in members)
            for g in members:
                pw[g] = share * (pw[g] / s if s > 0 else 1.0 / len(members))
    totals: dict[int, float] = defaultdict(float)
    counts: dict[int, int] = defaultdict(int)
    for g, s in zip(groups, scale):
        totals[g] += s
        counts[g] += 1
    out = [pw[g] * (s / totals[g] if totals[g] > 0 else 1.0 / counts[g]) for g, s in zip(groups, scale)]
    total = sum(out)
    return [w / total for w in out]


def build_hypotheses(
    raw_hypotheses: Sequence[RawHypothesis],
    values: Sequence[Normalized | Quantity | None],
    lex: Lexicon,
    *,
    groups: Sequence[int] | None = None,
    scale_weights: Sequence[float] | None = None,
) -> list[Hypothesis]:
    """``Hypothesis`` objects for the values that survived (``None`` = dropped), weighted by ``assign_weights``."""
    if len(values) != len(raw_hypotheses):
        raise ValueError("values must align with raw_hypotheses")
    keep = [i for i, v in enumerate(values) if v is not None and (not isinstance(v, Normalized) or v.quantity is not None)]
    if not keep:
        return []
    weights = assign_weights(
        [raw_hypotheses[i] for i in keep],
        lex,
        groups=[groups[i] for i in keep] if groups is not None else None,
        scale_weights=[scale_weights[i] for i in keep] if scale_weights is not None else None,
    )
    out: list[Hypothesis] = []
    for i, w in zip(keep, weights):
        v = values[i]
        q = v.quantity if isinstance(v, Normalized) else v
        out.append(Hypothesis(value=q, weight=w, rationale=raw_hypotheses[i].rationale, revises=raw_hypotheses[i].revises))
    return out


__all__ = ["assign_weights", "build_hypotheses"]
