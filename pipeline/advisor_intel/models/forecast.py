"""Forecast outputs (spec §12.4)."""

from __future__ import annotations

from pydantic import BaseModel


class ForecastResult(BaseModel):
    run_id: str
    profile_version: str
    config_version: str
    cma_version: str
    n_paths: int
    seed: int = 0
    pos: float  # P(essential spend met to the planning age)
    pos_ci: tuple[float, float]  # Monte Carlo 95% CI (Wilson)
    goal_funding: dict[str, float] = {}  # goal fact_id → P(funded on time)
    ages: list[int] = []  # the age of the primary client at each yearly step
    net_worth_bands: dict[str, list[float]] = {}  # p10/p25/p50/p75/p90 per year, real ZAR
    shortfall: dict[str, float | None] = {}  # P(any), median first shortfall age, p90 total real shortfall
    liquidity_breach_prob: float = 0.0
    retirement_income_bands: dict[str, list[float]] = {}
    variance_attribution: dict[str, float] = {}  # {"market":…, "client_content":…, "extraction":…, "interaction":…}
    market_attribution: dict[str, float] = {}  # Shapley effects within the market group (§12.5)
    top_drivers: list[tuple[str, float]] = []  # fact_id, first-order index
    deterministic: dict[str, float | None] = {}  # the FNA's own retirement projection, for side-by-side display (§12.8)
    need_bands: dict[str, dict[str, float]] = {}  # need → {p10, p50, p90} of the shortfall over materialised draws (§12.7)
