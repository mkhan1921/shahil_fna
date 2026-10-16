"""Time-value-of-money primitives: a port of ``lib/fna/finance.ts`` (§12.7).

Annual rates are effective; monthly rates are (1 + annual) ** (1/12) - 1, so twelve monthly periods
reproduce the annual rate. The TypeScript simulates escalating contributions month by month; here each
year is one step (a year of level contributions has a closed form), which matches the monthly loop to
about 1e-15 relative and is ten times faster. ``*_vec`` functions take numpy arrays (or scalars that
broadcast) and give the scalar results element by element.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from functools import lru_cache

import numpy as np

from ._js import INF, js_round, pw

# --------------------------------------------------------------------------- scalar (the engine's own arithmetic)


@lru_cache(maxsize=1024)
def monthly_rate(annual: float) -> float:
    return pw(1 + annual, 1 / 12) - 1


def real_rate(nominal: float, inflation: float) -> float:
    """(1 + nominal) / (1 + inflation) − 1."""
    return (1 + nominal) / (1 + inflation) - 1


def annuity_factor(rate: float, periods: float) -> float:
    """Present value of an ordinary annuity of 1 a period, a(n, r)."""
    if periods <= 0:
        return 0.0
    if abs(rate) < 1e-12:
        return float(periods)
    return (1 - pw(1 + rate, -periods)) / rate


def pv_growing_monthly_income(monthly_today: float, years: float, discount: float, growth: float, in_advance: bool = True) -> float:
    """Present value of a monthly income escalating at ``growth`` a year for ``years``, paid in advance and
    discounted at ``discount``: a level annuity at the real rate."""
    if monthly_today <= 0 or years <= 0:
        return 0.0
    r = monthly_rate(real_rate(discount, growth))
    n = js_round(years * 12)
    factor = annuity_factor(r, n) * (1 + r if in_advance else 1)
    return monthly_today * factor


def pv_growing_annuity(payment: float, years: float, discount: float, growth: float, due: bool = True) -> float:
    """Present value of an annual payment escalating at ``growth`` for ``years`` payments (``due``: first payment now)."""
    if payment <= 0 or years <= 0:
        return 0.0
    if abs(discount - growth) < 1e-12:
        return years * payment if due else (years * payment) / (1 + discount)
    factor = (1 - pw((1 + growth) / (1 + discount), years)) / (discount - growth)
    return payment * factor * (1 + discount if due else 1)


def fv(present: float, annual: float, years: float) -> float:
    return present * pw(1 + annual, years)


def pv(future: float, annual: float, years: float) -> float:
    return future if years <= 0 else future / pw(1 + annual, years)


@lru_cache(maxsize=1024)
def _year_factors(g: float, months: int) -> tuple[float, float]:
    """(g ** months, 1 + g + … + g ** (months − 1)): growth of a balance and of level end-of-month payments."""
    s = 0.0
    p = 1.0
    for _ in range(months):
        s += p
        p *= g
    return p, s


def fv_escalating_contributions(monthly: float, years: float, annual_return: float, escalation: float) -> float:
    """Future value of monthly contributions paid at month ends and escalated once a year."""
    if monthly <= 0 or years <= 0:
        return 0.0
    g = 1 + monthly_rate(annual_return)
    n = js_round(years * 12)
    if not n >= 1:  # also NaN: the TypeScript loop does not run
        return 0.0
    full, rest = divmod(int(n), 12)
    g12, s12 = _year_factors(g, 12)
    value = 0.0
    contribution = monthly
    for _ in range(full):
        value = value * g12 + contribution * s12
        contribution *= 1 + escalation
    if rest:
        gr, sr = _year_factors(g, rest)
        value = value * gr + contribution * sr
    return value


def loan_repayment(principal: float, annual_nominal_rate: float, months: float) -> float:
    """Level monthly repayment on a loan (ordinary annuity)."""
    if principal <= 0 or months <= 0:
        return 0.0
    r = annual_nominal_rate / 12
    if r == 0:
        return principal / months
    return (principal * r) / (1 - pw(1 + r, -months))


def months_to_repay(balance: float, annual_nominal_rate: float, instalment: float) -> float:
    """Months to repay at an instalment (rate compounded monthly); Infinity when it does not cover the interest."""
    if balance <= 0:
        return 0.0
    if instalment <= 0:
        return INF
    r = annual_nominal_rate / 12
    if r == 0:
        return float(math.ceil(balance / instalment))
    if instalment <= balance * r:
        return INF
    return float(math.ceil(-math.log(1 - (balance * r) / instalment) / math.log(1 + r) - 1e-9))


def remaining_interest(balance: float, annual_nominal_rate: float, instalment: float) -> float:
    """Total interest still payable at the current instalment (the final instalment is smaller)."""
    n = months_to_repay(balance, annual_nominal_rate, instalment)
    if not math.isfinite(n):
        return INF
    r = annual_nominal_rate / 12
    b = balance
    paid = 0.0
    i = 0
    while i < n and b > 0.005:
        interest = b * r
        payment = min(instalment, b + interest)
        paid += payment
        b = b + interest - payment
        i += 1
    return max(0.0, paid - balance)


def required_escalating_saving(target: float, years: float, annual_return: float, escalation: float) -> float:
    """Level monthly saving, escalating yearly, that reaches ``target`` in ``years``."""
    if target <= 0:
        return 0.0
    if years <= 0:
        return INF
    unit = fv_escalating_contributions(1, years, annual_return, escalation)
    return target / unit if unit > 0 else INF


def clamp(value: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, value))


def sum_finite[T](items: Iterable[T], pick: Callable[[T], float]) -> float:
    """``sum`` in finance.ts: left to right, skipping values that are not finite."""
    total = 0.0
    for item in items:
        v = pick(item)
        if v - v == 0:  # finite: inf - inf and nan - nan are nan
            total += v
    return total


def round_to(value: float, step: float = 1) -> float:
    """``round(value, step)``: Math.round(value / step) * step."""
    return js_round(value / step) * step


# --------------------------------------------------------------------------- numpy (many paths or documents at once)


def _f(x: np.ndarray | float) -> np.ndarray:
    return np.asarray(x, dtype=float)


def monthly_rate_vec(annual: np.ndarray | float) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return np.power(1 + _f(annual), 1 / 12) - 1


def real_rate_vec(nominal: np.ndarray | float, inflation: np.ndarray | float) -> np.ndarray:
    return (1 + _f(nominal)) / (1 + _f(inflation)) - 1


def annuity_factor_vec(rate: np.ndarray | float, periods: np.ndarray | float) -> np.ndarray:
    rate, periods = np.broadcast_arrays(_f(rate), _f(periods))
    small = np.abs(rate) < 1e-12
    with np.errstate(divide="ignore", invalid="ignore"):
        af = (1 - np.power(1 + rate, -periods)) / np.where(small, 1.0, rate)
    return np.where(periods <= 0, 0.0, np.where(small, periods, af))


def _js_round_vec(x: np.ndarray) -> np.ndarray:
    """``Math.round``, element by element (ties towards +∞)."""
    f = np.floor(x)
    return np.where(x - f >= 0.5, f + 1, f)


def pv_growing_monthly_income_vec(
    monthly_today: np.ndarray | float,
    years: np.ndarray | float,
    discount: np.ndarray | float,
    growth: np.ndarray | float,
    in_advance: bool = True,
) -> np.ndarray:
    m, y = _f(monthly_today), _f(years)
    r = monthly_rate_vec(real_rate_vec(discount, growth))
    factor = annuity_factor_vec(r, _js_round_vec(y * 12)) * (1 + r if in_advance else 1)
    return np.where((m <= 0) | (y <= 0), 0.0, m * factor)


def pv_growing_annuity_vec(
    payment: np.ndarray | float, years: np.ndarray | float, discount: np.ndarray | float, growth: np.ndarray | float, due: bool = True
) -> np.ndarray:
    p, y, d, g = np.broadcast_arrays(_f(payment), _f(years), _f(discount), _f(growth))
    level = np.abs(d - g) < 1e-12
    with np.errstate(divide="ignore", invalid="ignore"):
        factor = (1 - np.power((1 + g) / (1 + d), y)) / np.where(level, 1.0, d - g)
    grow = p * factor * (1 + d if due else 1)
    flat = y * p if due else (y * p) / (1 + d)
    return np.where((p <= 0) | (y <= 0), 0.0, np.where(level, flat, grow))


def fv_vec(present: np.ndarray | float, annual: np.ndarray | float, years: np.ndarray | float) -> np.ndarray:
    return _f(present) * np.power(1 + _f(annual), _f(years))


def pv_vec(future: np.ndarray | float, annual: np.ndarray | float, years: np.ndarray | float) -> np.ndarray:
    y = _f(years)
    return np.where(y <= 0, _f(future), _f(future) / np.power(1 + _f(annual), y))


def fv_escalating_contributions_vec(
    monthly: np.ndarray | float, years: np.ndarray | float, annual_return: np.ndarray | float, escalation: np.ndarray | float
) -> np.ndarray:
    """``fv_escalating_contributions`` element by element, stepping a year at a time across all elements."""
    m, y, ret, esc = np.broadcast_arrays(_f(monthly), _f(years), _f(annual_return), _f(escalation))
    g = 1 + monthly_rate_vec(ret)
    months = np.where((m <= 0) | (y <= 0), 0, _js_round_vec(y * 12)).astype(np.int64)
    full, rest = np.divmod(months, 12)
    g12, s12 = np.ones_like(g), np.zeros_like(g)
    for _ in range(12):
        s12 += g12
        g12 = g12 * g
    value = np.zeros_like(g)
    contribution = m.copy()
    for k in range(int(full.max(initial=0))):
        active = k < full
        value = np.where(active, value * g12 + contribution * s12, value)
        contribution = np.where(active, contribution * (1 + esc), contribution)
    gr, sr = np.ones_like(g), np.zeros_like(g)
    for j in range(int(rest.max(initial=0))):
        active = j < rest
        sr = np.where(active, sr + gr, sr)
        gr = np.where(active, gr * g, gr)
    return np.where(months > 0, value * gr + contribution * sr, 0.0)


def required_escalating_saving_vec(
    target: np.ndarray | float, years: np.ndarray | float, annual_return: np.ndarray | float, escalation: np.ndarray | float
) -> np.ndarray:
    t, y = _f(target), _f(years)
    unit = fv_escalating_contributions_vec(1.0, y, annual_return, escalation)
    with np.errstate(divide="ignore", invalid="ignore"):
        need = np.where(unit > 0, t / np.where(unit > 0, unit, 1.0), INF)
    return np.where(t <= 0, 0.0, np.where(y <= 0, INF, need))


def loan_repayment_vec(principal: np.ndarray | float, annual_nominal_rate: np.ndarray | float, months: np.ndarray | float) -> np.ndarray:
    p, rate, n = np.broadcast_arrays(_f(principal), _f(annual_nominal_rate), _f(months))
    r = rate / 12
    with np.errstate(divide="ignore", invalid="ignore"):
        level = (p * r) / (1 - np.power(1 + r, -n))
        flat = p / n
    return np.where((p <= 0) | (n <= 0), 0.0, np.where(r == 0, flat, level))


def months_to_repay_vec(balance: np.ndarray | float, annual_nominal_rate: np.ndarray | float, instalment: np.ndarray | float) -> np.ndarray:
    b, rate, pay = np.broadcast_arrays(_f(balance), _f(annual_nominal_rate), _f(instalment))
    r = rate / 12
    with np.errstate(divide="ignore", invalid="ignore"):
        n = np.ceil(-np.log(1 - (b * r) / pay) / np.log(1 + r) - 1e-9)
        flat = np.ceil(b / pay)
    out = np.where(r == 0, flat, np.where(pay <= b * r, INF, n))
    return np.where(b <= 0, 0.0, np.where(pay <= 0, INF, out))
