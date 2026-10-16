"""Market scenarios: returns, inflation and the rand (spec §12.1, §12.6, §12.8).

The v1 model (``return_model: mvn_log``):

- real log returns of the five asset classes are multivariate normal (CMA vols and correlations), scaled by a
  Student-t(5) multiplier per path-year (fat tails; capped at ``max_multiplier`` and normalised to unit
  variance);
- annual CPI is AR(1) around the document's CPI (phi 0.6, sd 0.015);
- the real USDZAR rate is a geometric Brownian motion (real drift 1%, vol 15%) whose shocks are correlated
  with the asset classes (CMA ``fx.correlation_with_assets``). Global classes are modelled in US dollars and
  earn the change in the rand on top.

Expected returns are not CMA inputs: each portfolio's expected real return is set from the document
(``portfolio_gross``), and the class-level means only place classes relative to each other.

All randomness comes from one stream per seed and input matrix, so sensitivity runs reuse the same market
draws (common random numbers) and the variance attribution can swap the whole market group.

``StationaryBlockBootstrap`` is the quarterly return-model check (§12.6): it resamples a *supplied* monthly
history of SA asset returns, CPI and USDZAR in blocks of mean length 24 months, in place of the MVN-t model.
The real series (since 2000) must be supplied by the investment team; nothing here ships historical data.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field

import numpy as np
from scipy import integrate
from scipy.stats import chi2 as chi2_dist

from .config import ASSET_CLASSES, CMA, ForecastConfig
from .sampling import stream

N_CLASSES = len(ASSET_CLASSES)
PLAYERS: tuple[str, ...] = (*ASSET_CLASSES, "fx", "inflation")  # the market inputs Shapley effects split (§12.5)


@dataclass(frozen=True)
class MarketShocks:
    """Group M randomness for P paths x T years, before any model parameters are applied."""

    z: np.ndarray  # (6, P, T) iid N(0, 1): five asset classes and the real USDZAR change
    chi2: np.ndarray  # (P, T) chi-square(df) draws behind the Student-t multiplier
    e: np.ndarray  # (P, T) iid N(0, 1) inflation innovations

    @property
    def n_paths(self) -> int:
        return int(self.z.shape[1])

    def take(self, rows: np.ndarray) -> "MarketShocks":
        return MarketShocks(self.z[:, rows], self.chi2[rows], self.e[rows])


@dataclass
class MarketPaths:
    """A realised market for P paths x T years."""

    class_log: np.ndarray  # (5, P, T) real log returns in rand (global classes include the real USDZAR change)
    infl: np.ndarray  # (P, T) CPI for each year
    fx_log: np.ndarray  # (P, T) real log change of USDZAR
    class_mean: np.ndarray  # (5,) the model's expected real gross return per class in rand, E[exp(class_log)]
    _cache: dict = field(default_factory=dict, repr=False)

    @property
    def n_paths(self) -> int:
        return int(self.infl.shape[0])

    @property
    def n_years(self) -> int:
        return int(self.infl.shape[1])

    @property
    def class_gross(self) -> np.ndarray:
        """(5, P, T) real gross returns per class, exp(class_log)."""
        if "gross" not in self._cache:
            self._cache["gross"] = np.exp(self.class_log)
        return self._cache["gross"]

    @property
    def price(self) -> np.ndarray:
        """(P, T+1) price index at the start of each year, 1 today."""
        if "price" not in self._cache:
            p = np.ones((self.n_paths, self.n_years + 1))
            np.cumprod(1 + self.infl, axis=1, out=p[:, 1:])
            self._cache["price"] = p
        return self._cache["price"]

    @property
    def fx_index(self) -> np.ndarray:
        """(P, T+1) real USDZAR relative to today (1 today)."""
        if "fx" not in self._cache:
            x = np.ones((self.n_paths, self.n_years + 1))
            x[:, 1:] = np.exp(np.cumsum(self.fx_log, axis=1))
            self._cache["fx"] = x
        return self._cache["fx"]

    def portfolio_gross(self, weights: np.ndarray, target_real: float | np.ndarray) -> np.ndarray:
        """Real gross return (P, T) of a rebalanced portfolio whose expected real return is ``target_real``.

        ``weights`` is (5,) or broadcastable to (5, P, T). The class mix sets the shape around the mean; the mean
        itself is the document's (§12.8). With zero volatility the result is exactly 1 + target_real every year.
        """
        w = np.asarray(weights, dtype=float)
        gross = self.class_gross
        port = np.zeros(gross.shape[1:])
        tmp = np.empty(gross.shape[1:])
        mean: float | np.ndarray = 0.0
        for k in range(N_CLASSES):
            if w.ndim == 1 and w[k] == 0:
                continue
            np.multiply(gross[k], w[k], out=tmp)
            port += tmp
            mean = mean + w[k] * self.class_mean[k]
        port *= (1 + np.asarray(target_real, dtype=float)) / mean
        return port

    def relative_target(self, weights: np.ndarray, reference: np.ndarray, reference_target: float) -> float | np.ndarray:
        """The expected real return of a mix placed around a reference mix whose mean is ``reference_target``.

        ``weights`` is (5,) or (5, P, T); only the classes' relative returns move it off the reference mean.
        """
        a = self.class_mean - 1
        w = np.asarray(weights, dtype=float)
        if w.ndim == 1:
            return float(reference_target + (w - reference) @ a)
        return reference_target + np.tensordot(a, w - reference.reshape((-1,) + (1,) * (w.ndim - 1)), axes=(0, 0))

    def take(self, rows: np.ndarray) -> "MarketPaths":
        return MarketPaths(self.class_log[:, rows], self.infl[rows], self.fx_log[rows], self.class_mean)


class MarketModel:
    """The MVN-t / AR(1) / GBM market of §12.1 for one document's CPI."""

    def __init__(self, config: ForecastConfig, cma: CMA, cpi_mean: float) -> None:
        self.config = config
        self.cma = cma
        zero = config.zero_volatility
        self.zero_volatility = zero
        self.vols = np.zeros(N_CLASSES) if zero else cma.vols
        self.fx_vol = 0.0 if zero else config.fx_usdzar.vol
        self.infl_sd = 0.0 if zero else config.inflation.sd
        self.phi = config.inflation.phi
        self.cpi = config.inflation.mean_for(cpi_mean)
        self.corr = cma.joint_correlation()
        self.chol = np.linalg.cholesky(self.corr)
        ft = config.fat_tails
        self.fat = ft.enabled and not zero
        self.df = ft.df
        self.wmax = ft.max_multiplier
        self.w_norm = _w_norm(self.df, self.wmax) if self.fat else 1.0
        a = cma.relative_returns
        self.fx_mu = np.log1p(config.fx_usdzar.drift_real) - 0.5 * self.fx_vol**2
        kappa = np.array([self._log_mgf(s, 0.0, 0.0) for s in self.vols])
        self.class_mu = np.log1p(a) - kappa  # E[exp(own-currency real log return)] = 1 + a
        fx_rho = self.corr[5, :5]
        mean = 1 + a
        for i in np.flatnonzero(cma.is_global):
            ratio = np.exp(self._log_mgf(self.vols[i], self.fx_vol, fx_rho[i]) - kappa[i])
            mean[i] = (1 + a[i]) * np.exp(self.fx_mu) * ratio
        self.class_mean = mean

    # ------------------------------------------------------------------ the t multiplier

    def _w(self, chi2: np.ndarray) -> np.ndarray:
        if not self.fat:
            return np.ones_like(chi2)
        return np.minimum(np.sqrt((self.df - 2) / chi2), self.wmax) / self.w_norm

    def _log_mgf(self, s: float, v: float, rho: float) -> float:
        """ln E[exp(W s x + v y)] for standard normals x, y with correlation rho and the capped t multiplier W."""
        if s == 0 and v == 0:
            return 0.0
        if not self.fat:
            return 0.5 * (s * s + 2 * s * v * rho + v * v)

        def g(q: float) -> float:
            w = min(np.sqrt((self.df - 2) / q), self.wmax) / self.w_norm
            return float(np.exp(0.5 * (w * w * s * s + 2 * w * s * v * rho + v * v)) * chi2_dist.pdf(q, self.df))

        kink = (self.df - 2) / self.wmax**2
        val = integrate.quad(g, 0, kink, limit=200)[0] + integrate.quad(g, kink, np.inf, limit=200)[0]
        return float(np.log(val))

    # ------------------------------------------------------------------ draws

    def shocks(self, seed: int, n_paths: int, n_years: int, *, matrix: str = "") -> MarketShocks:
        g = stream(seed, "market", matrix)
        z = g.standard_normal((6, n_paths, n_years))
        c = g.chisquare(self.df, (n_paths, n_years)) if self.config.fat_tails.enabled else np.full((n_paths, n_years), self.df)
        e = g.standard_normal((n_paths, n_years))
        return MarketShocks(z, c, e)

    def players(self, shocks: MarketShocks) -> np.ndarray:
        """(7, P, T) correlated unit normals per market input: five classes, FX, inflation (Shapley players)."""
        z = shocks.z
        out = np.empty((7,) + z.shape[1:])
        tmp = np.empty(z.shape[1:])
        for i in range(6):  # x = L z with L lower triangular, plane by plane and in place (large temporaries are slow)
            np.multiply(z[0], self.chol[i, 0], out=out[i])
            for j in range(1, i + 1):
                np.multiply(z[j], self.chol[i, j], out=tmp)
                out[i] += tmp
        out[6] = shocks.e
        return out

    def realise(self, shocks: MarketShocks) -> MarketPaths:
        return self.realise_players(self.players(shocks), shocks.chi2)

    def realise_players(self, x: np.ndarray, chi2: np.ndarray) -> MarketPaths:
        """A market from correlated player normals (7, P, T) and the t multiplier's chi-square draws (P, T)."""
        w = self._w(chi2)
        fx_log = self.fx_vol * x[5]
        fx_log += self.fx_mu
        class_log = np.empty((N_CLASSES,) + fx_log.shape)
        for k in range(N_CLASSES):
            np.multiply(w, self.vols[k], out=class_log[k])
            class_log[k] *= x[k]
            class_log[k] += self.class_mu[k]
            if self.cma.is_global[k]:
                class_log[k] += fx_log
        infl = np.empty_like(fx_log)
        prev = np.full(fx_log.shape[0], self.cpi)
        shock = np.ascontiguousarray((self.infl_sd * x[6]).T)
        for t in range(fx_log.shape[1]):
            prev = self.cpi + self.phi * (prev - self.cpi) + shock[t]
            infl[:, t] = prev
        return MarketPaths(class_log, infl, fx_log, self.class_mean.copy())

    @functools.lru_cache(maxsize=128)
    def conditional(self, varied: frozenset[int]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Gaussian conditioning of the correlated block (players 0–5): varied | fixed.

        Returns (varied indices, fixed indices, regression matrix A, Cholesky L) so that
        x_varied = A @ x_fixed + L @ xi for independent normals xi (Shapley within M, §12.5).
        """
        v = np.array(sorted(i for i in varied if i < 6), dtype=int)
        f = np.array([i for i in range(6) if i not in varied], dtype=int)
        s = self.corr
        if len(v) == 0:
            return v, f, np.zeros((0, len(f))), np.zeros((0, 0))
        if len(f) == 0:
            return v, f, np.zeros((len(v), 0)), np.linalg.cholesky(s[np.ix_(v, v)])
        a = s[np.ix_(v, f)] @ np.linalg.inv(s[np.ix_(f, f)])
        cov = s[np.ix_(v, v)] - a @ s[np.ix_(f, v)]
        cov = (cov + cov.T) / 2 + 1e-12 * np.eye(len(v))
        return v, f, a, np.linalg.cholesky(cov)


def _w_norm(df: float, wmax: float) -> float:
    """sqrt(E[min(sqrt((df-2)/chi2), wmax)^2]): rescales the capped multiplier to unit variance."""
    kink = (df - 2) / wmax**2

    def g(q: float) -> float:
        return min((df - 2) / q, wmax**2) * chi2_dist.pdf(q, df)

    val = integrate.quad(g, 0, kink, limit=200)[0] + integrate.quad(g, kink, np.inf, limit=200)[0]
    return float(np.sqrt(val))


# --------------------------------------------------------------------------- block bootstrap (§12.6)


@dataclass(frozen=True)
class HistoricalSeries:
    """A monthly history for the quarterly return-model check (§12.6).

    The real series must be supplied (the investment team's data, SA monthly since 2000): nominal total
    returns per asset class (global classes in US dollars), SA CPI, and the monthly log change of USDZAR.
    """

    returns: dict[str, np.ndarray]  # class → (M,) monthly nominal total return, decimal
    cpi: np.ndarray  # (M,) SA monthly inflation, decimal
    usdzar_log_change: np.ndarray  # (M,) ln(USDZAR_m / USDZAR_{m-1})
    us_cpi: np.ndarray | None = None  # (M,) US monthly inflation; a constant from the config when absent
    start: str = ""

    def __post_init__(self) -> None:
        m = len(self.cpi)
        missing = set(ASSET_CLASSES) - set(self.returns)
        if missing:
            raise ValueError(f"historical series lacks {sorted(missing)}")
        if any(len(np.asarray(self.returns[k])) != m for k in ASSET_CLASSES) or len(self.usdzar_log_change) != m:
            raise ValueError("every monthly series must have the same length")
        if m < 24:
            raise ValueError("a block bootstrap needs at least 24 months of history")

    @property
    def n_months(self) -> int:
        return len(self.cpi)

    def real_monthly(self, us_cpi_annual: float, is_global: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(M, 5) real log returns in each class's own currency, (M,) real USDZAR log change, (M,) SA log CPI."""
        sa = np.log1p(np.asarray(self.cpi, dtype=float))
        us = np.log1p(np.asarray(self.us_cpi, dtype=float)) if self.us_cpi is not None else np.full_like(sa, np.log1p(us_cpi_annual) / 12)
        nominal = np.stack([np.log1p(np.asarray(self.returns[k], dtype=float)) for k in ASSET_CLASSES], axis=1)
        real = nominal - np.where(is_global, us[:, None], sa[:, None])
        fx_real = np.asarray(self.usdzar_log_change, dtype=float) - sa + us
        return real, fx_real, sa


@dataclass(frozen=True)
class BootstrapShocks:
    """Group M randomness for the bootstrap: the resampled month indices, (P, 12T)."""

    idx: np.ndarray

    @property
    def n_paths(self) -> int:
        return int(self.idx.shape[0])

    def take(self, rows: np.ndarray) -> "BootstrapShocks":
        return BootstrapShocks(self.idx[rows])


class StationaryBlockBootstrap:
    """Politis–Romano stationary block bootstrap of a monthly history, aggregated to annual steps (§12.6).

    With ``recentre`` (default) each series keeps its historical shape (volatility, fat tails, autocorrelation
    within blocks, cross-correlation) but is shifted to the MVN-t model's means, so a PoS gap measures the
    return model, not a disagreement about expected returns. The portfolio means are anchored to the document
    exactly as in the MVN-t model.
    """

    def __init__(self, series: HistoricalSeries, model: MarketModel, *, mean_block_months: int | None = None, recentre: bool | None = None) -> None:
        cfg = model.config.block_bootstrap
        self.model = model
        self.series = series
        self.mean_block = float(mean_block_months or cfg.mean_block_months)
        self.recentre = cfg.recentre if recentre is None else recentre
        real, fx, sa = series.real_monthly(cfg.us_cpi_annual, model.cma.is_global)
        if self.recentre:
            real = real - real.mean(axis=0) + model.class_mu / 12
            fx = fx - fx.mean() + model.fx_mu / 12
            sa = sa - sa.mean() + np.log1p(model.cpi) / 12
        self.real, self.fx, self.sa = real, fx, sa

    def shocks(self, seed: int, n_paths: int, n_years: int, *, matrix: str = "") -> BootstrapShocks:
        g = stream(seed, "bootstrap", matrix)
        months = 12 * n_years
        m = self.series.n_months
        new_block = g.random((n_paths, months)) < 1.0 / self.mean_block
        new_block[:, 0] = True
        starts = g.integers(0, m, size=(n_paths, months))
        k = np.broadcast_to(np.arange(months), (n_paths, months))
        last = np.maximum.accumulate(np.where(new_block, k, 0), axis=1)  # where the current block began
        idx = (np.take_along_axis(starts, last, axis=1) + (k - last)) % m
        return BootstrapShocks(idx)

    def realise(self, shocks: BootstrapShocks) -> MarketPaths:
        p, months = shocks.idx.shape
        t = months // 12
        fx = self.fx[shocks.idx].reshape(p, t, 12).sum(axis=2)
        infl = np.expm1(self.sa[shocks.idx].reshape(p, t, 12).sum(axis=2))
        real = np.empty((N_CLASSES, p, t))
        for k in range(N_CLASSES):
            real[k] = self.real[:, k][shocks.idx].reshape(p, t, 12).sum(axis=2)
            if self.model.cma.is_global[k]:
                real[k] += fx
        return MarketPaths(real, infl, fx, self.model.class_mean.copy())
