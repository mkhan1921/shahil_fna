"""Forecast configuration and capital market assumptions (spec §12.1, §12.8).

Both files are version-pinned: ``config_v1.yaml`` (``config_version``) and ``cma/<version>.yaml``
(``cma_version``), and every ``ForecastResult`` records both. The CMA supplies only volatilities,
correlations and the asset mix of each portfolio; expected returns come from the FNA document's
assumptions (§12.8), so the CMA's ``relative_real_return`` only places one asset class relative to another.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Literal

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FORECAST_DIR = Path(__file__).parent
CMA_DIR = FORECAST_DIR / "cma"
CURRENT_CONFIG = "config_v1"
ASSET_CLASSES: tuple[str, ...] = ("za_equity", "za_bonds", "za_cash", "global_equity", "global_bonds")
FOREIGN_CURRENCIES: tuple[str, ...] = ("USD", "GBP", "EUR")

Allocation = dict[str, float]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------- config_v1.yaml


class InflationConfig(_Frozen):
    model: Literal["ar1"] = "ar1"
    mean: str | float = "fna.assumptions.cpi"  # the document's CPI unless a number is pinned here
    phi: float = 0.6
    sd: float = 0.015

    def mean_for(self, document_cpi: float) -> float:
        return document_cpi if isinstance(self.mean, str) else float(self.mean)


class FxConfig(_Frozen):
    model: Literal["gbm"] = "gbm"
    drift_real: float = 0.01  # expected real depreciation of the rand a year
    vol: float = 0.15


class FatTails(_Frozen):
    enabled: bool = True
    df: float = 5.0
    max_multiplier: float = 4.0  # caps the t multiplier so one year cannot draw an absurd shock

    @field_validator("df")
    @classmethod
    def _df(cls, v: float) -> float:
        if v <= 2:
            raise ValueError("fat_tails.df must be above 2 (finite variance)")
        return v


class SuccessDefinition(_Frozen):
    essential_spend_met_until_age: str = "fna.retirement.{p}.planningAge"
    allow_property_sale: bool = False
    tolerance: float = 0.001  # unmet essential spend below this share of the year's essential spend is rounding


class DrawdownBand(_Frozen):
    min: float = 0.025
    max: float = 0.175


class ExpenseRules(_Frozen):
    children_end_age: float = 18
    support_keys: tuple[str, ...] = ("family-support",)


class BootstrapConfig(_Frozen):
    mean_block_months: int = 24
    recentre: bool = True  # keep the model's means and test only the shape of the historical distribution
    us_cpi_annual: float = 0.025  # used when the supplied series has no US CPI column


class ModeSettings(_Frozen):
    n_paths: int
    attribution_n: int
    shapley_paths: int
    shapley_contexts: int
    shapley_permutations: int
    shapley_inner: int = 3
    top_drivers: int = 10
    need_bands_k: int


class ForecastConfig(_Frozen):
    config_version: str
    n_paths: int = 10_000
    n_paths_sensitivity: int = 1_000
    n_paths_max: int = 40_000
    pos_ci_max_width: float = 0.02
    step: Literal["annual"] = "annual"
    horizon_age: float = 100
    max_years: int = 110
    seed_policy: Literal["per_run_fixed"] = "per_run_fixed"
    asset_classes: tuple[str, ...] = ASSET_CLASSES
    capital_market_assumptions: str = "cma/2026Q4.yaml"
    inflation: InflationConfig = InflationConfig()
    fx_usdzar: FxConfig = FxConfig()
    return_model: Literal["mvn_log", "block_bootstrap"] = "mvn_log"
    fat_tails: FatTails = FatTails()
    success_definition: SuccessDefinition = SuccessDefinition()
    portfolio_policy: Literal["from_profile_or_default", "default"] = "from_profile_or_default"
    drawdown_band: DrawdownBand = DrawdownBand()
    expenses: ExpenseRules = ExpenseRules()
    block_bootstrap: BootstrapConfig = BootstrapConfig()
    modes: dict[str, ModeSettings] = Field(default_factory=dict)
    # §12.8 consistency mode: every volatility (assets, inflation, FX) is zero, so each path follows the means.
    zero_volatility: bool = False

    @field_validator("asset_classes")
    @classmethod
    def _classes(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(v) != ASSET_CLASSES:
            raise ValueError(f"asset_classes must be {list(ASSET_CLASSES)} (the engine's factor order)")
        return tuple(v)

    @property
    def cma_version(self) -> str:
        return Path(self.capital_market_assumptions).stem

    def mode(self, name: str) -> ModeSettings:
        try:
            return self.modes[name]
        except KeyError:
            raise KeyError(f"no forecast mode {name!r}; configured: {sorted(self.modes)}") from None

    def with_(self, **updates: object) -> ForecastConfig:
        """A copy with top-level fields replaced (models are frozen so a pinned config never changes)."""
        return self.model_copy(update=updates)


# --------------------------------------------------------------------------- cma/<version>.yaml


class AssetClassCMA(_Frozen):
    name: str
    currency: Literal["ZAR", "USD"]
    vol: float  # annual volatility of real log returns in the class's own currency
    relative_real_return: float  # arithmetic real return in its own currency; only differences matter (§12.8)


class GlidePoint(_Frozen):
    age: float
    allocation: Allocation


class Policies(_Frozen):
    reference: Allocation
    glide_path: tuple[GlidePoint, ...]
    living_annuity: tuple[GlidePoint, ...]
    risk_profiles: dict[str, Allocation]
    cash: Allocation
    tfsa: Allocation
    offshore: Allocation
    capital: Allocation
    discretionary: Literal["glide_path"] | Allocation = "glide_path"


class FxMarket(_Frozen):
    usdzar_spot: float
    gbpusd: float
    eurusd: float
    correlation_with_assets: dict[str, float]

    def spot(self, currency: str) -> float:
        """Rand per unit of ``currency`` at the CMA date (GBP and EUR through fixed cross rates)."""
        return {"ZAR": 1.0, "USD": self.usdzar_spot, "GBP": self.usdzar_spot * self.gbpusd, "EUR": self.usdzar_spot * self.eurusd}[currency]


class RealAssets(_Frozen):
    property_real_growth: float = 0.0
    business_real_growth: float = 0.0
    lifestyle_real_growth: dict[str, float] = {"vehicle": -0.10, "personal-effects": -0.05, "other": -0.02}
    loan_account_nominal: bool = True


class CMA(_Frozen):
    cma_version: str
    as_of: str
    owner: str = ""
    status: Literal["placeholder", "approved"] = "placeholder"
    notes: str = ""
    asset_classes: tuple[AssetClassCMA, ...]
    correlation: tuple[tuple[float, ...], ...]
    fx: FxMarket
    policies: Policies
    real_assets: RealAssets = RealAssets()

    @model_validator(mode="after")
    def _check(self) -> CMA:
        names = tuple(a.name for a in self.asset_classes)
        if names != ASSET_CLASSES:
            raise ValueError(f"CMA asset classes must be {list(ASSET_CLASSES)} in that order, got {list(names)}")
        c = np.asarray(self.correlation, dtype=float)
        if c.shape != (5, 5) or not np.allclose(c, c.T) or not np.allclose(np.diag(c), 1.0):
            raise ValueError("CMA correlation must be a symmetric 5x5 matrix with a unit diagonal")
        if set(self.fx.correlation_with_assets) != set(ASSET_CLASSES):
            raise ValueError("fx.correlation_with_assets needs one entry per asset class")
        if np.linalg.eigvalsh(self.joint_correlation()).min() <= 1e-10:
            raise ValueError("CMA correlation (with the FX row) is not positive definite")
        p = self.policies
        allocations = [p.reference, p.cash, p.tfsa, p.offshore, p.capital, *p.risk_profiles.values()]
        allocations += [g.allocation for g in (*p.glide_path, *p.living_annuity)]
        if not isinstance(p.discretionary, str):
            allocations.append(p.discretionary)
        for a in allocations:
            _check_allocation(a)
        for points in (p.glide_path, p.living_annuity):
            ages = [g.age for g in points]
            if not points or ages != sorted(ages):
                raise ValueError("glide path points must be non-empty and in ascending age order")
        return self

    # numeric views, in ASSET_CLASSES order
    @property
    def vols(self) -> np.ndarray:
        return np.array([a.vol for a in self.asset_classes], dtype=float)

    @property
    def relative_returns(self) -> np.ndarray:
        return np.array([a.relative_real_return for a in self.asset_classes], dtype=float)

    @property
    def is_global(self) -> np.ndarray:
        return np.array([a.currency == "USD" for a in self.asset_classes], dtype=bool)

    def joint_correlation(self) -> np.ndarray:
        """6x6 correlation of the five class shocks and the real USDZAR shock (last row)."""
        out = np.eye(6)
        out[:5, :5] = np.asarray(self.correlation, dtype=float)
        fx = np.array([self.fx.correlation_with_assets[n] for n in ASSET_CLASSES], dtype=float)
        out[5, :5] = fx
        out[:5, 5] = fx
        return out


def _check_allocation(a: Allocation) -> None:
    unknown = set(a) - set(ASSET_CLASSES)
    if unknown:
        raise ValueError(f"allocation names unknown asset classes {sorted(unknown)}")
    if any(w < 0 for w in a.values()) or abs(sum(a.values()) - 1) > 1e-6:
        raise ValueError(f"allocation weights must be non-negative and sum to 1, got {a}")


def allocation_vector(a: Allocation) -> np.ndarray:
    """An allocation as weights in ASSET_CLASSES order."""
    return np.array([float(a.get(n, 0.0)) for n in ASSET_CLASSES], dtype=float)


def glide_weights(points: tuple[GlidePoint, ...], ages: np.ndarray) -> np.ndarray:
    """Allocation weights at each age (linear between points, flat outside): shape ages.shape + (5,)."""
    ages = np.asarray(ages, dtype=float)
    xs = np.array([g.age for g in points], dtype=float)
    ys = np.stack([allocation_vector(g.allocation) for g in points])  # (k, 5)
    if len(points) == 1:
        return np.broadcast_to(ys[0], ages.shape + (5,)).copy()
    flat = ages.reshape(-1)
    out = np.stack([np.interp(flat, xs, ys[:, i]) for i in range(5)], axis=-1)
    return out.reshape(ages.shape + (5,))


# --------------------------------------------------------------------------- loaders


@functools.lru_cache(maxsize=8)
def load_config(version: str = CURRENT_CONFIG) -> ForecastConfig:
    """The pinned forecast configuration (``forecast/<version>.yaml``)."""
    path = FORECAST_DIR / f"{version}.yaml"
    return ForecastConfig.model_validate(yaml.safe_load(path.read_text()))


@functools.lru_cache(maxsize=8)
def load_cma(version: str = "2026Q4") -> CMA:
    """Capital market assumptions ``cma/<version>.yaml``, validated (positive definite, allocations sum to 1)."""
    path = CMA_DIR / f"{version}.yaml"
    data = yaml.safe_load(path.read_text())
    cma = CMA.model_validate(data)
    if cma.cma_version != version:
        raise ValueError(f"{path.name} declares cma_version {cma.cma_version!r}")
    return cma
