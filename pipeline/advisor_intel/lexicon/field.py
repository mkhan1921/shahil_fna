"""One extracted field through §7.3 steps 4–7: scale resolution, normalisation, hedges, bounds, weighting.

``normalize_field`` is the single call ``to_state_ops`` needs per ``FieldValue``: the LLM's amount
forms get their scale from the grammar spans (§7.4), every hypothesis is normalised (§8.3), hedge
terms get lexicon strengths (step 5), values outside the slot's bounds are dropped and logged
(step 6), and the survivors are weighted, with recency (step 7). It also returns the calibration
features this part of the pipeline owns (§9.2).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ..models.extraction import FieldValue
from ..models.facts import Hedge, Hypothesis
from ..models.fna import Assumptions
from ..models.profile import Profile
from ..models.transcript import NumericSpan
from ..schema.slots import SlotDef
from .bounds import filter_bounds
from .hedges import lookup_hedges
from .loader import Lexicon
from .normalize import Normalized, normalize_value
from .weighting import assign_weights

if TYPE_CHECKING:
    from ..extract.scale import ScaleContext


class NormalizedField(BaseModel):
    hypotheses: list[Hypothesis]  # in bounds, weighted, summing to 1 (empty when every value was dropped)
    normalizations: list[dict[str, Any]]  # aligned with hypotheses
    hedges: list[Hedge]
    flags: list[str]  # union of the hypotheses', the hedges' and scale resolution's flags
    features: dict[str, float | None]  # §9.2: hedge_strength_max, lexicon_miss, scale_ambiguous, grammar_*, second_pass_*, …
    dropped: list[str] = []  # out-of-bounds drops, for ExtractionResult.dropped
    rejected: list[Normalized] = []

    @property
    def normalization(self) -> dict[str, Any] | None:
        """The ``Fact.normalization`` record: the single hypothesis's, or one entry per hypothesis."""
        if not self.normalizations:
            return None
        if len(self.normalizations) == 1:
            return self.normalizations[0]
        return {"lexicon_version": self.normalizations[0].get("lexicon_version"), "hypotheses": self.normalizations}


def normalize_field(
    field: FieldValue,
    slot: SlotDef,
    profile: Profile | None,
    lex: Lexicon,
    *,
    spans: Sequence[NumericSpan] = (),
    scale_context: ScaleContext | None = None,
    anchor_date: date | None = None,
    fact_id: str | None = None,
    person: str | None = None,
    assumptions: Assumptions | None = None,
    priced_year: float | None = None,
) -> NormalizedField:
    """§7.3 steps 4–7 for one field. ``spans`` are the grammar spans overlapping the aligned evidence."""
    from ..extract.scale import resolve_hypotheses  # here, not at import: extract.scale imports this package

    scaled = resolve_hypotheses(field.hypotheses, spans, slot, context=scale_context)
    values = [
        normalize_value(
            h.raw.intermediate,
            slot,
            profile,
            lex,
            anchor_date=anchor_date,
            fact_id=fact_id,
            person=person,
            assumptions=assumptions,
            priced_year=priced_year,
        )
        for h in scaled.items
    ]
    bounded = filter_bounds(values, slot, fact_id=fact_id)
    keep = [i for i, v in enumerate(bounded.values) if v is not None and v.quantity is not None]
    weights = assign_weights(
        [scaled.items[i].raw for i in keep],
        lex,
        groups=[scaled.items[i].parent for i in keep],
        scale_weights=[scaled.items[i].scale_weight for i in keep],
    )
    hypotheses: list[Hypothesis] = []
    normalizations: list[dict[str, Any]] = []
    for i, w in zip(keep, weights):
        n = bounded.values[i]
        raw = scaled.items[i].raw
        hypotheses.append(Hypothesis(value=n.quantity, weight=w, rationale=raw.rationale, revises=raw.revises))  # type: ignore[union-attr]
        normalizations.append({**n.normalization, "span_ids": scaled.items[i].span_ids} if scaled.items[i].span_ids else n.normalization)  # type: ignore[union-attr]
    hedges = lookup_hedges(field.hedges, lex)
    flags: list[str] = []
    for f in [*(fl for v in values for fl in v.flags), *(fl for i in keep for fl in bounded.values[i].flags), *hedges.flags, *scaled.flags]:  # type: ignore[union-attr]
        if f not in flags:
            flags.append(f)
    if bounded.dropped:
        flags.append("out_of_bounds")
    features: dict[str, float | None] = {
        "hedge_strength_max": hedges.strength_max,
        "lexicon_miss": 1.0 if "lexicon_miss" in flags else 0.0,
        "lexicon_near_miss": 1.0 if "lexicon_near_miss" in flags else 0.0,
        **scaled.features,
        "n_hypotheses": float(len(hypotheses)),
        "top_weight": max(weights, default=0.0),
    }
    return NormalizedField(
        hypotheses=hypotheses,
        normalizations=normalizations,
        hedges=hedges.hedges,
        flags=flags,
        features=features,
        dropped=bounded.dropped,
        rejected=bounded.rejected,
    )


__all__ = ["NormalizedField", "normalize_field"]
