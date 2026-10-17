"""Value distributions (spec §3.3).

A slot value is always a typed distribution; a certain value is a ``Point``. Dates
that carry uncertainty are held as decimal years (``unit="date"``), e.g. 1985.5 for
1 July 1985; a certain date may also be an ISO string ``Point``.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_TOL = 1e-6


class _Dist(BaseModel):
    model_config = ConfigDict(frozen=True)


class Point(_Dist):
    kind: Literal["point"] = "point"
    value: bool | int | float | str


class Categorical(_Dist):
    kind: Literal["categorical"] = "categorical"
    probs: dict[str, float]  # sums to 1

    @model_validator(mode="after")
    def _sums_to_one(self) -> Categorical:
        if not self.probs:
            raise ValueError("categorical needs at least one category")
        if any(p < -_TOL for p in self.probs.values()):
            raise ValueError("categorical probabilities must be non-negative")
        if abs(sum(self.probs.values()) - 1) > 1e-4:
            raise ValueError(f"categorical probabilities sum to {sum(self.probs.values())}, not 1")
        return self


class Discrete(_Dist):
    kind: Literal["discrete"] = "discrete"
    support: list[float]
    weights: list[float]

    @model_validator(mode="after")
    def _check(self) -> Discrete:
        if len(self.support) != len(self.weights) or not self.support:
            raise ValueError("discrete support and weights must be non-empty and the same length")
        if any(w < -_TOL for w in self.weights) or abs(sum(self.weights) - 1) > 1e-4:
            raise ValueError("discrete weights must be non-negative and sum to 1")
        return self


class Triangular(_Dist):
    kind: Literal["triangular"] = "triangular"
    low: float
    mode: float
    high: float

    @model_validator(mode="after")
    def _ordered(self) -> Triangular:
        if not (self.low <= self.mode <= self.high):
            raise ValueError(f"triangular needs low <= mode <= high, got {self.low}, {self.mode}, {self.high}")
        return self


class LogNormal(_Dist):
    """Stored as quantiles; the (mu, sigma) of the log are fitted at sample time."""

    kind: Literal["lognormal"] = "lognormal"
    median: float
    p10: float
    p90: float

    @model_validator(mode="after")
    def _ordered(self) -> LogNormal:
        if not (0 < self.p10 <= self.median <= self.p90):
            raise ValueError("lognormal needs 0 < p10 <= median <= p90")
        return self

    @property
    def mu(self) -> float:
        return math.log(self.median)

    @property
    def sigma(self) -> float:
        # A lognormal is symmetric in log space; use the wider half so neither quantile is understated.
        z = 1.2815515655446004
        return max(math.log(self.median / self.p10), math.log(self.p90 / self.median)) / z


class Normal(_Dist):
    kind: Literal["normal"] = "normal"
    mean: float
    sd: float

    @field_validator("sd")
    @classmethod
    def _sd(cls, v: float) -> float:
        if v < 0:
            raise ValueError("sd must be >= 0")
        return v


class Bernoulli(_Dist):
    kind: Literal["bernoulli"] = "bernoulli"
    p: float

    @field_validator("p")
    @classmethod
    def _p(cls, v: float) -> float:
        if not (-_TOL <= v <= 1 + _TOL):
            raise ValueError("p must be in [0, 1]")
        return min(1.0, max(0.0, v))


class Mixture(_Dist):
    kind: Literal["mixture"] = "mixture"
    components: list[tuple[float, Dist]]  # (weight, dist)

    @model_validator(mode="after")
    def _weights(self) -> Mixture:
        if not self.components:
            raise ValueError("mixture needs at least one component")
        total = sum(w for w, _ in self.components)
        if any(w < -_TOL for w, _ in self.components) or abs(total - 1) > 1e-4:
            raise ValueError(f"mixture weights must be non-negative and sum to 1 (got {total})")
        return self


Dist = Annotated[
    Point | Categorical | Discrete | Triangular | LogNormal | Normal | Bernoulli | Mixture,
    Field(discriminator="kind"),
]
Mixture.model_rebuild()

# "pct" holds a rate as a decimal (0.115 for 11.5%), as the FnaDocument stores it; "months" is a duration
# in months (policy waiting periods); calendar years and uncertain dates are decimal years in "date".
Unit = Literal["ZAR", "USD", "GBP", "EUR", "years", "months", "age", "pct", "count", "date", "none"]
Period = Literal["once", "monthly", "annual", "none"]
Basis = Literal["gross", "net", "unknown", "na"]
RealOrNominal = Literal["real", "nominal", "na"]
CURRENCIES: tuple[str, ...] = ("ZAR", "USD", "GBP", "EUR")


class Quantity(BaseModel):
    model_config = ConfigDict(frozen=True)

    dist: Dist
    unit: Unit
    period: Period = "none"
    basis: Basis = "na"
    real_or_nominal: RealOrNominal = "na"
