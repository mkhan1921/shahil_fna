"""The FNA engine: a port of ``lib/fna/analysis.ts`` (§12.7). Pure functions, document in, analysis out.

Each function follows its TypeScript counterpart statement by statement, with the same order of floating
point operations, so the numbers agree to ~1e-12 and every note and finding reads the same; the parity
harness (:mod:`advisor_intel.fna_parity`) checks this on 2,000 generated documents. The one structural
difference: month-by-month projections step a year at a time (closed form within the year, see
:mod:`.finance`), falling back to months only in the year a living annuity runs out.

"Today" is injectable: a ``date`` is midnight UTC; a ``datetime`` is an exact instant, like a JavaScript
``Date`` in a process running in UTC (the TypeScript defaults to ``new Date()``).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from functools import reduce
from math import isfinite
from operator import add
from typing import NamedTuple

from ..models.fna import Dependant, EstateProfile, FnaDocument, Income, NeedArea, Owner, Person, PersonKey, RetirementGoal
from ._js import Now, date_ms, en_za, js_round, js_str, js_trim, parse_date_ms, parse_local_date, pw, rand, to_fixed
from .catalog import ASSET_BEHAVIOUR, EXPENSE_CATEGORIES, EXPENSE_ITEMS, PRE_RETIREMENT_FUNDS, UNSECURED_DEBT
from .estate import BOND_CANCELLATION, ESTATE_SUNDRIES, conveyancing_estimate, masters_fee
from .finance import _year_factors, fv, fv_escalating_contributions, monthly_rate, pv, pv_growing_monthly_income, required_escalating_saving
from .id_number import _age_on
from .results import (
    Analysis,
    Cashflow,
    CategoryAmount,
    CompletenessItem,
    EducationResult,
    EducationYear,
    EstateResult,
    Finding,
    GoalResult,
    GroupAmount,
    Line,
    NeedResult,
    NetWorth,
    PersonAnalysis,
    PersonIncome,
    Ratios,
    RetirementResult,
    RiskProfileResult,
    Status,
    line,
    retirement_year,
)
from .risk_profile import score_risk_profile
from .tables import TaxTable, TaxTableFile, table_for
from .tax import PersonTaxInput, _person_tax, estate_duty, incremental_tax, person_tax, retirement_lump_sum_tax

__all__ = [
    "FALLBACK_AGE",
    "LivingExpenses",
    "analyse",
    "compute_education",
    "compute_estate",
    "compute_income",
    "compute_retirement",
    "living_expenses",
    "person_name",
    "share_of",
]

FALLBACK_AGE = 40
_MS_PER_YEAR = 365.2425 * 24 * 3600 * 1000
_MS_PER_JULIAN_YEAR = 365.25 * 864e5
_PRE_RETIREMENT = frozenset(PRE_RETIREMENT_FUNDS)
_UNSECURED = frozenset(UNSECURED_DEBT)
_PROPERTY = frozenset(("primary-residence", "residential-property", "commercial-property"))
_NOT_EMPLOYED = frozenset(("self-employed", "retired", "unemployed", "student"))
_NOT_WORKING = frozenset(("retired", "unemployed", "student"))
_LIQUID = frozenset(k for k, v in ASSET_BEHAVIOUR.items() if v.liquid)
_CGT_EXEMPT = frozenset(k for k, v in ASSET_BEHAVIOUR.items() if v.cgtExempt)
_GROUP_LABELS = {
    "property": "Property",
    "lifestyle": "Vehicles & personal",
    "liquid": "Cash & money market",
    "investment": "Investments",
    "business": "Business interests",
    "retirement": "Retirement funds",
}
_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "positive": 3}

Today = date | datetime | Now | None

# --------------------------------------------------------------------------- helpers


def _sum(values: Iterable[float]) -> float:
    """``sum`` in finance.ts: left to right, skipping values that are not finite (not Python's compensated ``sum``)."""
    return reduce(add, filter(isfinite, values), 0.0)


def _other(key: PersonKey) -> PersonKey:
    return "spouse" if key == "client" else "client"


def _person(doc: FnaDocument, key: PersonKey) -> Person:
    return doc.client if key == "client" else doc.spouse


def _income(doc: FnaDocument, key: PersonKey) -> Income:
    return doc.income.get(key) or Income()


def _goal(doc: FnaDocument, key: PersonKey) -> RetirementGoal:
    return doc.retirement.get(key) or RetirementGoal()


def _estate(doc: FnaDocument, key: PersonKey) -> EstateProfile:
    return doc.estate.get(key) or EstateProfile()


def _age(date_of_birth: str, now: Now) -> int | None:
    """``ageOn(dateOfBirth, now)``."""
    dob = parse_local_date(date_of_birth)
    return None if dob is None else _age_on(dob, now.year, now.month, now.day)


def person_name(p: Person, fallback: str) -> str:
    return js_trim(f"{p.firstName} {p.surname}") or fallback


def share_of(owner: Owner, key: PersonKey, icop: bool) -> float:
    """Share of an owned item attributable to ``key`` (joint = 50%; in community of property everything is halved)."""
    if icop or owner == "joint":
        return 0.5
    return 1.0 if owner == key else 0.0


def _status_for(funded: float, need: float) -> Status:
    if need <= 0.5:
        return "na"
    if funded >= 0.95:
        return "covered"
    if funded >= 0.6:
        return "partial"
    return "shortfall"


def _lines(lines: Sequence[tuple]) -> list[Line]:
    """The lines ``makeNeed`` keeps: those of at least 50c either way."""
    return [line(*x) for x in lines if abs(x[1]) >= 0.5]


def _make_need(
    area: NeedArea,
    title: str,
    need_lines: Sequence[tuple],
    provision_lines: Sequence[tuple],
    notes: list[str] | None = None,
    person: PersonKey | None = None,
    monthly: bool = False,
) -> NeedResult:
    need = max(0.0, _sum([x[1] for x in need_lines]))
    provision = max(0.0, _sum([x[1] for x in provision_lines]))
    funded = min(1.0, provision / need) if need > 0 else 1.0
    return NeedResult.build(
        {
            "area": area,
            "person": person,
            "title": title,
            "need": need,
            "provision": provision,
            "shortfall": max(0.0, need - provision),
            "funded": funded,
            "status": _status_for(funded, need),
            "monthly": monthly,
            "needLines": _lines(need_lines),
            "provisionLines": _lines(provision_lines),
            "notes": notes if notes is not None else [],
        }
    )


def _is_child_dependent(d: Dependant, age: float) -> bool:
    return d.relationship == "child" and age < d.dependencyEndAge


# --------------------------------------------------------------------------- income and tax


def compute_income(doc: FnaDocument, key: PersonKey, table: TaxTable, today: Today = None) -> PersonIncome:
    """``computeIncome``: assessed tax (all deductions) and the payroll view (debit-order RA relief only arrives on assessment)."""
    now = Now.of(today)
    person = _person(doc, key)
    inc = _income(doc, key)
    dob = parse_local_date(person.dateOfBirth)
    known_age = None if dob is None else _age_on(dob, now.year, now.month, now.day)
    age = known_age if known_age is not None else FALLBACK_AGE
    # Rebates depend on age on the last day of the year of assessment.
    end = table.end_date
    tax_age = _age_on(dob, end.year, end.month, end.day) if dob is not None else age
    funds = [f for f in doc.retirementFunds if f.owner == key and f.type in _PRE_RETIREMENT]
    retirement_payroll = _sum([f.employeeMonthly for f in funds if f.viaPayroll])
    retirement_own = _sum([f.employeeMonthly for f in funds if not f.viaPayroll])
    retirement_employer = _sum([f.employerMonthly for f in funds])
    uif_applies = inc.uifApplies and person.employmentType not in _NOT_EMPLOYED

    gross, other, members = inc.grossMonthly, inc.otherTaxableMonthly, inc.medicalSchemeMembers
    contributions = (retirement_payroll + retirement_own + retirement_employer) * 12
    employer_annual = retirement_employer * 12
    tax = person_tax(PersonTaxInput(tax_age, gross, inc.annualBonus, other, contributions, employer_annual, members, uif_applies), table)
    tax_ex_bonus = _person_tax(PersonTaxInput(tax_age, gross, 0.0, other, contributions, employer_annual, members, uif_applies), table)
    payroll_contributions = (retirement_payroll + retirement_employer) * 12
    payroll = _person_tax(PersonTaxInput(tax_age, gross, 0.0, other, payroll_contributions, employer_annual, members, uif_applies), table)
    bonus_tax = max(0.0, tax.annualTax - tax_ex_bonus[7])

    override = inc.payeOverrideMonthly
    paye_monthly = override if override is not None else payroll[8]
    gross_monthly = gross + other
    take_home = (
        gross_monthly
        + inc.nonTaxableMonthly
        - paye_monthly
        - tax.monthlyUif
        - retirement_payroll
        - inc.medicalAidPayrollMonthly
        - inc.otherPayrollDeductionsMonthly
    )
    bonus_net = max(0.0, inc.annualBonus - bonus_tax)
    annual_tax = override * 12 + bonus_tax if override is not None else tax.annualTax
    after_tax_monthly = (tax.grossAnnual + inc.nonTaxableMonthly * 12 - annual_tax - tax.monthlyUif * 12) / 12

    # Earned income (salary/business income and bonus) is what income protection and illness cover insure.
    earned_annual = gross * 12 + inc.annualBonus
    avg_tax_rate = annual_tax / tax.grossAnnual if tax.grossAnnual > 0 else 0.0
    earned_after_tax_monthly = max(0.0, (earned_annual * (1 - avg_tax_rate) - tax.monthlyUif * 12) / 12)
    working = person.employmentType not in _NOT_WORKING and age < _goal(doc, key).retirementAge

    return PersonIncome.build(
        {
            "key": key,
            "name": person_name(person, "Client" if key == "client" else "Spouse"),
            "age": age,
            "taxAge": tax_age,
            "ageKnown": known_age is not None,
            "exactAge": (now.ms - date_ms(dob)) / _MS_PER_YEAR if dob is not None else age + 0.5,
            "tax": tax,
            "grossMonthly": gross_monthly,
            "grossMonthlyEquivalent": gross_monthly + inc.annualBonus / 12,
            "earnedGrossMonthly": earned_annual / 12,
            "earnedAfterTaxMonthly": earned_after_tax_monthly if working else 0.0,
            "working": working,
            "payeMonthly": paye_monthly,
            "uifMonthly": tax.monthlyUif,
            "retirementPayrollMonthly": retirement_payroll,
            "retirementEmployerMonthly": retirement_employer,
            "retirementOwnMonthly": retirement_own,
            "medicalPayrollMonthly": inc.medicalAidPayrollMonthly,
            "otherDeductionsMonthly": inc.otherPayrollDeductionsMonthly,
            "nonTaxableMonthly": inc.nonTaxableMonthly,
            "takeHomeMonthly": max(0.0, take_home),
            "afterTaxMonthly": max(0.0, after_tax_monthly),
            "bonusNet": bonus_net,
        }
    )


# --------------------------------------------------------------------------- household: cash flow, ratios and net worth


class LivingExpenses(NamedTuple):
    total: float
    essential: float
    byCategory: list[CategoryAmount]


def living_expenses(doc: FnaDocument) -> LivingExpenses:
    """Catalog items and custom expenses per month, in total, essential only and by category."""
    by_category: dict[str, float] = {}
    total = essential = 0.0
    items = doc.expenses.items
    for item in EXPENSE_ITEMS:
        amount = items.get(item.key, 0)
        if not amount:
            continue
        total += amount
        if item.essential:
            essential += amount
        by_category[item.category] = by_category.get(item.category, 0) + amount
    for c in doc.expenses.custom:
        if not c.amount:
            continue
        total += c.amount
        if c.essential:
            essential += c.amount
        by_category[c.category] = by_category.get(c.category, 0) + c.amount
    return LivingExpenses(
        total,
        essential,
        [CategoryAmount.build({"key": k, "label": label, "amount": by_category.get(k, 0)}) for k, label in EXPENSE_CATEGORIES],
    )


def _cashflow(doc: FnaDocument, incomes: list[PersonIncome], people: list[PersonKey]) -> tuple[Cashflow, LivingExpenses]:
    exp = living_expenses(doc)
    take_home = _sum([i.takeHomeMonthly for i in incomes])
    debt_repayments = _sum([x.monthlyRepayment for x in doc.liabilities])
    risk_premiums = _sum([p.premiumMonthly for p in doc.policies if p.lifeAssured in people and not p.isGroup])
    savings = (
        _sum([x.monthlyContribution for x in doc.assets])
        + _sum([i.retirementOwnMonthly for i in incomes])
        + _sum([d.educationSavingsMonthly for d in doc.dependants])
    )
    total_outgoings = exp.total + debt_repayments + risk_premiums + savings
    cashflow = Cashflow.build(
        {
            "takeHome": take_home,
            "livingExpenses": exp.total,
            "essentialExpenses": exp.essential,
            "byCategory": exp.byCategory,
            "debtRepayments": debt_repayments,
            "riskPremiums": risk_premiums,
            "savingsContributions": savings,
            "totalOutgoings": total_outgoings,
            "surplus": take_home - total_outgoings,
            "bonusNet": _sum([i.bonusNet for i in incomes]),
        }
    )
    return cashflow, exp


def _net_worth(doc: FnaDocument) -> NetWorth:
    groups: dict[str, float] = {}
    for x in doc.assets:
        g = ASSET_BEHAVIOUR[x.type].group
        groups[g] = groups.get(g, 0) + x.value
    # Guaranteed (life) annuities have no capital value to the household once purchased.
    retirement = _sum([f.value for f in doc.retirementFunds if f.type != "life-annuity"])
    if retirement:
        groups["retirement"] = retirement
    assets = _sum([x.value for x in doc.assets])
    liabilities = _sum([x.balance for x in doc.liabilities])
    by_group = []
    for key in ("property", "investment", "retirement", "liquid", "business", "lifestyle"):
        amount = groups.get(key, 0)
        if amount > 0:
            by_group.append(GroupAmount.build({"key": key, "label": _GROUP_LABELS[key], "amount": amount}))
    return NetWorth.build(
        {
            "assets": assets,
            "retirement": retirement,
            "liabilities": liabilities,
            "netWorth": assets + retirement - liabilities,
            "liquidAssets": _sum([x.value for x in doc.assets if x.type in _LIQUID]),
            "byGroup": by_group,
        }
    )


# --------------------------------------------------------------------------- education


def compute_education(doc: FnaDocument, today: Today = None) -> list[EducationResult]:
    """``computeEducation``: school fees (Grade R to matric) and tertiary costs per child; assets earmarked for
    education are shared across children in proportion to each child's tertiary need."""
    now = Now.of(today)
    a = doc.assumptions
    year = now.year
    infl1 = 1 + a.educationInflation
    ret = a.riskCapitalReturn
    pool = [x for x in doc.assets if x.earmark == "education"]
    pool_value = _sum([x.value for x in pool])
    pool_monthly = _sum([x.monthlyContribution for x in pool])

    base = []
    for d in doc.dependants:
        if d.relationship != "child":
            continue
        known = _age(d.dateOfBirth, now)
        age = known if known is not None else 0
        schedule = []
        # School fees: Grade R (the year the child turns 6) to matric (turns 18), paid in advance.
        school_pv = 0.0
        if d.schoolType != "none" and d.schoolFeesAnnual > 0:
            t = max(0, 6 - age)
            while age + t <= 18:
                cost = d.schoolFeesAnnual * pw(infl1, t)
                school_pv += pv(cost, ret, t)
                t += 1
        tertiary_pv = 0.0
        capital_at_start = 0.0
        years_to_tertiary = max(0, d.tertiaryStartAge - age)
        if d.tertiary and d.tertiaryCostAnnual > 0:
            already_studied = max(0, age - d.tertiaryStartAge)
            remaining = max(0, d.tertiaryYears - already_studied)
            k = 0
            while k < remaining:
                t = years_to_tertiary + k
                cost = d.tertiaryCostAnnual * pw(infl1, t)
                schedule.append(EducationYear.build({"year": year + t, "age": age + t, "cost": cost}))
                tertiary_pv += pv(cost, ret, t)
                capital_at_start += pv(cost, ret, k)
                k += 1
        base.append((d, age, schedule, school_pv, tertiary_pv, capital_at_start, years_to_tertiary))

    total_pv = _sum([b[4] for b in base])
    out = []
    for d, age, schedule, school_pv, tertiary_pv, capital_at_start, years_to_tertiary in base:
        share = tertiary_pv / total_pv if total_pv > 0 else 0
        savings = d.educationSavings + pool_value * share
        monthly = d.educationSavingsMonthly + pool_monthly * share
        projected = fv(savings, ret, years_to_tertiary) + fv_escalating_contributions(monthly, years_to_tertiary, ret, a.cpi)
        shortfall_at_start = max(0, capital_at_start - projected)
        shortfall_today = pv(shortfall_at_start, ret, years_to_tertiary)
        can_save = years_to_tertiary >= 1
        monthly_required = (
            required_escalating_saving(shortfall_at_start, years_to_tertiary, ret, a.cpi) if shortfall_at_start > 0 and can_save else 0
        )
        funded = min(1, projected / capital_at_start) if capital_at_start > 0 else 1
        out.append(
            EducationResult.build(
                {
                    "dependantId": d.id,
                    "name": d.name or "Child",
                    "age": age,
                    "yearsToTertiary": years_to_tertiary,
                    "tertiaryPV": tertiary_pv,
                    "schoolPV": school_pv,
                    "capitalAtStart": capital_at_start,
                    "projectedSavings": projected,
                    "shortfallAtStart": shortfall_at_start,
                    "shortfallToday": shortfall_today,
                    "monthlyRequired": monthly_required,
                    "lumpSumRequired": shortfall_at_start if shortfall_at_start > 0 and not can_save else 0,
                    "funded": funded,
                    "status": _status_for(funded, capital_at_start),
                    "schedule": schedule,
                }
            )
        )
    return out


# --------------------------------------------------------------------------- estate


def _own_net_estate(doc: FnaDocument, key: PersonKey, deceased: bool) -> float:
    """Net value (excl. retirement funds and policies) owned by ``key``, for accrual; for the deceased, debts
    settled by credit life on death are left out."""
    return _sum([x.value * share_of(x.owner, key, False) for x in doc.assets]) - _sum(
        [x.balance * share_of(x.owner, key, False) for x in doc.liabilities if not (deceased and x.creditLifeCover)]
    )


def compute_estate(doc: FnaDocument, key: PersonKey, income: PersonIncome, table: TaxTable, has_spouse: bool) -> EstateResult:
    """``computeEstate``: gross and net estate, costs, CGT on death, accrual, estate duty and liquidity."""
    a = doc.assumptions
    profile = _estate(doc, key)
    other = _other(key)
    hh = doc.household
    married = hh.maritalStatus == "married"
    icop = has_spouse and married and hh.maritalRegime == "icop"
    notes: list[str] = []

    assets = [(x, s) for x in doc.assets if (s := share_of(x.owner, key, icop)) > 0]
    liabilities = [(x, s) for x in doc.liabilities if (s := share_of(x.owner, key, icop)) > 0]
    policies = [p for p in doc.policies if p.lifeAssured == key and p.type == "life"]

    asset_value = _sum([x.value * s for x, s in assets])
    policies_to_estate = _sum([p.cover for p in policies if p.beneficiary == "estate"])
    deemed_policies = [p for p in policies if p.beneficiary != "estate" and p.beneficiary != "business"]
    deemed_property = _sum([p.cover for p in deemed_policies])

    # Accrual claim (Matrimonial Property Act s3–s4) for ANC with accrual.
    accrual_payable = 0.0
    accrual_receivable = 0.0
    if has_spouse and married and hh.maritalRegime == "anc-accrual":
        mine = max(0, _own_net_estate(doc, key, True) - profile.accrualCommencementValue)
        theirs = max(0, _own_net_estate(doc, other, False) - _estate(doc, other).accrualCommencementValue)
        if mine > theirs:
            accrual_payable = (mine - theirs) / 2
        else:
            accrual_receivable = (theirs - mine) / 2
        notes.append("Accrual claim estimated from current net asset values less commencement values in the antenuptial contract.")
    if icop:
        notes.append(
            "Married in community of property: half of the joint estate is attributed to the deceased, but the executor administers"
            " — and charges fees on — the whole joint estate."
        )

    gross_estate = asset_value + policies_to_estate + accrual_receivable
    unsecured = [(x, s) for x, s in liabilities if not x.creditLifeCover]
    # All debts reduce the dutiable estate; only those to be settled need cash (others are taken over by heirs).
    debts_all = _sum([x.balance * s for x, s in unsecured])
    debts = _sum([x.balance * s for x, s in unsecured if x.settleOnDeath])
    if debts_all > debts:
        notes.append("Some debts are assumed to be taken over by heirs rather than settled; lenders must consent to this.")

    fee_base = _sum([x.value for x in doc.assets]) + policies_to_estate if icop else gross_estate
    executor_fees = fee_base * a.executorFeeRate * (1 + a.vatRate)
    masters = masters_fee(fee_base)
    # Transfer to the surviving spouse still requires conveyancing; ICOP halves may pass by endorsement.
    conveyancing = _sum([conveyancing_estimate(x.value * s, a.vatRate) for x, s in assets if x.type in _PROPERTY])
    sundries = ESTATE_SUNDRIES if gross_estate > 0 else 0
    bond_cancellation = sum(1 for x, _ in liabilities if x.type == "home-loan" and x.balance > 0 and x.settleOnDeath) * BOND_CANCELLATION
    funeral = profile.funeralCost

    # Capital gains tax on the deemed disposal at death (s9HA / para 40 of the Eighth Schedule).
    gains = 0.0
    cgt = table.cgt
    for x, s in assets:
        if x.type in _CGT_EXEMPT:
            continue
        if has_spouse and x.bequeathToSpouse:
            continue  # roll-over to the surviving spouse (s25(4))
        if x.baseCost <= 0:
            continue
        gain = (x.value - x.baseCost) * s
        if x.type == "primary-residence":
            gain -= cgt.primaryResidence * s
        gains += max(0, gain)
    net_gain = max(0, gains - cgt.deathExclusion)
    taxable_gain = net_gain * cgt.inclusionRate
    cgt_due = incremental_tax(income.tax.taxable, taxable_gain, income.taxAge, table)
    if any(x.type not in _CGT_EXEMPT and x.baseCost <= 0 and not (has_spouse and x.bequeathToSpouse) for x, _ in assets):
        notes.append("Some assets have no base cost captured, so CGT on death may be understated.")

    costs = executor_fees + masters + conveyancing + bond_cancellation + sundries + funeral
    net_estate = max(0, gross_estate + deemed_property - debts_all - costs - cgt_due - accrual_payable)

    # s4(q): property accruing to the surviving spouse.
    to_spouse = (
        _sum([x.value * s for x, s in assets if x.bequeathToSpouse]) + _sum([p.cover for p in deemed_policies if p.beneficiary == "spouse"])
        if has_spouse
        else 0
    )
    spouse_deduction = min(net_estate, to_spouse)
    # s4A: the surviving spouse can use the predeceased spouse's unused abatement (at most twice the abatement).
    duty_table = table.estateDuty
    abatement = duty_table.abatement + min(max(0, profile.portedAbatement), duty_table.abatement)
    dutiable = max(0, net_estate - spouse_deduction - abatement)
    duty = estate_duty(dutiable, table)

    cash_available = _sum([x.value * s for x, s in assets if x.type in _LIQUID]) + policies_to_estate
    cash_required = debts + costs + cgt_due + duty + profile.cashBequests + accrual_payable
    liquidity_shortfall = max(0, cash_required - cash_available)

    if not has_spouse and to_spouse == 0 and married:
        notes.append("Spouse not included in the analysis — the s4(q) spousal deduction and CGT roll-over were not applied.")

    return EstateResult.build(
        {
            "person": key,
            "grossEstate": gross_estate,
            "deemedProperty": deemed_property,
            "liabilities": debts,
            "executorFees": executor_fees,
            "mastersFees": masters,
            "conveyancing": conveyancing,
            "bondCancellation": bond_cancellation,
            "sundries": sundries,
            "funeral": funeral,
            "cgt": cgt_due,
            "capitalGains": gains,
            "accrualPayable": accrual_payable,
            "accrualReceivable": accrual_receivable,
            "netEstate": net_estate,
            "spouseDeduction": spouse_deduction,
            "abatement": abatement,
            "dutiable": dutiable,
            "estateDuty": duty,
            "totalCosts": costs + cgt_due + duty,
            "cashRequired": cash_required,
            "cashAvailable": cash_available,
            "liquidityShortfall": liquidity_shortfall,
            "lines": [
                line(label, amount)
                for label, amount in (
                    ("Debts payable by the estate", debts),
                    (f"Executor's fees ({to_fixed(a.executorFeeRate * 100, 1)}% + VAT)", executor_fees),
                    ("Master's fees", masters),
                    ("Conveyancing (transfer to heirs)", conveyancing),
                    ("Bond cancellation", bond_cancellation),
                    ("Advertising, tax returns & sundries", sundries),
                    ("Funeral costs", funeral),
                    ("Capital gains tax on death", cgt_due),
                    ("Estate duty", duty),
                    ("Accrual claim payable to spouse", accrual_payable),
                    ("Cash bequests", profile.cashBequests),
                )
            ],
            "notes": notes,
        }
    )


# --------------------------------------------------------------------------- death (life cover)


def _death_income_term(doc: FnaDocument, key: PersonKey, has_spouse: bool, spouse_income: PersonIncome | None, now: Now) -> float:
    a = doc.assumptions
    if a.deathIncomeTerm == "fixed":
        return a.deathIncomeFixedYears
    child_years = 0
    for d in doc.dependants:
        if d.relationship != "child":
            continue
        known = _age(d.dateOfBirth, now)
        age = known if known is not None else 0
        child_years = max(child_years, d.dependencyEndAge - age if _is_child_dependent(d, age) else 0)
    spouse_years = max(0, _goal(doc, _other(key)).retirementAge - spouse_income.age) if has_spouse and spouse_income is not None else 0
    if a.deathIncomeTerm == "youngest-independent":
        return child_years
    if a.deathIncomeTerm == "spouse-retirement":
        return spouse_years
    # auto: until the youngest child is independent; a non-earning spouse to retirement age.
    # Support for parents and other dependants is capitalised separately.
    spouse_dependent = spouse_income.afterTaxMonthly < 1 if has_spouse and spouse_income is not None else False
    return max(child_years, spouse_years if spouse_dependent else 0)


def _death(
    doc: FnaDocument,
    key: PersonKey,
    income: PersonIncome,
    estate: EstateResult,
    education: list[EducationResult],
    table: TaxTable,
    has_spouse: bool,
    spouse_income: PersonIncome | None,
    now: Now,
) -> tuple[NeedResult, float, float]:
    a = doc.assumptions
    profile = _estate(doc, key)
    hh = doc.household
    icop = has_spouse and hh.maritalStatus == "married" and hh.maritalRegime == "icop"
    notes: list[str] = []
    ret, cpi = a.riskCapitalReturn, a.cpi

    years = _death_income_term(doc, key, has_spouse, spouse_income, now)
    marginal = income.tax.marginalRate
    continuing_income = _sum(
        [x.monthlyIncome * share_of(x.owner, key, icop) * (1 - marginal) for x in doc.assets if x.monthlyIncome > 0 and x.bequeathToSpouse]
    )
    income_to_replace = max(0, income.afterTaxMonthly - continuing_income)
    replacement_monthly = income_to_replace * a.deathIncomeReplacement if years > 0 else 0
    extra_years = years if years > 0 else a.deathIncomeFixedYears
    extra_monthly = profile.additionalIncomeNeedMonthly
    monthly_need = replacement_monthly + (extra_monthly if extra_monthly > 0 and extra_years > 0 else 0)
    income_capital = pv_growing_monthly_income(replacement_monthly, years, ret, cpi) + pv_growing_monthly_income(
        extra_monthly, extra_years, ret, cpi
    )

    # Parents and other supported dependants: the actual support amount for the stated period.
    supported = [d for d in doc.dependants if d.relationship != "child" and d.monthlySupport > 0 and d.supportYears > 0]
    support_capital = _sum([pv_growing_monthly_income(d.monthlySupport, d.supportYears, ret, cpi) for d in supported])

    education_capital = max(0, _sum([e.tertiaryPV for e in education]) - _sum([d.educationSavings for d in doc.dependants]))
    has_dependants = has_spouse or len(supported) > 0
    if not has_dependants:
        for d in doc.dependants:
            if d.relationship == "child":
                known = _age(d.dateOfBirth, now)
                if _is_child_dependent(d, known if known is not None else 0):
                    has_dependants = True
                    break
    adjustment_fund = income.afterTaxMonthly * a.emergencyMonths if has_dependants else 0

    policies = [p for p in doc.policies if p.lifeAssured == key]
    life_cover = _sum([p.cover for p in policies if p.type == "life" and p.beneficiary != "business"])
    funeral_cover = min(estate.funeral, _sum([p.cover for p in policies if p.type == "funeral"]))
    family_income = _sum(
        [
            pv_growing_monthly_income(p.monthlyBenefit, p.benefitTermYears or years, ret, p.coverEscalation)
            for p in policies
            if p.type == "family-income"
        ]
    )
    lumpable = [f for f in doc.retirementFunds if f.owner == key and f.type != "life-annuity"]
    fund_total = _sum([f.value for f in lumpable])
    retirement = max(0, fund_total - retirement_lump_sum_tax(fund_total, table))
    liquid = _sum([x.value * share_of(x.owner, key, icop) for x in doc.assets if x.type in _LIQUID])

    if years == 0 and income.afterTaxMonthly > 0:
        notes.append("No dependent children or non-earning spouse were identified, so no family income replacement is included.")
    if any(d.specialNeeds for d in doc.dependants):
        notes.append(
            "A dependant has special needs and may require lifelong support — consider a special trust and extending the income term."
        )
    notes.append(
        f"Income capital: {js_str(js_round(a.deathIncomeReplacement * 100))}% of after-tax income for {js_str(years)} years,"
        f" escalating at CPI and discounted at {to_fixed(ret * 100, 1)}% p.a."
    )
    notes.append(
        "Retirement fund death benefits are allocated by the fund trustees under s37C of the Pension Funds Act (nominations are not"
        " binding) and can take up to 12 months; they are shown net of lump-sum tax and do not provide estate liquidity."
    )

    need = _make_need(
        "life",
        "Life cover",
        [
            ("Settle debts", estate.liabilities),
            ("Estate costs, CGT & estate duty", estate.totalCosts - estate.funeral),
            ("Funeral", estate.funeral),
            ("Cash bequests", profile.cashBequests),
            (f"Adjustment fund ({js_str(a.emergencyMonths)} months' income)", adjustment_fund),
            ("Tertiary education capital", education_capital),
            (f"Income for dependants ({js_str(years)} yrs)", income_capital, f"{js_str(js_round(monthly_need))} p.m. today"),
            ("Support for parents & other dependants", support_capital),
            ("Other capital needs", profile.additionalCapitalNeed),
        ],
        [
            ("Existing life cover", life_cover),
            ("Funeral cover", funeral_cover),
            ("Family income benefits (PV)", family_income),
            ("Retirement fund death benefits (net of tax)", retirement),
            ("Liquid assets & investments", liquid),
        ],
        notes,
        key,
    )
    return need, years, monthly_need


# --------------------------------------------------------------------------- disability, income protection, severe illness, funeral


def _disability(
    doc: FnaDocument, key: PersonKey, income: PersonIncome, has_spouse: bool
) -> tuple[NeedResult, NeedResult, NeedResult, NeedResult]:
    a = doc.assumptions
    hh = doc.household
    icop = has_spouse and hh.maritalStatus == "married" and hh.maritalRegime == "icop"
    retirement_age = _goal(doc, key).retirementAge
    years_to_retirement = max(0, retirement_age - income.exactAge)
    policies = [p for p in doc.policies if p.lifeAssured == key and p.beneficiary != "business"]

    ip_policies = [p for p in policies if p.type == "income-protection"]
    permanent_from = min(60, retirement_age)
    ip_permanent = _sum([p.monthlyBenefit for p in ip_policies if p.benefitToAge >= permanent_from])
    ip_temporary = _sum([p.monthlyBenefit for p in ip_policies if not p.benefitToAge >= permanent_from])
    # Only earned income (salary / business income and bonus) is insurable; passive income continues on disability.
    earned = income.earnedAfterTaxMonthly
    ip_target = earned * a.incomeProtectionTarget
    ip_notes = (
        [
            (
                f"Target: {js_str(js_round(a.incomeProtectionTarget * 100))}% of after-tax earned income. Insurers generally limit total"
                " income protection to about 100% of after-tax income."
            )
        ]
        if income.working
        else ["Not economically active or past retirement age — income protection does not apply."]
    )
    if any(p.isGroup for p in ip_policies):
        ip_notes.append(
            "Group income protection may be taxable if the employer owns the policy — confirm the tax treatment with the scheme."
        )
    if ip_permanent + ip_temporary > earned * 1.05 and earned > 0:
        ip_notes.append("Existing income protection exceeds after-tax income — benefits may be reduced at claim stage (over-insurance).")
    if ip_temporary > 0:
        ip_notes.append(f"Temporary (short-term) benefits of {js_str(js_round(ip_temporary))} p.m. only pay for a limited period.")
    income_protection = _make_need(
        "income-protection",
        "Income protection",
        [("Monthly income to protect", ip_target)],
        [("Income protection to retirement", ip_permanent)],
        ip_notes,
        key,
        True,
    )

    debts = _sum([x.balance * share_of(x.owner, key, icop) for x in doc.liabilities if x.settleOnDisability and not x.creditLifeCover])
    # The member's own contributions can continue from an income protection benefit (it replaces after-tax
    # income before payroll deductions); the employer's contributions stop.
    retirement_capital = (
        pv_growing_monthly_income(income.retirementEmployerMonthly, years_to_retirement, a.preRetirementReturn, a.salaryEscalation)
        if a.disabilityIncludeRetirement and income.working
        else 0
    )
    income_gap = max(0, ip_target - ip_permanent)
    income_gap_capital = pv_growing_monthly_income(income_gap, years_to_retirement, a.riskCapitalReturn, a.cpi)
    lump_cover = _sum([p.cover for p in policies if p.type == "disability-lump"])

    dis_notes = (
        [
            (
                "Income replacement is addressed through income protection; the lump sum covers debt, lifestyle adaptations and lost"
                " employer retirement funding."
            )
        ]
        if income.working
        else ["Not economically active — lump-sum disability cover is generally not available or needed."]
    )
    if income_gap > 0:
        dis_notes.append(
            f"If income protection is not taken up, a further {en_za(js_round(income_gap_capital))} lump sum would be needed to replace"
            f" the income gap to age {js_str(retirement_age)}."
        )
    if any(p.type == "disability-lump" and p.definition == "any-occupation" for p in policies):
        dis_notes.append('Some disability cover uses an "any occupation" definition, which is harder to claim on.')

    disability = _make_need(
        "disability",
        "Disability — lump sum",
        [
            ("Settle debts", debts),
            ("Home, vehicle & medical adaptations", a.disabilityAdjustments),
            (f"Lost employer retirement contributions (to {js_str(retirement_age)})", retirement_capital),
        ]
        if income.working
        else [],
        [("Existing lump-sum disability cover", lump_cover)],
        dis_notes,
        key,
    )

    si_cover = _sum([p.cover for p in policies if p.type == "severe-illness"])
    accelerated = any(p.type == "severe-illness" and p.accelerated for p in policies)
    severe_illness = _make_need(
        "severe-illness",
        "Severe illness",
        [(f"{js_str(a.severeIllnessMonths)} months of gross income", income.earnedGrossMonthly * a.severeIllnessMonths)]
        if income.working
        else [],
        [("Existing severe illness cover", si_cover)],
        ["Some severe illness cover is accelerated — a claim reduces the life cover by the same amount."] if accelerated else [],
        key,
    )

    funeral_cost = _estate(doc, key).funeralCost
    funeral_cover = _sum([p.cover for p in policies if p.type == "funeral"])
    quick_life = _sum(
        [p.cover for p in policies if p.type == "life" and not p.isGroup and p.beneficiary not in ("estate", "business", "trust")]
    )
    funeral = _make_need(
        "funeral",
        "Funeral",
        [("Funeral costs", funeral_cost)],
        [
            ("Funeral cover", funeral_cover),
            ("Life cover paying a nominee within days", min(quick_life, max(0, funeral_cost - funeral_cover))),
        ],
        [
            (
                "Bank accounts are frozen on death. Funeral policies and life cover with a nominated beneficiary pay quickly; group cover"
                " and estate proceeds can take weeks to months."
            )
        ],
        key,
    )
    return income_protection, disability, severe_illness, funeral


# --------------------------------------------------------------------------- retirement


def compute_retirement(
    doc: FnaDocument, key: PersonKey, income: PersonIncome, today: Today = None, *, table: TaxTableFile | None = None
) -> RetirementResult:
    """``computeRetirement``: capital projected to retirement, the capital the target income needs to the planning
    age, the extra saving that closes the gap, and the drawdown tested against the living annuity band."""
    now = Now.of(today)
    a = doc.assumptions
    goal = _goal(doc, key)
    notes: list[str] = []
    current_age = income.exactAge
    retired = current_age >= goal.retirementAge or _person(doc, key).employmentType == "retired"
    # Someone already retired draws income from today, whatever retirement age was captured.
    ret_start_age = current_age if retired else goal.retirementAge
    retirement_age = math.floor(current_age) if retired else goal.retirementAge
    planning_age = max(goal.planningAge, math.ceil(ret_start_age) + 1)
    years_to_retirement = 0 if retired else max(0, goal.retirementAge - current_age)
    years_in_retirement = max(1, planning_age - ret_start_age)

    pre_funds = [f for f in doc.retirementFunds if f.owner == key and f.type in _PRE_RETIREMENT]
    post_funds = [f for f in doc.retirementFunds if f.owner == key and f.type == "living-annuity"]
    earmarked = [x for x in doc.assets if x.earmark == "retirement" and (x.owner == key or x.owner == "joint")]
    earmarked_value = _sum([x.value * (0.5 if x.owner == "joint" else 1) for x in earmarked])
    earmarked_monthly = _sum([x.monthlyContribution * (0.5 if x.owner == "joint" else 1) for x in earmarked])
    current_capital = _sum([f.value for f in pre_funds]) + _sum([f.value for f in post_funds]) + earmarked_value
    monthly_contributions = 0 if retired else _sum([f.employeeMonthly + f.employerMonthly for f in pre_funds]) + earmarked_monthly

    target_monthly_today = goal.targetMonthly if goal.targetMode == "amount" else income.grossMonthlyEquivalent * goal.targetPercent
    other_income_today = goal.otherIncomeMonthly
    net_target_today = max(0, target_monthly_today - other_income_today)
    cpi1 = 1 + a.cpi
    inflator = pw(cpi1, years_to_retirement)
    target_at_retirement = net_target_today * inflator

    # Accumulation: monthly compounding with annual escalation of contributions, a year per step.
    start_year = now.year
    capital = current_capital
    contribution = monthly_contributions
    timeline = [retirement_year(math.floor(current_age), start_year, capital, capital, 0, 0, "retirement" if retired else "accumulation")]
    # Math.pow(1 + cpi, t) for the real values; ** is the same C pow, and safe while the base is positive.
    deflator = (lambda t: cpi1**t) if cpi1 > 0 else (lambda t: pw(cpi1, t))
    months_to_retirement = int(js_round(years_to_retirement * 12))
    g = 1 + monthly_rate(a.preRetirementReturn)
    full, rest = divmod(months_to_retirement, 12)
    if full:
        g12, s12 = _year_factors(g, 12)
        for t in range(1, full + 1):
            capital = capital * g12 + contribution * s12
            timeline.append(
                retirement_year(
                    math.floor(current_age + t), start_year + t, capital, capital / deflator(t), contribution * 12, 0, "accumulation"
                )
            )
            contribution *= 1 + a.salaryEscalation
    if rest:
        gr, sr = _year_factors(g, rest)
        capital = capital * gr + contribution * sr
        t = months_to_retirement / 12
        timeline.append(
            retirement_year(
                math.floor(current_age + t), start_year + math.ceil(t), capital, capital / deflator(t), contribution * 12, 0, "accumulation"
            )
        )
    projected = capital
    projected_real = projected / inflator

    # Capital required to fund the (net) target income to the planning age.
    post = a.postRetirementReturn
    required = pv_growing_monthly_income(target_at_retirement, years_in_retirement, post, a.cpi)
    shortfall = max(0, required - projected)
    annuity_factor_at_ret = (
        required / target_at_retirement
        if required > 0 and target_at_retirement > 0
        else pv_growing_monthly_income(1, years_in_retirement, post, a.cpi)
    )
    sustainable_at_ret = projected / annuity_factor_at_ret if annuity_factor_at_ret > 0 else 0
    sustainable_today = sustainable_at_ret / inflator + other_income_today
    additional = (
        required_escalating_saving(shortfall, years_to_retirement, a.preRetirementReturn, a.salaryEscalation)
        if shortfall > 0 and years_to_retirement >= 1 / 12
        else 0
    )

    # Drawdown: withdraw the (inflating) target income monthly in advance; a year per step until the capital runs out.
    withdrawal = target_at_retirement
    depletion_age = None
    months_in_retirement = int(js_round(years_in_retirement * 12))
    g = 1 + monthly_rate(post)
    g12, s12 = _year_factors(g, 12)
    for start in range(0, months_in_retirement, 12):
        n = min(12, months_in_retirement - start)
        if depletion_age is None:
            gn, sn = (g12, s12) if n == 12 else _year_factors(g, n)
            first = (capital - withdrawal) * g
            last = capital * gn - withdrawal * g * sn  # within a year the balance moves monotonically
            if first > 0 and last > 0:
                capital = last
            else:
                for j in range(1, n + 1):
                    capital = (capital - withdrawal) * g
                    if capital <= 0 and depletion_age is None:
                        depletion_age = ret_start_age + (start + j) / 12
                        capital = 0.0
        if n == 12:
            m = start + 12
            t = years_to_retirement + m / 12
            rounded = math.floor(t)  # Math.round for t >= 0
            if t - rounded >= 0.5:
                rounded += 1
            shown = max(0.0, capital) if depletion_age is None else 0.0  # after running out the balance stays at or below zero
            timeline.append(
                retirement_year(
                    math.floor(ret_start_age + m / 12), start_year + rounded, shown, shown / deflator(t), 0, withdrawal * 12, "retirement"
                )
            )
            withdrawal *= cpi1

    initial_drawdown = (target_at_retirement * 12) / projected if projected > 0 else 0
    max_drawdown = (table if table is not None else table_for(a.taxYear)).livingAnnuity.maxDrawdown
    if initial_drawdown > max_drawdown:
        notes.append(
            f"The required income exceeds the {js_str(js_round(max_drawdown * 1000) / 10)}% maximum living annuity drawdown"
            " — the target is not achievable from this capital."
        )
    elif initial_drawdown > 0.05 and target_at_retirement > 0:
        notes.append(
            f"An initial drawdown of {to_fixed(initial_drawdown * 100, 1)}% exceeds the ~4–5% generally regarded as sustainable"
            " for a living annuity."
        )
    if math.ceil(years_to_retirement) > 0 and monthly_contributions == 0:
        notes.append("No ongoing retirement contributions are recorded.")
    notes.append(
        f"Projection: {to_fixed(a.preRetirementReturn * 100, 1)}% p.a. before and {to_fixed(post * 100, 1)}% p.a. after retirement,"
        f" CPI {to_fixed(a.cpi * 100, 1)}%, contributions escalating at {to_fixed(a.salaryEscalation * 100, 1)}% p.a."
        " Returns are not guaranteed."
    )

    funded = min(1, projected / required) if required > 0 else 1
    current_gross = income.grossMonthlyEquivalent
    return RetirementResult.build(
        {
            "person": key,
            "retired": retired,
            "currentAge": current_age,
            "retirementAge": retirement_age,
            "planningAge": planning_age,
            "yearsToRetirement": years_to_retirement,
            "yearsInRetirement": years_in_retirement,
            "currentCapital": current_capital,
            "monthlyContributions": monthly_contributions,
            "projectedCapital": projected,
            "projectedCapitalReal": projected_real,
            "targetMonthlyToday": target_monthly_today,
            "otherIncomeToday": other_income_today,
            "targetMonthlyAtRetirement": target_at_retirement,
            "requiredCapital": required,
            "requiredCapitalReal": required / inflator,
            "shortfall": shortfall,
            "shortfallReal": shortfall / inflator,
            "additionalMonthly": additional,
            "sustainableMonthlyToday": sustainable_today,
            "replacementRatio": sustainable_today / current_gross if current_gross > 0 else 0,
            "targetReplacementRatio": target_monthly_today / current_gross if current_gross > 0 else 0,
            "initialDrawdown": initial_drawdown,
            "depletionAge": depletion_age,
            "funded": funded,
            "status": _status_for(funded, required),
            "timeline": timeline,
            "notes": notes,
        }
    )


# --------------------------------------------------------------------------- goals and emergency fund


def _goals(doc: FnaDocument, now: Now) -> list[GoalResult]:
    a = doc.assumptions
    ret = a.riskCapitalReturn
    pool = [x for x in doc.assets if x.earmark == "goal"]
    pool_value = _sum([x.value for x in pool])
    pool_monthly = _sum([x.monthlyContribution for x in pool])
    total_cost = _sum([g.cost for g in doc.goals])
    out = []
    for g in doc.goals:
        years = max(0, g.targetYear - now.year)
        future_cost = g.cost * pw(1 + a.cpi, years)
        share = g.cost / total_cost if total_cost > 0 else 0
        projected = fv(g.existingSavings + pool_value * share, ret, years) + fv_escalating_contributions(
            g.monthlyContribution + pool_monthly * share, years, ret, 0
        )
        shortfall = max(0, future_cost - projected)
        monthly_required = required_escalating_saving(shortfall, years, ret, 0) if shortfall > 0 and years > 0 else 0
        funded = min(1, projected / future_cost) if future_cost > 0 else 1
        out.append(
            GoalResult.build(
                {
                    "id": g.id,
                    "name": g.name or "Goal",
                    "years": years,
                    "futureCost": future_cost,
                    "projected": projected,
                    "shortfall": shortfall,
                    "monthlyRequired": monthly_required,
                    "funded": funded,
                    "status": _status_for(funded, future_cost),
                }
            )
        )
    return out


def _emergency(doc: FnaDocument, cashflow: Cashflow) -> NeedResult:
    a = doc.assumptions
    essential_monthly = cashflow.essentialExpenses + cashflow.debtRepayments + cashflow.riskPremiums
    earmarked = [x for x in doc.assets if x.earmark == "emergency"]
    pool = earmarked or [x for x in doc.assets if x.type == "cash" or x.type == "money-market"]
    available = _sum([x.value for x in pool])
    variable = doc.client.employmentType != "salaried" or (
        doc.household.includeSpouse and doc.spouse.employmentType not in ("salaried", "retired", "unemployed")
    )
    months = js_str(a.emergencyMonths)
    notes = [
        f"Target: {months} months of essential expenses, debt repayments and risk premiums ({en_za(js_round(essential_monthly))} p.m.)."
    ]
    if variable and a.emergencyMonths < 6:
        notes.append("Variable or self-employed income: consider a 6-month reserve.")
    if not earmarked:
        notes.append("No assets are earmarked as an emergency fund, so all cash and money-market balances were used.")
    return _make_need(
        "emergency",
        "Emergency fund",
        [(f"{months} months of essential outgoings", essential_monthly * a.emergencyMonths)],
        [("Earmarked emergency savings" if earmarked else "Cash & money market", available)],
        notes,
    )


# --------------------------------------------------------------------------- findings and completeness


def _finding(severity: str, area: str, title: str, detail: str, person: PersonKey | None = None) -> dict:
    f = {"severity": severity, "area": area, "title": title, "detail": detail}
    if person is not None:
        f["person"] = person
    return f


def _findings(
    doc: FnaDocument,
    people: list[PersonKey],
    persons: dict[PersonKey, PersonAnalysis],
    cashflow: Cashflow,
    ratios: Ratios,
    emergency: NeedResult,
    education: list[EducationResult],
    risk: RiskProfileResult,
    now: Now,
) -> list[Finding]:
    f: list[dict] = []
    has_minors = False
    for d in doc.dependants:
        if d.relationship == "child":
            known = _age(d.dateOfBirth, now)
            if (known if known is not None else 0) < 18:
                has_minors = True
                break

    for key in people:
        p = persons.get(key)
        if p is None:
            continue
        who = p.name
        if not p.income.ageKnown:
            f.append(
                _finding(
                    "critical",
                    "data",
                    f"Date of birth missing for {who}",
                    f"Calculations assume age {FALLBACK_AGE} until a date of birth or ID number is captured.",
                    key,
                )
            )
        if p.death.status in ("shortfall", "partial"):
            f.append(
                _finding(
                    "critical" if p.death.status == "shortfall" else "warning",
                    "life",
                    f"Life cover shortfall of {rand(p.death.shortfall)} on {who}",
                    f"Existing provision meets {js_str(js_round(p.death.funded * 100))}% of the capital your family would need.",
                    key,
                )
            )
        ip = p.incomeProtection
        if ip.need > 0 and ip.funded < 0.95:
            f.append(
                _finding(
                    "critical" if ip.provision == 0 else "warning",
                    "income-protection",
                    f"Income protection gap of {rand(ip.shortfall)} p.m. for {who}",
                    "Loss of income through illness or injury is the largest financial risk for most working people.",
                    key,
                )
            )
        if p.disability.status in ("shortfall", "partial"):
            f.append(
                _finding(
                    "warning",
                    "disability",
                    f"Disability lump-sum shortfall of {rand(p.disability.shortfall)} for {who}",
                    "Covers debt settlement, adaptations and lost retirement contributions if permanently disabled.",
                    key,
                )
            )
        if p.severeIllness.status == "shortfall":
            f.append(
                _finding(
                    "warning",
                    "severe-illness",
                    f"Severe illness cover of {rand(p.severeIllness.shortfall)} recommended for {who}",
                    "Provides a lump sum on diagnosis of cancer, heart attack, stroke and similar events.",
                    key,
                )
            )
        estate = p.estate
        if estate.liquidityShortfall > 0:
            to_spouse = _sum([x.cover for x in doc.policies if x.lifeAssured == key and x.type == "life" and x.beneficiary == "spouse"])
            spouse_can_fund = len(people) > 1 and to_spouse >= estate.liquidityShortfall
            f.append(
                _finding(
                    "warning" if spouse_can_fund else "critical",
                    "estate",
                    f"Estate liquidity shortfall of {rand(estate.liquidityShortfall)} for {who}",
                    "The executor cannot access policies paid to a nominee. The surviving spouse could contribute from policy proceeds,"
                    " but cover payable to the estate (or a spouse undertaking) avoids forced sales."
                    if spouse_can_fund
                    else "The executor may have to sell assets (such as the family home) to pay debts, costs and taxes.",
                    key,
                )
            )
        if estate.estateDuty > 0:
            f.append(
                _finding(
                    "info",
                    "estate",
                    f"Estate duty of {rand(estate.estateDuty)} payable on {who}'s estate",
                    "Consider estate structuring (spousal bequests, trusts, loan accounts, donations) with a fiduciary specialist.",
                    key,
                )
            )
        profile = _estate(doc, key)
        if profile.hasWill != "yes":
            f.append(
                _finding(
                    "critical",
                    "estate",
                    f"{who} has no confirmed valid will",
                    "Without a will the estate devolves under the Intestate Succession Act, which may not reflect your wishes.",
                    key,
                )
            )
        elif profile.willDate:
            age = (now.ms - parse_date_ms(profile.willDate)) / _MS_PER_JULIAN_YEAR
            if age > 5:
                f.append(
                    _finding(
                        "warning",
                        "estate",
                        f"{who}'s will is {js_str(math.floor(age))} years old",
                        "Wills should be reviewed every 3–5 years and after marriage, divorce or births.",
                        key,
                    )
                )
        if has_minors and profile.guardianNominated != "yes":
            f.append(
                _finding(
                    "warning",
                    "estate",
                    f"No guardian nominated by {who} for minor children",
                    "Nominate a guardian in the will and consider a testamentary trust — minors cannot inherit directly (funds go to the"
                    " Guardian’s Fund).",
                    key,
                )
            )
        r = p.retirement
        if not r.retired and r.requiredCapital > 0 and r.yearsToRetirement < 1 / 12 and r.shortfall > 0:
            f.append(
                _finding(
                    "critical",
                    "retirement",
                    f"Retirement capital shortfall of {rand(r.shortfall)} for {who}",
                    "Retirement is imminent — consider a later retirement date, a lower income or phased retirement.",
                    key,
                )
            )
        elif not r.retired and r.requiredCapital > 0:
            if r.status == "covered":
                f.append(
                    _finding(
                        "positive",
                        "retirement",
                        f"{who} is on track for retirement",
                        f"Projected capital funds about {js_str(js_round(r.replacementRatio * 100))}% of current income"
                        f" from age {js_str(r.retirementAge)}.",
                        key,
                    )
                )
            else:
                f.append(
                    _finding(
                        "critical" if r.status == "shortfall" else "warning",
                        "retirement",
                        f"Retirement shortfall for {who}: save an extra {rand(r.additionalMonthly)} p.m.",
                        f"Projected capital funds {js_str(js_round(r.funded * 100))}% of the target income"
                        f" ({js_str(js_round(r.replacementRatio * 100))}% replacement"
                        f" vs {js_str(js_round(r.targetReplacementRatio * 100))}% targeted).",
                        key,
                    )
                )
        if r.retired and r.depletionAge is not None and r.depletionAge < r.planningAge:
            f.append(
                _finding(
                    "critical",
                    "retirement",
                    f"{who}'s capital may run out at age {js_str(math.floor(r.depletionAge))}",
                    "Consider reducing the drawdown, a guaranteed annuity for core income, or other income sources.",
                    key,
                )
            )
        funds = [x for x in doc.retirementFunds if x.owner == key]
        if any(not x.beneficiariesNominated for x in funds):
            f.append(
                _finding(
                    "info",
                    "retirement",
                    f"Nominate beneficiaries on all of {who}'s retirement funds",
                    "Nominations guide the trustees under s37C and speed up payment to dependants.",
                    key,
                )
            )
        if any(x.type == "living-annuity" and x.drawdownRate > 0.08 for x in funds):
            f.append(
                _finding(
                    "warning",
                    "retirement",
                    f"High living annuity drawdown for {who}",
                    "Drawdowns above ~8% p.a. carry a high risk of capital depletion.",
                    key,
                )
            )
        if estate.liquidityShortfall == 0 and any(
            x.lifeAssured == key and x.type == "life" and x.beneficiary == "estate" for x in doc.policies
        ):
            f.append(
                _finding(
                    "info",
                    "estate",
                    f"Review beneficiary nominations on {who}'s life policies",
                    "Policies paying into the estate attract executor’s fees and delay payment; nominate beneficiaries where estate"
                    " liquidity is sufficient.",
                    key,
                )
            )

    if cashflow.takeHome > 0:
        if cashflow.surplus < 0:
            f.append(
                _finding(
                    "critical",
                    "cashflow",
                    f"Monthly cash-flow deficit of {rand(-cashflow.surplus)}",
                    "Outgoings exceed take-home pay. Budget restructuring should precede new commitments.",
                )
            )
        else:
            f.append(
                _finding(
                    "positive",
                    "cashflow",
                    f"Monthly surplus of {rand(cashflow.surplus)}",
                    "Available to fund recommendations, build the emergency fund or reduce debt.",
                )
            )
    if ratios.debtToIncome > 0.4:
        f.append(
            _finding(
                "critical",
                "debt",
                f"Debt repayments are {js_str(js_round(ratios.debtToIncome * 100))}% of gross income",
                "Above 40% indicates high debt stress. Prioritise expensive unsecured debt.",
            )
        )
    elif ratios.debtToIncome > 0.3:
        f.append(
            _finding(
                "warning",
                "debt",
                f"Debt repayments are {js_str(js_round(ratios.debtToIncome * 100))}% of gross income",
                "Aim to keep total debt repayments below 30–36% of gross income.",
            )
        )
    expensive = [x for x in doc.liabilities if x.type in _UNSECURED and x.interestRate >= 0.18 and x.balance > 0]
    if expensive:
        n = len(expensive)
        f.append(
            _finding(
                "warning",
                "debt",
                f"{n} high-interest debt account{'s' if n > 1 else ''} (18%+)",
                f"Settling {rand(_sum([x.balance for x in expensive]))} of expensive debt is a guaranteed, tax-free return.",
            )
        )
    if emergency.need > 0 and emergency.status != "covered":
        f.append(
            _finding(
                "warning" if emergency.status == "shortfall" else "info",
                "emergency",
                f"Emergency fund at {to_fixed(ratios.emergencyMonths, 1)} months",
                f"Build a further {rand(emergency.shortfall)} in an accessible money-market account.",
            )
        )
    for e in education:
        if e.status != "covered" and e.status != "na":
            if e.lumpSumRequired > 0:
                f.append(
                    _finding(
                        "warning",
                        "education",
                        f"Education shortfall for {e.name}: {rand(e.lumpSumRequired)} needed now",
                        "Study is already under way, so the remaining costs must be funded from capital.",
                    )
                )
            else:
                f.append(
                    _finding(
                        "warning",
                        "education",
                        f"Education shortfall for {e.name}: {rand(e.monthlyRequired)} p.m.",
                        f"Tertiary costs of about {rand(e.capitalAtStart)} are needed"
                        f" from {js_str(now.year + js_round(e.yearsToTertiary))}.",
                    )
                )
    for d in doc.dependants:
        if d.specialNeeds:
            f.append(
                _finding(
                    "warning",
                    "estate",
                    f"{d.name or 'A dependant'} has special needs",
                    "Plan for lifelong support: a special trust (s6B) in the will, guardianship and capital beyond the normal"
                    " dependency age.",
                )
            )
    if doc.household.maritalStatus == "married" and doc.household.maritalRegime == "icop":
        f.append(
            _finding(
                "info",
                "estate",
                "Married in community of property",
                "Both spouses share one joint estate; business risk and debts affect both. Consider the implications with an attorney.",
            )
        )
    if risk.mismatch:
        f.append(
            _finding(
                "warning",
                "investment",
                "Risk tolerance and capacity differ",
                "The recommended portfolio follows the lower of the two; discuss the difference with the client.",
            )
        )

    e = doc.engagement
    if not e.popiaConsent:
        f.append(
            _finding(
                "critical",
                "compliance",
                "POPIA consent not recorded",
                "Obtain the client’s consent to process personal (and special personal) information before advice is finalised.",
            )
        )
    if not e.disclosureLetterProvided:
        f.append(
            _finding(
                "warning",
                "compliance",
                "FSP disclosures not confirmed",
                "Confirm that the FSP and representative disclosures (GCC s4, s5 and s7) were provided.",
            )
        )
    if not e.ficaVerified:
        f.append(
            _finding(
                "warning",
                "compliance",
                "FICA verification outstanding",
                "Verify the client’s identity and residential address before implementation.",
            )
        )
    if any(r.isReplacement and (not r.replacementReasons or not r.replacementConsequences) for r in doc.advice.recommendations):
        f.append(
            _finding(
                "critical",
                "compliance",
                "Replacement disclosures incomplete",
                "GCC s8(1)(d) requires the costs, consequences and reasons for replacing a product to be recorded.",
            )
        )

    f.sort(key=lambda x: _SEVERITY_ORDER[x["severity"]])  # stable, as Array.prototype.sort
    return [Finding.build(x) for x in f]


def _completeness(doc: FnaDocument, people: list[PersonKey], exp: LivingExpenses, risk: RiskProfileResult) -> list[CompletenessItem]:
    items = [
        {
            "section": "Engagement",
            "complete": bool(doc.engagement.popiaConsent and doc.engagement.meetingDate),
            "detail": "Meeting details and POPIA consent",
        }
    ]
    for k in people:
        p = _person(doc, k)
        label = "Client" if k == "client" else "Spouse"
        items.append(
            {
                "section": f"{label} profile",
                "complete": bool(p.firstName and p.surname and p.dateOfBirth and p.gender),
                "detail": "Name, date of birth and gender",
            }
        )
        items.append(
            {
                "section": f"{label} income",
                "complete": _income(doc, k).grossMonthly > 0 or p.employmentType in _NOT_WORKING,
                "detail": "Gross income",
            }
        )
    items.append({"section": "Expenses", "complete": exp.total > 0, "detail": "Monthly living expenses"})
    items.append(
        {"section": "Assets", "complete": len(doc.assets) > 0 or len(doc.retirementFunds) > 0, "detail": "Assets and retirement funds"}
    )
    items.append({"section": "Existing cover", "complete": len(doc.policies) > 0, "detail": "Existing policies (or confirmed none)"})
    items.append({"section": "Estate", "complete": all(_estate(doc, k).hasWill != "unsure" for k in people), "detail": "Will status"})
    items.append({"section": "Risk profile", "complete": risk.complete, "detail": "Risk questionnaire"})
    items.append(
        {
            "section": "Advice",
            "complete": len(doc.advice.recommendations) > 0 or bool(doc.advice.summary),
            "detail": "Recommendations recorded",
        }
    )
    return [CompletenessItem.build(x) for x in items]


# --------------------------------------------------------------------------- orchestrator


def analyse(doc: FnaDocument, *, today: Today = None) -> Analysis:
    """``analyse(doc, now)``: the full needs analysis of one document (§12.7)."""
    now = Now.of(today)
    a = doc.assumptions
    table = table_for(a.taxYear)
    hh = doc.household
    has_spouse = hh.includeSpouse and hh.maritalStatus in ("married", "life-partner")
    people: list[PersonKey] = ["client", "spouse"] if has_spouse else ["client"]

    income_map = {k: compute_income(doc, k, table, now) for k in people}
    incomes = [income_map[k] for k in people]
    cashflow, exp = _cashflow(doc, incomes, people)
    net_worth = _net_worth(doc)
    education = compute_education(doc, now)
    emergency = _emergency(doc, cashflow)

    gross_total = _sum([i.grossMonthlyEquivalent for i in incomes])
    housing = _sum([x.monthlyRepayment for x in doc.liabilities if x.type == "home-loan"]) + doc.expenses.items.get("rent", 0)
    retirement_saving = _sum([i.retirementPayrollMonthly + i.retirementEmployerMonthly + i.retirementOwnMonthly for i in incomes])
    essential_monthly = cashflow.essentialExpenses + cashflow.debtRepayments + cashflow.riskPremiums
    if gross_total > 0:
        unsecured = _sum([x.monthlyRepayment for x in doc.liabilities if x.type in _UNSECURED])
        # Employer contributions are part of the cost-to-company package, so they count in the base too.
        savings_rate = (retirement_saving + _sum([x.monthlyContribution for x in doc.assets])) / (
            gross_total + _sum([i.retirementEmployerMonthly for i in incomes])
        )
        ratios = Ratios.build(
            {
                "debtToIncome": cashflow.debtRepayments / gross_total,
                "unsecuredDebtToIncome": unsecured / gross_total,
                "housingToIncome": housing / gross_total,
                "savingsRate": savings_rate,
                "premiumsToIncome": cashflow.riskPremiums / gross_total,
                "emergencyMonths": emergency.provision / essential_monthly if essential_monthly > 0 else 0,
            }
        )
    else:
        ratios = Ratios.build(
            {
                "debtToIncome": 0,
                "unsecuredDebtToIncome": 0,
                "housingToIncome": 0,
                "savingsRate": 0,
                "premiumsToIncome": 0,
                "emergencyMonths": emergency.provision / essential_monthly if essential_monthly > 0 else 0,
            }
        )

    persons: dict[PersonKey, PersonAnalysis] = {}
    for k in people:
        income = income_map[k]
        estate = compute_estate(doc, k, income, table, has_spouse)
        death, death_years, death_monthly = _death(doc, k, income, estate, education, table, has_spouse, income_map.get(_other(k)), now)
        income_protection, disability, severe_illness, funeral = _disability(doc, k, income, has_spouse)
        persons[k] = PersonAnalysis.build(
            {
                "key": k,
                "name": income.name,
                "income": income,
                "death": death,
                "deathIncomeYears": death_years,
                "deathIncomeMonthly": death_monthly,
                "disability": disability,
                "incomeProtection": income_protection,
                "severeIllness": severe_illness,
                "funeral": funeral,
                "estate": estate,
                "retirement": compute_retirement(doc, k, income, now, table=table),
            }
        )

    ret = a.riskCapitalReturn
    education_need = _sum([pv(e.capitalAtStart, ret, e.yearsToTertiary) if e.capitalAtStart > 0 else 0 for e in education])
    education_provision = _sum([pv(e.projectedSavings, ret, e.yearsToTertiary) for e in education])
    education_total = _make_need(
        "education",
        "Education funding",
        [("Tertiary education (present value)", education_need)],
        [("Education savings (present value)", education_provision)],
    )

    risk = score_risk_profile(doc.riskProfile.answers)
    completeness = _completeness(doc, people, exp, risk)
    return Analysis.build(
        {
            "table": table,
            "people": people,
            "persons": persons,
            "incomes": incomes,
            "cashflow": cashflow,
            "ratios": ratios,
            "netWorth": net_worth,
            "emergency": emergency,
            "education": education,
            "educationTotal": education_total,
            "goals": _goals(doc, now),
            "risk": risk,
            "findings": _findings(doc, people, persons, cashflow, ratios, emergency, education, risk, now),
            "completeness": completeness,
            "completenessScore": sum(1 for c in completeness if c.complete) / len(completeness),
        }
    )
