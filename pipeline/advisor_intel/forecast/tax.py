"""Tax for the Monte Carlo layer: one tax implementation, the ported FNA engine (spec §12.3 step 3, §12.7).

The engine works in today's money. SARS brackets beyond the latest table are indexed by CPI (§12.3); indexing
by each path's own inflation leaves the tax function unchanged in real terms, so the engine taxes real
amounts with the document's tax-year table every year. (Indexing every rand limit this way is
``indexed_table(..., index_limits=True)``; the FNA engine keeps the s11F cap and UIF ceiling nominal by
default, a second-order difference the forecast does not model.)

``TaxModel`` is the seam: ``FnaEngineTaxModel`` imports ``advisor_intel.fna_engine`` lazily and delegates every
SARS rule to it; tests pass a simple deterministic model instead. Nothing in the forecast package computes tax.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

ArrayLike = np.ndarray | float


@dataclass(frozen=True)
class PersonTax:
    """Annual income tax (after rebates and s6A credits) and the monthly UIF, element by element."""

    annual_tax: np.ndarray
    monthly_uif: np.ndarray


@runtime_checkable
class TaxModel(Protocol):
    """What the forecast needs from the tax module. All amounts are rand in today's money, vectorised."""

    version: str

    def person_tax(
        self,
        *,
        age: ArrayLike,
        gross_monthly: ArrayLike,
        annual_bonus: ArrayLike = 0.0,
        other_taxable_monthly: ArrayLike = 0.0,
        retirement_contributions_annual: ArrayLike = 0.0,
        employer_retirement_annual: ArrayLike = 0.0,
        medical_scheme_members: ArrayLike = 0.0,
        uif_applies: np.ndarray | bool = True,
    ) -> PersonTax:
        """Assessed tax for one person-year: s11F deduction, brackets, rebates, s6A credits and UIF (``personTax``)."""
        ...

    def income_tax(self, taxable_annual: ArrayLike, age: ArrayLike) -> np.ndarray:
        """Normal tax after rebates on a taxable income (``incomeTax``)."""
        ...

    def gross_up_monthly(
        self,
        net_monthly: ArrayLike,
        *,
        age: ArrayLike,
        retirement_monthly: ArrayLike = 0.0,
        medical_members: ArrayLike = 0.0,
        medical_payroll_monthly: ArrayLike = 0.0,
        other_payroll_monthly: ArrayLike = 0.0,
        uif: np.ndarray | bool = True,
    ) -> np.ndarray:
        """The gross monthly salary whose take-home pay is ``net_monthly`` (§7.6, §12.2 basis unknown)."""
        ...


class TaxModelUnavailable(RuntimeError):
    """The default tax model needs the ported FNA engine (``advisor_intel.fna_engine``)."""


def gross_up_by_bisection(
    tax_model: TaxModel,
    net_monthly: ArrayLike,
    *,
    age: ArrayLike,
    retirement_monthly: ArrayLike = 0.0,
    medical_members: ArrayLike = 0.0,
    medical_payroll_monthly: ArrayLike = 0.0,
    other_payroll_monthly: ArrayLike = 0.0,
    uif: np.ndarray | bool = True,
    iterations: int = 60,
) -> np.ndarray:
    """Invert the payroll view of take-home pay with ``tax_model.person_tax``, element by element.

    Take-home = gross − PAYE − UIF − payroll retirement − medical aid − other payroll deductions, with PAYE
    from the s11F deduction on the payroll contributions (the view ``fna_engine.tax.gross_up_monthly``
    inverts one value at a time). Take-home rises strictly with gross, so bisection converges.
    """
    net = np.atleast_1d(np.asarray(net_monthly, dtype=float))
    shape = net.shape
    ret = np.broadcast_to(np.asarray(retirement_monthly, dtype=float), shape)
    med = np.broadcast_to(np.asarray(medical_payroll_monthly, dtype=float), shape)
    oth = np.broadcast_to(np.asarray(other_payroll_monthly, dtype=float), shape)
    fixed = ret + med + oth

    def take_home(gross: np.ndarray) -> np.ndarray:
        t = tax_model.person_tax(
            age=age, gross_monthly=gross, retirement_contributions_annual=ret * 12, medical_scheme_members=medical_members, uif_applies=uif
        )
        return gross - t.annual_tax / 12 - t.monthly_uif - fixed

    lo = np.zeros(shape)
    hi = np.maximum(1.0, 2 * (np.maximum(net, 0) + fixed))
    for _ in range(64):  # widen until take-home at hi covers the target
        short = take_home(hi) < net
        if not short.any():
            break
        hi = np.where(short, hi * 2, hi)
    for _ in range(iterations):
        mid = (lo + hi) / 2
        below = take_home(mid) < net
        lo = np.where(below, mid, lo)
        hi = np.where(below, hi, mid)
    out = (lo + hi) / 2
    return np.where(take_home(np.zeros(shape)) >= net, 0.0, out)


class FnaEngineTaxModel:
    """SARS tax from ``advisor_intel.fna_engine.tax`` (§12.7), for the document's tax year.

    The table is ``tables.table_for(tax_year)``, which falls back to the default year for an unknown one exactly
    as the app and ``fna_engine.analysis.analyse`` do, so the forecast taxes with the table the adviser sees.
    """

    def __init__(self, tax_year: str = "2026/27") -> None:
        try:
            from ..fna_engine import tables as fna_tables
            from ..fna_engine import tax as fna_tax
        except ImportError as e:  # pragma: no cover - exercised only without the engine
            raise TaxModelUnavailable(
                "the forecast taxes income with the ported FNA engine (advisor_intel.fna_engine, spec §12.7), which is not "
                "installed; pass tax_model= explicitly"
            ) from e
        self._tax = fna_tax
        self.table = fna_tables.table_for(tax_year)
        self.version = f"fna_engine:{self.table.taxYear}"

    def person_tax(
        self,
        *,
        age: ArrayLike,
        gross_monthly: ArrayLike,
        annual_bonus: ArrayLike = 0.0,
        other_taxable_monthly: ArrayLike = 0.0,
        retirement_contributions_annual: ArrayLike = 0.0,
        employer_retirement_annual: ArrayLike = 0.0,
        medical_scheme_members: ArrayLike = 0.0,
        uif_applies: np.ndarray | bool = True,
    ) -> PersonTax:
        r = self._tax.person_tax_vec(
            age,
            gross_monthly,
            self.table,
            annual_bonus=annual_bonus,
            other_taxable_monthly=other_taxable_monthly,
            retirement_contributions_annual=retirement_contributions_annual,
            employer_retirement_annual=employer_retirement_annual,
            medical_scheme_members=medical_scheme_members,
            uif_applies=uif_applies,
        )
        return PersonTax(annual_tax=np.asarray(r["annualTax"], dtype=float), monthly_uif=np.asarray(r["monthlyUif"], dtype=float))

    def income_tax(self, taxable_annual: ArrayLike, age: ArrayLike) -> np.ndarray:
        return np.asarray(self._tax.income_tax_vec(taxable_annual, age, self.table), dtype=float)

    def gross_up_monthly(
        self,
        net_monthly: ArrayLike,
        *,
        age: ArrayLike,
        retirement_monthly: ArrayLike = 0.0,
        medical_members: ArrayLike = 0.0,
        medical_payroll_monthly: ArrayLike = 0.0,
        other_payroll_monthly: ArrayLike = 0.0,
        uif: np.ndarray | bool = True,
    ) -> np.ndarray:
        return gross_up_by_bisection(
            self,
            net_monthly,
            age=age,
            retirement_monthly=retirement_monthly,
            medical_members=medical_members,
            medical_payroll_monthly=medical_payroll_monthly,
            other_payroll_monthly=other_payroll_monthly,
            uif=uif,
        )


def default_tax_model(tax_year: str = "2026/27") -> TaxModel:
    """The pipeline's one tax implementation; raises ``TaxModelUnavailable`` without the FNA engine."""
    return FnaEngineTaxModel(tax_year)


def marginal_rate(tax_model: TaxModel, taxable_annual: np.ndarray, age: ArrayLike, step: float = 1_000.0) -> np.ndarray:
    """The marginal income tax rate at ``taxable_annual`` (used to gross up extra annuity withdrawals)."""
    base = np.asarray(taxable_annual, dtype=float)
    return np.clip((tax_model.income_tax(base + step, age) - tax_model.income_tax(base, age)) / step, 0.0, 0.95)
