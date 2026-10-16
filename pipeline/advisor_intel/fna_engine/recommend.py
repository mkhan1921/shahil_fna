"""Draft recommendations from the analysis: a port of ``lib/fna/recommend.ts`` (§12.7).

Starting points for the adviser, who confirms suitability, chooses products and edits the rationale. Each
draft gets a fresh id, as ``newRecommendation`` does; everything else matches the TypeScript.
"""

from __future__ import annotations

import math
from datetime import date, datetime

from ..models.fna import EstateProfile, FnaDocument, Recommendation
from ._js import Now, en_za, js_round, js_str, rand, to_fixed
from .catalog import UNSECURED_DEBT
from .results import Analysis
from .tables import table_for
from .tax import retirement_deduction

__all__ = ["draft_recommendations"]

_UNSECURED = frozenset(UNSECURED_DEBT)


def _round_up(n: float, step: float) -> float:
    return math.ceil(n / step) * step


def draft_recommendations(doc: FnaDocument, analysis: Analysis, *, today: date | datetime | Now | None = None) -> list[Recommendation]:
    """``draftRecommendations(doc, analysis, now)``; pass the ``today`` given to ``analyse`` for repeatable text."""
    now = Now.of(today)
    a = analysis
    out: list[Recommendation] = []
    table = table_for(doc.assumptions.taxYear)
    assumptions = doc.assumptions

    for key in a.people:
        p = a.persons.get(key)
        if p is None:
            continue
        name = p.name.split(" ")[0]
        held = {x.type for x in doc.policies if x.lifeAssured == key and not x.isGroup}

        if p.death.shortfall > 25_000:
            estate_note = (
                f" At least {rand(p.estate.liquidityShortfall)} should be payable to the estate (or to an heir who undertakes to fund it)"
                " to meet the estate liquidity shortfall without selling assets."
                if p.estate.liquidityShortfall > 0
                else ""
            )
            rationale = (
                f"The capital needed if {name} dies is {rand(p.death.need)} against existing provision of {rand(p.death.provision)}."
                f" The shortfall of {rand(p.death.shortfall)} would leave debts, estate costs, education and"
                f" {js_str(p.deathIncomeYears)} years of family income ({rand(p.deathIncomeMonthly)} p.m. in today’s money) unfunded."
                f"{estate_note} Group cover ends when employment ends, so personally owned cover is preferred."
            )
            out.append(
                Recommendation(
                    area="life",
                    lifeAssured=key,
                    action="increase" if "life" in held else "new",
                    productType="Life cover",
                    amount=_round_up(p.death.shortfall, 50_000),
                    rationale=rationale,
                )
            )

        if p.incomeProtection.shortfall > 500:
            months = a.ratios.emergencyMonths
            waiting = "3-month" if months >= 3 else "1-month" if months >= 1 else "7- or 14-day"
            benefit = _round_up(p.incomeProtection.shortfall, 500)
            rationale = (
                f"{name}’s after-tax income is {rand(p.income.afterTaxMonthly)} p.m. and existing income protection to retirement"
                f" is {rand(p.incomeProtection.provision)} p.m. A benefit of {rand(benefit)} p.m. restores cover to"
                f" {js_str(js_round(assumptions.incomeProtectionTarget * 100))}% of after-tax income. Benefits are tax-free."
                f" A {waiting} waiting period is suggested given an emergency fund of {to_fixed(months, 1)} months."
            )
            out.append(
                Recommendation(
                    area="income-protection",
                    lifeAssured=key,
                    action="increase" if "income-protection" in held else "new",
                    productType="Income protection (to age 65, CPI-linked)",
                    amount=benefit,
                    rationale=rationale,
                )
            )

        if p.disability.shortfall > 25_000:
            rationale = (
                f"If {name} became permanently disabled, {rand(p.disability.need)} would be needed to settle debt, adapt the home"
                " and vehicle, and replace retirement contributions that stop."
                f" Existing lump-sum disability cover is {rand(p.disability.provision)}."
            )
            out.append(
                Recommendation(
                    area="disability",
                    lifeAssured=key,
                    action="increase" if "disability-lump" in held else "new",
                    productType="Lump-sum disability (own/suited occupation)",
                    amount=_round_up(p.disability.shortfall, 50_000),
                    rationale=rationale,
                )
            )

        if p.severeIllness.shortfall > 25_000:
            rationale = (
                f"A severe illness benefit of about {js_str(assumptions.severeIllnessMonths)} months’ gross income"
                f" ({rand(p.severeIllness.need)}) funds treatment not covered by medical aid, recovery time and lifestyle adjustments."
                f" Existing cover is {rand(p.severeIllness.provision)}."
            )
            out.append(
                Recommendation(
                    area="severe-illness",
                    lifeAssured=key,
                    action="increase" if "severe-illness" in held else "new",
                    productType="Severe illness cover (comprehensive, severity-based)",
                    amount=_round_up(p.severeIllness.shortfall, 50_000),
                    rationale=rationale,
                )
            )

        if p.funeral.shortfall > 2_000:
            rationale = (
                "Bank accounts are frozen on death. Funeral cover pays within 48 hours to meet funeral costs"
                f" of about {rand(p.funeral.need)}."
            )
            out.append(
                Recommendation(
                    area="funeral",
                    lifeAssured=key,
                    action="increase" if "funeral" in held else "new",
                    productType="Funeral cover",
                    amount=_round_up(p.funeral.shortfall, 5_000),
                    rationale=rationale,
                )
            )

        ret = p.retirement
        if not ret.retired and ret.additionalMonthly > 100:
            monthly = _round_up(ret.additionalMonthly, 100)
            inc = p.income
            current = (inc.retirementPayrollMonthly + inc.retirementOwnMonthly + inc.retirementEmployerMonthly) * 12
            remuneration = inc.tax.remuneration
            headroom = max(
                0.0, retirement_deduction(current + monthly * 12, remuneration, table) - retirement_deduction(current, remuneration, table)
            )
            deductible = min(monthly * 12, headroom)
            after_tax = monthly - (deductible * inc.tax.marginalRate) / 12
            relief = (
                f"Contributions are deductible under s11F, reducing the after-tax cost to about {rand(after_tax)} p.m."
                if deductible > 0
                else "The s11F deduction limit is already used; consider a tax-free savings account."
            )
            rationale = (
                f"Projected retirement capital funds {js_str(js_round(ret.funded * 100))}% of the capital needed for"
                f" {rand(ret.targetMonthlyToday)} p.m. (today’s money) from age {js_str(ret.retirementAge)}."
                f" An additional {rand(monthly)} p.m., escalating at {to_fixed(assumptions.salaryEscalation * 100, 1)}% a year,"
                f" closes the gap. {relief}"
            )
            out.append(
                Recommendation(
                    area="retirement",
                    lifeAssured=key,
                    action="new",
                    productType="Retirement annuity (Regulation 28 compliant)",
                    amount=monthly,
                    premiumMonthly=monthly,
                    premiumEscalation=assumptions.salaryEscalation,
                    rationale=rationale,
                )
            )

        will = (doc.estate.get(key) or EstateProfile()).hasWill
        if will != "yes" or p.estate.estateDuty > 0:
            rationale = (
                f"{name} does not have a confirmed valid will. A will (with a testamentary trust and guardian nomination for minor"
                " children) ensures assets go to the intended heirs and avoids the Guardian’s Fund."
                if will != "yes"
                else f"Estate duty of {rand(p.estate.estateDuty)} is estimated."
                " Refer for estate structuring (spousal bequests, trusts, donations)."
            )
            out.append(
                Recommendation(
                    area="estate",
                    lifeAssured=key,
                    action="refer",
                    productType="Will drafting" if will != "yes" else "Estate plan review",
                    rationale=rationale,
                )
            )

    for e in a.education:
        if e.lumpSumRequired > 1_000:
            rationale = (
                f"{e.name} is already studying. About {rand(e.lumpSumRequired)} is needed to fund the remaining years of study"
                " and should be set aside in a low-risk, accessible investment."
            )
            out.append(
                Recommendation(
                    area="education",
                    lifeAssured="client",
                    action="new",
                    productType="Education funding from capital",
                    amount=_round_up(e.lumpSumRequired, 5_000),
                    rationale=rationale,
                )
            )
        if e.monthlyRequired > 100:
            saving = _round_up(e.monthlyRequired, 100)
            rationale = (
                f"{e.name}’s tertiary education will cost about {rand(e.capitalAtStart)} from"
                f" {js_str(now.year + js_round(e.yearsToTertiary))}. Saving {rand(saving)} p.m. (escalating with inflation) closes the"
                f" projected shortfall. A tax-free savings account (R{en_za(table.tfsa.annual)} a year limit) avoids tax on growth."
            )
            out.append(
                Recommendation(
                    area="education",
                    lifeAssured="client",
                    action="new",
                    productType="Education investment (TFSA or unit trust)",
                    amount=saving,
                    premiumMonthly=saving,
                    premiumEscalation=assumptions.cpi,
                    rationale=rationale,
                )
            )

    if a.emergency.shortfall > 5_000:
        rationale = (
            f"An emergency fund of {rand(a.emergency.need)} ({js_str(assumptions.emergencyMonths)} months of essential outgoings)"
            f" protects against job loss and income protection waiting periods. Build the {rand(a.emergency.shortfall)} shortfall"
            " over 12 months, or from the annual bonus."
        )
        out.append(
            Recommendation(
                area="emergency",
                lifeAssured="client",
                action="new",
                productType="Money market / notice account",
                amount=_round_up(a.emergency.shortfall, 5_000),
                premiumMonthly=_round_up(a.emergency.shortfall / 12, 100),
                premiumEscalation=0,
                rationale=rationale,
            )
        )

    expensive = [x for x in doc.liabilities if x.type in _UNSECURED and x.interestRate >= 0.18 and x.balance > 0]
    if expensive:
        total = 0.0
        for x in expensive:
            total += x.balance
        accounts = ", ".join(f"{x.description or x.type} at {to_fixed(x.interestRate * 100, 1)}%" for x in expensive)
        rationale = (
            f"Settle {rand(total)} of high-interest unsecured debt ({accounts}) — highest rate first — using the annual bonus"
            " or surplus. This is a guaranteed, tax-free return equal to the interest rate."
        )
        out.append(
            Recommendation(
                area="debt",
                lifeAssured="client",
                action="restructure",
                productType="Debt reduction plan",
                amount=total,
                rationale=rationale,
            )
        )

    return out
