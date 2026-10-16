"""SARS income tax and the other taxes the FNA needs: a port of ``lib/fna/tax.ts`` (§12.7).

Every SARS value comes from the shared table files (:mod:`.tables`). The Monte Carlo layer imports this
module (§12.3): nothing else in the pipeline computes tax. ``*_vec`` functions are the numpy versions and
give the same results element by element.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, NamedTuple

import numpy as np
from scipy.optimize import brentq

from ..models.fna import DEFAULT_TAX_YEAR
from .results import PersonTaxResult
from .tables import Bracket, TaxTable, TaxTableFile, _tables, available_tax_years, get_table, indexed_table, table_for, table_for_year

__all__ = [
    "DEFAULT_TAX_YEAR",
    "PersonTaxInput",
    "apply_brackets",
    "apply_brackets_vec",
    "capital_gains_tax",
    "donations_tax",
    "estate_duty",
    "get_table",
    "get_tax_table",
    "get_tax_table_file",
    "gross_up_monthly",
    "income_tax",
    "income_tax_vec",
    "incremental_tax",
    "indexed_table",
    "interest_exemption",
    "lump_sum_tax_vec",
    "marginal_rate",
    "medical_tax_credit",
    "person_tax",
    "person_tax_vec",
    "rebates_for",
    "retirement_deduction",
    "retirement_lump_sum_tax",
    "savings_withdrawal_tax",
    "table_for_year",
    "tax_tables",
    "tax_threshold",
    "tax_years",
    "taxable_capital_gain",
    "uif_monthly",
    "withdrawal_lump_sum_tax",
]

# --------------------------------------------------------------------------- tables (TAX_TABLES, getTaxTable, TAX_YEARS)

get_tax_table = table_for  # getTaxTable: an unknown year falls back to the default year
get_tax_table_file = table_for  # getTaxTableFile


def tax_tables(tables_dir: Path | str | None = None) -> dict[str, TaxTableFile]:
    """``TAX_TABLES``: every table file by tax year, newest first."""
    return dict(_tables(tables_dir))


def tax_years(tables_dir: Path | str | None = None) -> list[str]:
    """``TAX_YEARS()``."""
    return available_tax_years(tables_dir)


# --------------------------------------------------------------------------- brackets, rebates and credits


def apply_brackets(amount: float, brackets: Sequence[Bracket]) -> float:
    """``applyBrackets``: a progressive table whose brackets carry cumulative base amounts."""
    if amount <= 0:
        return 0.0
    bracket = brackets[0]
    for b in brackets:
        if amount > b.from_:
            bracket = b
    return bracket.base + (amount - bracket.from_) * bracket.rate


def _apply(amount: float, froms: tuple[float, ...], bases: tuple[float, ...], rates: tuple[float, ...]) -> float:
    """``apply_brackets`` on a validated (ascending) table: the bracket is the last one starting below ``amount``."""
    if amount <= 0:
        return 0.0
    i = bisect_left(froms, amount) - 1
    return bases[i] + (amount - froms[i]) * rates[i]


def marginal_rate(taxable: float, table: TaxTable) -> float:
    f = table.fast
    return f.rates[bisect_left(f.froms, taxable) - 1] if taxable > f.froms[0] else f.rates[0]


def rebates_for(age: float, table: TaxTable) -> float:
    r = table.rebates
    rebate = r.primary
    if age >= 65:
        rebate += r.secondary
    if age >= 75:
        rebate += r.tertiary
    return rebate


def tax_threshold(age: float, table: TaxTable) -> float:
    """Income below which no tax is payable, for the age on the last day of the tax year."""
    t = table.thresholds
    return t.under65 if age < 65 else t.age65to74 if age < 75 else t.age75plus


def medical_tax_credit(members: float, table: TaxTable) -> float:
    """Annual s6A medical scheme fees tax credit."""
    if members <= 0:
        return 0.0
    m = table.medicalCredits
    monthly = m.main + (m.firstDependant if members >= 2 else 0.0) + max(0.0, members - 2) * m.additional
    return monthly * 12


def income_tax(taxable_annual: float, age: float, table: TaxTable) -> float:
    """Normal tax after rebates, before medical credits (``incomeTax``)."""
    f = table.fast
    return max(0.0, _apply(taxable_annual, f.froms, f.bases, f.rates) - rebates_for(age, table))


def retirement_deduction(contributions: float, remuneration: float, table: TaxTable) -> float:
    """s11F: the lesser of contributions, the rate times remuneration (or taxable income) and the annual cap."""
    rd = table.retirementDeduction
    return max(0.0, min(contributions, rd.rate * remuneration, rd.cap))


def uif_monthly(gross_monthly: float, table: TaxTable, applies: bool = True) -> float:
    """Employee UIF on remuneration up to the monthly ceiling."""
    return min(gross_monthly, table.uif.ceilingMonthly) * table.uif.rate if applies else 0.0


def interest_exemption(age: float, table: TaxTable) -> float:
    e = table.interestExemption
    return e.under65 if age < 65 else e.over65


class PersonTaxInput(NamedTuple):
    """``PersonTaxInput``: annual retirement contributions include employee, employer fringe and RA amounts."""

    age: float
    gross_monthly: float
    annual_bonus: float = 0.0
    other_taxable_monthly: float = 0.0
    retirement_contributions_annual: float = 0.0
    employer_retirement_annual: float = 0.0  # a taxable fringe benefit, s7(8)
    medical_scheme_members: float = 0.0
    uif_applies: bool = True


def _person_tax(i: PersonTaxInput, table: TaxTable) -> tuple[float, ...]:
    cash = i.gross_monthly * 12 + i.annual_bonus + i.other_taxable_monthly * 12
    # Employer contributions are a fringe benefit included in remuneration and then deductible,
    # so they count on both sides of the s11F calculation.
    remuneration = cash + i.employer_retirement_annual
    deduction = retirement_deduction(i.retirement_contributions_annual, remuneration, table)
    taxable = max(0.0, remuneration - deduction)
    f = table.fast
    before = _apply(taxable, f.froms, f.bases, f.rates)
    rebates = min(before, rebates_for(i.age, table))
    after_rebates = max(0.0, before - rebates)
    medical = min(after_rebates, medical_tax_credit(i.medical_scheme_members, table))
    annual = max(0.0, after_rebates - medical)
    uif = min(i.gross_monthly, table.uif.ceilingMonthly) * table.uif.rate if i.uif_applies else 0.0
    marginal = marginal_rate(taxable, table) if taxable > 0 else 0.0
    effective = annual / cash if cash > 0 else 0.0
    return cash, remuneration, deduction, taxable, before, rebates, medical, annual, annual / 12, uif, marginal, effective


def person_tax(inp: PersonTaxInput, table: TaxTable) -> PersonTaxResult:
    """``personTax``: assessed tax, PAYE and UIF for one person."""
    (cash, remuneration, deduction, taxable, before, rebates, medical, annual, paye, uif, marginal, effective) = _person_tax(inp, table)
    return PersonTaxResult.build(
        {
            "grossAnnual": cash,
            "remuneration": remuneration,
            "retirementDeduction": deduction,
            "taxable": taxable,
            "taxBeforeRebates": before,
            "rebates": rebates,
            "medicalCredit": medical,
            "annualTax": annual,
            "monthlyPaye": paye,
            "monthlyUif": uif,
            "marginalRate": marginal,
            "effectiveRate": effective,
        }
    )


# --------------------------------------------------------------------------- lump sums, estate duty, CGT, donations


def retirement_lump_sum_tax(amount: float, table: TaxTable, prior_lump_sums: float = 0.0) -> float:
    """Tax on a retirement or death lump sum (retirement table), net of tax on earlier lump sums."""
    f = table.fast
    t = (f.ret_froms, f.ret_bases, f.ret_rates)
    return max(0.0, _apply(amount + prior_lump_sums, *t) - _apply(prior_lump_sums, *t))


def withdrawal_lump_sum_tax(amount: float, table: TaxTable, prior_lump_sums: float = 0.0) -> float:
    """Tax on a pre-retirement withdrawal lump sum (withdrawal table)."""
    f = table.fast
    t = (f.wd_froms, f.wd_bases, f.wd_rates)
    return max(0.0, _apply(amount + prior_lump_sums, *t) - _apply(prior_lump_sums, *t))


def estate_duty(dutiable: float, table: TaxTable) -> float:
    """Estate duty on the dutiable amount (after the abatement)."""
    if dutiable <= 0:
        return 0.0
    e = table.estateDuty
    return min(dutiable, e.threshold) * e.rateLow + max(0.0, dutiable - e.threshold) * e.rateHigh


def incremental_tax(base_taxable: float, extra_taxable: float, age: float, table: TaxTable) -> float:
    """Income tax caused by adding ``extra_taxable`` to a base taxable income."""
    return max(0.0, income_tax(base_taxable + extra_taxable, age, table) - income_tax(base_taxable, age, table))


def taxable_capital_gain(gain: float, table: TaxTable, *, death: bool = False) -> float:
    """The capital gain included in taxable income after the annual (or year-of-death) exclusion."""
    c = table.cgt
    return max(0.0, gain - (c.deathExclusion if death else c.annualExclusion)) * c.inclusionRate


def capital_gains_tax(gain: float, base_taxable: float, age: float, table: TaxTable, *, death: bool = False) -> float:
    """CGT as the income tax on the included gain at the person's marginal rates (as ``computeEstate`` does on death)."""
    return incremental_tax(base_taxable, taxable_capital_gain(gain, table, death=death), age, table)


def donations_tax(amount: float, table: TaxTableFile, *, exemption_used: float = 0.0, prior_taxable: float = 0.0) -> float:
    """Donations tax on donations made in one tax year: the annual exemption first, then the low rate up to
    the cumulative threshold (counting ``prior_taxable`` donations since 1 March 2018) and the high rate above."""
    d = table.donations
    taxable = max(0.0, amount - max(0.0, d.annualExemption - exemption_used))

    def cumulative(x: float) -> float:
        return min(x, d.threshold) * d.rateLow + max(0.0, x - d.threshold) * d.rateHigh

    return cumulative(prior_taxable + taxable) - cumulative(prior_taxable)


def savings_withdrawal_tax(amount: float, taxable_income: float, age: float, table: TaxTableFile) -> float:
    """Tax on a two-pot savings withdrawal: added to taxable income and taxed at the member's marginal rates."""
    if amount < table.savingsWithdrawal.minimum:
        raise ValueError(f"a savings withdrawal must be at least R{table.savingsWithdrawal.minimum:,.0f}")
    return incremental_tax(taxable_income, amount, age, table)


# --------------------------------------------------------------------------- net pay → gross (§7.6)


def gross_up_monthly(
    net_monthly: float,
    *,
    age: float,
    table: TaxTable,
    retirement_monthly: float = 0.0,
    medical_members: float = 0.0,
    medical_payroll_monthly: float = 0.0,
    other_payroll_monthly: float = 0.0,
    uif: bool = True,
) -> float:
    """The gross monthly salary whose take-home pay is ``net_monthly``.

    Inverts the payroll view of ``computeIncome`` (PAYE with the s11F deduction for ``retirement_monthly``
    deducted through payroll and the s6A credits, UIF, then the payroll deductions) with Brent's method.
    Take-home pay rises strictly with gross pay (marginal deductions stay below 100%), so the root is unique.
    """
    fixed = retirement_monthly + medical_payroll_monthly + other_payroll_monthly

    def gap(gross: float) -> float:
        t = _person_tax(PersonTaxInput(age, gross, 0.0, 0.0, retirement_monthly * 12, 0.0, medical_members, uif), table)
        return gross - t[8] - t[9] - retirement_monthly - medical_payroll_monthly - other_payroll_monthly - net_monthly

    if gap(0.0) >= 0:
        return 0.0
    hi = max(1.0, 2 * (net_monthly + fixed))
    for _ in range(64):
        if gap(hi) > 0:
            break
        hi *= 2
    return float(brentq(gap, 0.0, hi, xtol=1e-7, rtol=1e-14, maxiter=200))


# --------------------------------------------------------------------------- vectorised (numpy)


def _arrays(table: TaxTable) -> dict[str, np.ndarray]:
    f = table.fast
    if f.np is None:
        f.np = {
            "income": (np.array(f.froms), np.array(f.bases), np.array(f.rates)),
            "retirement": (np.array(f.ret_froms), np.array(f.ret_bases), np.array(f.ret_rates)),
            "withdrawal": (np.array(f.wd_froms), np.array(f.wd_bases), np.array(f.wd_rates)),
        }
    return f.np


def apply_brackets_vec(amount: np.ndarray | float, froms: np.ndarray, bases: np.ndarray, rates: np.ndarray) -> np.ndarray:
    """``apply_brackets`` element by element; ``froms`` must be ascending."""
    amount = np.asarray(amount, dtype=float)
    i = np.maximum(np.searchsorted(froms, amount, side="left") - 1, 0)
    return np.where(amount <= 0, 0.0, bases[i] + (amount - froms[i]) * rates[i])


def _rebates_vec(age: np.ndarray, table: TaxTable) -> np.ndarray:
    r = table.rebates
    return r.primary + np.where(age >= 65, r.secondary, 0.0) + np.where(age >= 75, r.tertiary, 0.0)


def income_tax_vec(taxable: np.ndarray | float, age: np.ndarray | float, table: TaxTable, *, index: np.ndarray | float = 1.0) -> np.ndarray:
    """``income_tax`` element by element.

    ``index`` is a CPI factor per element: brackets, rebates and thresholds scale by it, which equals
    ``income_tax(taxable, age, indexed_table(...))`` up to rounding, so each simulation path can carry its own
    inflation (§12.3)."""
    taxable = np.asarray(taxable, dtype=float)
    age = np.asarray(age, dtype=float)
    froms, bases, rates = _arrays(table)["income"]
    if np.isscalar(index) and index == 1.0:
        return np.maximum(0.0, apply_brackets_vec(taxable, froms, bases, rates) - _rebates_vec(age, table))
    index = np.asarray(index, dtype=float)
    return index * np.maximum(0.0, apply_brackets_vec(taxable / index, froms, bases, rates) - _rebates_vec(age, table))


def person_tax_vec(
    age: np.ndarray | float,
    gross_monthly: np.ndarray | float,
    table: TaxTable,
    *,
    annual_bonus: np.ndarray | float = 0.0,
    other_taxable_monthly: np.ndarray | float = 0.0,
    retirement_contributions_annual: np.ndarray | float = 0.0,
    employer_retirement_annual: np.ndarray | float = 0.0,
    medical_scheme_members: np.ndarray | float = 0.0,
    uif_applies: np.ndarray | bool = True,
) -> dict[str, np.ndarray]:
    """``person_tax`` element by element, keyed like ``PersonTaxResult``."""
    age = np.asarray(age, dtype=float)
    gross = np.asarray(gross_monthly, dtype=float)
    cash = gross * 12 + np.asarray(annual_bonus, dtype=float) + np.asarray(other_taxable_monthly, dtype=float) * 12
    remuneration = cash + np.asarray(employer_retirement_annual, dtype=float)
    rd = table.retirementDeduction
    deduction = np.maximum(
        0.0, np.minimum(np.minimum(np.asarray(retirement_contributions_annual, dtype=float), rd.rate * remuneration), rd.cap)
    )
    taxable = np.maximum(0.0, remuneration - deduction)
    froms, bases, rates = _arrays(table)["income"]
    before = apply_brackets_vec(taxable, froms, bases, rates)
    rebates = np.minimum(before, _rebates_vec(age, table))
    after_rebates = np.maximum(0.0, before - rebates)
    members = np.asarray(medical_scheme_members, dtype=float)
    m = table.medicalCredits
    monthly_credit = m.main + np.where(members >= 2, m.firstDependant, 0.0) + np.maximum(0.0, members - 2) * m.additional
    medical = np.minimum(after_rebates, np.where(members <= 0, 0.0, monthly_credit * 12))
    annual = np.maximum(0.0, after_rebates - medical)
    uif = np.where(np.asarray(uif_applies, dtype=bool), np.minimum(gross, table.uif.ceilingMonthly) * table.uif.rate, 0.0)
    i = np.clip(np.searchsorted(froms, taxable, side="left") - 1, 0, None)
    marginal = np.where(taxable > 0, rates[i], 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        effective = np.where(cash > 0, annual / np.where(cash > 0, cash, 1.0), 0.0)
    out = {
        "grossAnnual": cash,
        "remuneration": remuneration,
        "retirementDeduction": deduction,
        "taxable": taxable,
        "taxBeforeRebates": before,
        "rebates": rebates,
        "medicalCredit": medical,
        "annualTax": annual,
        "monthlyPaye": annual / 12,
        "monthlyUif": uif,
        "marginalRate": marginal,
        "effectiveRate": effective,
    }
    shape = np.broadcast_shapes(*(np.shape(v) for v in out.values()))
    return {k: np.broadcast_to(v, shape).astype(float) for k, v in out.items()}


def lump_sum_tax_vec(
    amount: np.ndarray | float,
    table: TaxTable,
    *,
    kind: Literal["retirement", "withdrawal"] = "retirement",
    prior_lump_sums: np.ndarray | float = 0.0,
) -> np.ndarray:
    """``retirement_lump_sum_tax`` / ``withdrawal_lump_sum_tax`` element by element."""
    t = _arrays(table)[kind]
    prior = np.asarray(prior_lump_sums, dtype=float)
    return np.maximum(0.0, apply_brackets_vec(np.asarray(amount, dtype=float) + prior, *t) - apply_brackets_vec(prior, *t))
