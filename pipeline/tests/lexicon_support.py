"""Builders for the lexicon, normalisation and scale tests: hand-made slots (the catalog YAML is
written elsewhere), facts and profiles, and a fake embedder with chosen similarities."""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import numpy as np

from advisor_intel.lexicon.phrases import canon
from advisor_intel.models.dists import Quantity
from advisor_intel.models.facts import Fact, Provenance
from advisor_intel.models.profile import Profile
from advisor_intel.schema.slots import SlotDef

ANCHOR = date(2026, 10, 16)

_VALUE_TYPE = {
    "rand": "amount",
    "rand_monthly": "amount",
    "rand_annual": "amount",
    "years": "years",
    "months": "months",
    "age": "age",
    "year": "year",
    "decimal": "rate",
    "date": "date",
    "count": "count",
    "category": "category",
    "boolean": "boolean",
    "text": "text",
    "probability": "probability",
}
_POLICY = {"category": "categorical", "boolean": "boolean", "text": "text", "probability": "span_normalise"}


def make_slot(slot_id: str, fna_unit: str, **kw: Any) -> SlotDef:
    """A SlotDef for one FnaDocument path, with defaults that follow the unit."""
    list_slot = "[]" in slot_id
    return SlotDef(
        slot_id=slot_id,
        fna_path=kw.pop("fna_path", slot_id),
        group=kw.pop("group", slot_id.split(".")[0].split("[")[0]),
        cardinality=kw.pop("cardinality", "per_entity" if list_slot else ("per_person" if "{p}" in slot_id else "one")),
        policy=kw.pop("policy", _POLICY.get(fna_unit, "span_normalise")),
        value_type=kw.pop("value_type", _VALUE_TYPE[fna_unit]),
        fna_unit=fna_unit,
        question=kw.pop("question", f"What is {slot_id}?"),
        **kw,
    )


def fact(fact_id: str, value: Quantity | None, *, slot_id: str | None = None, prior: Quantity | None = None, status: str = "stated") -> Fact:
    instance = fact_id.split("#", 1)[1] if "#" in fact_id else None
    return Fact(
        fact_id=fact_id,
        slot_id=slot_id or fact_id.split("#", 1)[0],
        instance_key=instance,
        fna_path=fact_id,
        status=status,  # type: ignore[arg-type]
        value=value,
        prior_value=prior,
        provenance=Provenance(run_id="run-test"),
    )


def profile(*facts: Fact, anchor: date = ANCHOR) -> Profile:
    return Profile(call_id="call-test", run_id="run-test", schema_version="profile_v1", anchor_date=anchor, facts={f.fact_id: f for f in facts})


class FakeEmbedder:
    """Embeds texts so that cosine(query, key) is exactly what the test asks for.

    ``similar`` maps a query phrase to {key phrase: similarity}; anything else is orthogonal.
    """

    name = "fake-test"
    dim = 512

    def __init__(self, similar: dict[str, dict[str, float]]) -> None:
        self.similar = {canon(q): {canon(k): s for k, s in ks.items()} for q, ks in similar.items()}
        self._index: dict[str, int] = {}
        self.queries: list[str] = []

    def _e(self, label: str) -> np.ndarray:
        if label not in self._index:
            self._index[label] = len(self._index)
        v = np.zeros(self.dim, dtype=np.float32)
        v[self._index[label]] = 1.0
        return v

    def embed_documents(self, texts):
        return np.stack([self._e("doc:" + canon(t)) for t in texts]) if texts else np.zeros((0, self.dim), dtype=np.float32)

    def embed_queries(self, texts):
        rows = []
        for t in texts:
            c = canon(t)
            self.queries.append(c)
            v = np.zeros(self.dim, dtype=np.float32)
            rest = 1.0
            for key, sim in self.similar.get(c, {}).items():
                v += sim * self._e("doc:" + key)
                rest -= sim * sim
            v += math.sqrt(max(0.0, rest)) * self._e("query:" + c)
            rows.append(v / np.linalg.norm(v))
        return np.stack(rows)
