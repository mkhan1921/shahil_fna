from __future__ import annotations

import math

import pytest
from pydantic import TypeAdapter, ValidationError

from advisor_intel.models.dists import (
    Bernoulli,
    Categorical,
    Discrete,
    Dist,
    LogNormal,
    Mixture,
    Normal,
    Point,
    Quantity,
    Triangular,
)

DIST = TypeAdapter(Dist)


def test_discriminated_round_trip():
    dists = [
        Point(value=42000),
        Point(value="1986-04-12"),
        Point(value=True),
        Categorical(probs={"a": 0.6, "b": 0.4}),
        Discrete(support=[2, 3], weights=[0.75, 0.25]),
        Triangular(low=60, mode=62, high=65),
        LogNormal(median=100, p10=80, p90=130),
        Normal(mean=0.03, sd=0.03),
        Bernoulli(p=0.4),
        Mixture(components=[(0.8, Point(value=60)), (0.2, Triangular(low=63, mode=65, high=67))]),
    ]
    for d in dists:
        assert DIST.validate_python(d.model_dump()) == d
        assert DIST.validate_json(DIST.dump_json(d)) == d


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "categorical", "probs": {"a": 0.5}},
        {"kind": "discrete", "support": [1, 2], "weights": [1.0]},
        {"kind": "triangular", "low": 3, "mode": 2, "high": 4},
        {"kind": "lognormal", "median": 10, "p10": 11, "p90": 20},
        {"kind": "normal", "mean": 0, "sd": -1},
        {"kind": "bernoulli", "p": 1.5},
        {"kind": "mixture", "components": [[0.5, {"kind": "point", "value": 1}]]},
    ],
)
def test_invalid_dists_rejected(bad):
    with pytest.raises(ValidationError):
        DIST.validate_python(bad)


def test_lognormal_sigma_uses_wider_half():
    d = LogNormal(median=100, p10=90, p90=150)
    assert math.isclose(d.sigma, math.log(1.5) / 1.2815515655446004)


def test_quantity_is_frozen_and_hashable_by_value():
    q = Quantity(dist=Point(value=1), unit="ZAR", period="monthly")
    with pytest.raises(ValidationError):
        q.unit = "USD"  # type: ignore[misc]
    assert q == Quantity(dist=Point(value=1), unit="ZAR", period="monthly")
