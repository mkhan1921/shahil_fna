"""Stage 8: the Monte Carlo forecasting engine (spec §12) and the engine side of the sensitivity runs (§10.2).

Public API:

- ``run_forecast(profile, ...) -> ForecastResult``: the forecast of one profile version (§12.4);
- ``sensitivity_runs(profile, fact_ids, ...) -> {fact_id: SensitivityRow}`` and ``fact_extremes(fact, slot)``;
- ``TaxModel`` (protocol) and ``FnaEngineTaxModel``: tax comes only from the ported FNA engine (§12.7);
- ``attribution_sentence(variance_attribution)``: the review UI's line (§12.5);
- ``return_model_check(profile, series)``: the quarterly block-bootstrap check (§12.6).
"""

from .attribution import attribution_sentence
from .config import CMA, ForecastConfig, load_cma, load_config
from .returns import HistoricalSeries, MarketModel, StationaryBlockBootstrap
from .run import fna_engine_analyse, fna_engine_bands, return_model_check, run_forecast, wilson_interval
from .sensitivity import SensitivityRow, fact_extremes, sensitivity_runs
from .tax import FnaEngineTaxModel, PersonTax, TaxModel, TaxModelUnavailable, default_tax_model

__all__ = [
    "CMA",
    "FnaEngineTaxModel",
    "ForecastConfig",
    "HistoricalSeries",
    "MarketModel",
    "PersonTax",
    "SensitivityRow",
    "StationaryBlockBootstrap",
    "TaxModel",
    "TaxModelUnavailable",
    "attribution_sentence",
    "default_tax_model",
    "fact_extremes",
    "fna_engine_analyse",
    "fna_engine_bands",
    "load_cma",
    "load_config",
    "return_model_check",
    "run_forecast",
    "sensitivity_runs",
    "wilson_interval",
]
