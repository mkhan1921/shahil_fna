"""What ``analyse()`` returns, mirrored from the result types in ``lib/fna/analysis.ts`` (§12.7).

Field names and order follow the TypeScript objects, so ``model_dump(mode="json")`` has the same keys as
the app's ``JSON.stringify(analysis)`` and the review overlay can render Python results with the app's
own components. Optional TypeScript fields (``note?``, ``person?``) are left out of a dump when unset, as
JSON leaves out ``undefined``; nullable ones (``depletionAge``, risk categories) are dumped as null.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal, Self

from pydantic import BaseModel, Field

from ..models.fna import NeedArea, PersonKey
from .tables import TaxTable

Status = Literal["covered", "partial", "shortfall", "na"]
Severity = Literal["critical", "warning", "info", "positive"]
FindingArea = NeedArea | Literal["data", "compliance", "cashflow"]
ExpenseCategory = Literal[
    "housing", "transport", "household", "communication", "insurance", "medical", "children", "personal", "family", "other"
]

_new = object.__new__
# BaseModel keeps these in slots; setting them through the slot descriptors is what model_construct does, minus its overhead.
_set_fields_set = BaseModel.__dict__["__pydantic_fields_set__"].__set__
_set_extra = BaseModel.__dict__["__pydantic_extra__"].__set__
_set_private = BaseModel.__dict__["__pydantic_private__"].__set__


def _unset(v: object) -> bool:
    return v is None


class _Result(BaseModel):
    """Result models. The engine builds them with :meth:`build`; anyone else uses the validating constructor."""

    _optional: ClassVar[tuple[str, ...]] = ()  # TypeScript `?:` fields, None when not given

    @classmethod
    def build(cls, values: dict[str, Any]) -> Self:
        """An instance from values the engine computed itself, without validating them again.

        The engine gives every field its declared type (whole numbers for ``int`` fields, model instances for
        nested models), so validation would only repeat work; skipping it keeps ``analyse`` near 1 ms."""
        obj = _new(cls)
        d = obj.__dict__
        d.update(values)
        _set_fields_set(obj, set(values))
        for name in cls._optional:
            if name not in d:
                d[name] = None
        _set_extra(obj, None)
        _set_private(obj, None)
        return obj


class Line(_Result):
    _optional = ("note",)
    label: str
    amount: float
    note: str | None = Field(default=None, exclude_if=_unset)


def line(label: str, amount: float, note: str | None = None) -> Line:
    """``Line.build`` for the engine's commonest result, without the intermediate dict."""
    obj = _new(Line)
    d = obj.__dict__
    d["label"] = label
    d["amount"] = amount
    d["note"] = note
    _set_fields_set(obj, {"label", "amount"} if note is None else {"label", "amount", "note"})
    _set_extra(obj, None)
    _set_private(obj, None)
    return obj


class PersonTaxResult(_Result):
    grossAnnual: float
    remuneration: float
    retirementDeduction: float
    taxable: float
    taxBeforeRebates: float
    rebates: float
    medicalCredit: float
    annualTax: float
    monthlyPaye: float
    monthlyUif: float
    marginalRate: float
    effectiveRate: float


class PersonIncome(_Result):
    key: PersonKey
    name: str
    age: int
    taxAge: int  # age at the end of the tax year (used for rebates)
    ageKnown: bool
    exactAge: float
    tax: PersonTaxResult
    grossMonthly: float  # regular (non-bonus) monthly gross incl. other taxable income
    grossMonthlyEquivalent: float  # incl. bonus (annual / 12)
    earnedGrossMonthly: float  # salary / business income plus bonus, per month (excludes passive income)
    earnedAfterTaxMonthly: float  # 0 when not working
    working: bool
    payeMonthly: float
    uifMonthly: float
    retirementPayrollMonthly: float
    retirementEmployerMonthly: float
    retirementOwnMonthly: float
    medicalPayrollMonthly: float
    otherDeductionsMonthly: float
    nonTaxableMonthly: float
    takeHomeMonthly: float  # regular monthly take-home pay (excludes bonus)
    afterTaxMonthly: float  # after-tax income incl. bonus, per month: the basis for income replacement
    bonusNet: float


class CategoryAmount(_Result):
    key: ExpenseCategory
    label: str
    amount: float


class Cashflow(_Result):
    takeHome: float
    livingExpenses: float
    essentialExpenses: float
    byCategory: list[CategoryAmount]
    debtRepayments: float
    riskPremiums: float
    savingsContributions: float
    totalOutgoings: float
    surplus: float
    bonusNet: float


class Ratios(_Result):
    debtToIncome: float
    unsecuredDebtToIncome: float
    housingToIncome: float
    savingsRate: float
    premiumsToIncome: float
    emergencyMonths: float


class GroupAmount(_Result):
    key: str
    label: str
    amount: float


class NetWorth(_Result):
    assets: float
    retirement: float
    liabilities: float
    netWorth: float
    liquidAssets: float
    byGroup: list[GroupAmount]


class NeedResult(_Result):
    _optional = ("person",)
    area: NeedArea
    person: PersonKey | None = Field(default=None, exclude_if=_unset)
    title: str
    need: float
    provision: float
    shortfall: float
    funded: float  # funding ratio 0..1 (provision / need)
    status: Status
    monthly: bool = False
    needLines: list[Line]
    provisionLines: list[Line]
    notes: list[str]


class EstateResult(_Result):
    person: PersonKey
    grossEstate: float
    deemedProperty: float
    liabilities: float
    executorFees: float
    mastersFees: float
    conveyancing: float
    bondCancellation: float
    sundries: float
    funeral: float
    cgt: float
    capitalGains: float
    accrualPayable: float
    accrualReceivable: float
    netEstate: float
    spouseDeduction: float
    abatement: float
    dutiable: float
    estateDuty: float
    totalCosts: float
    cashRequired: float
    cashAvailable: float
    liquidityShortfall: float
    lines: list[Line]
    notes: list[str]


class RetirementYear(_Result):
    age: int
    year: int
    capital: float
    capitalReal: float
    contribution: float
    withdrawal: float
    phase: Literal["accumulation", "retirement"]


_YEAR_FIELDS = frozenset(RetirementYear.model_fields)


def retirement_year(age: int, year: int, capital: float, real: float, contribution: float, withdrawal: float, phase: str) -> RetirementYear:
    """``RetirementYear.build`` for the projection timeline (about 50 entries a person)."""
    obj = _new(RetirementYear)
    d = obj.__dict__
    d["age"] = age
    d["year"] = year
    d["capital"] = capital
    d["capitalReal"] = real
    d["contribution"] = contribution
    d["withdrawal"] = withdrawal
    d["phase"] = phase
    _set_fields_set(obj, set(_YEAR_FIELDS))
    _set_extra(obj, None)
    _set_private(obj, None)
    return obj


class RetirementResult(_Result):
    person: PersonKey
    retired: bool
    currentAge: float
    retirementAge: float
    planningAge: float
    yearsToRetirement: float
    yearsInRetirement: float
    currentCapital: float
    monthlyContributions: float
    projectedCapital: float
    projectedCapitalReal: float
    targetMonthlyToday: float
    otherIncomeToday: float
    targetMonthlyAtRetirement: float
    requiredCapital: float
    requiredCapitalReal: float
    shortfall: float
    shortfallReal: float
    additionalMonthly: float
    sustainableMonthlyToday: float
    replacementRatio: float
    targetReplacementRatio: float
    initialDrawdown: float
    depletionAge: float | None
    funded: float
    status: Status
    timeline: list[RetirementYear]
    notes: list[str]


class EducationYear(_Result):
    year: float
    age: float
    cost: float


class EducationResult(_Result):
    dependantId: str
    name: str
    age: int
    yearsToTertiary: float
    tertiaryPV: float
    schoolPV: float
    capitalAtStart: float
    projectedSavings: float
    shortfallAtStart: float
    shortfallToday: float
    monthlyRequired: float
    lumpSumRequired: float  # shortfall that must be funded now (study already under way)
    funded: float
    status: Status
    schedule: list[EducationYear]


class GoalResult(_Result):
    id: str
    name: str
    years: int
    futureCost: float
    projected: float
    shortfall: float
    monthlyRequired: float
    funded: float
    status: Status


class Finding(_Result):
    _optional = ("person",)
    severity: Severity
    area: FindingArea
    person: PersonKey | None = Field(default=None, exclude_if=_unset)
    title: str
    detail: str


class RiskCategory(_Result):
    key: Literal["conservative", "moderately-conservative", "moderate", "moderately-aggressive", "aggressive"]
    label: str
    description: str
    equity: str
    horizon: str
    target: str


class RiskProfileResult(_Result):
    complete: bool
    answered: int
    total: int
    toleranceScore: float
    capacityScore: float
    tolerance: RiskCategory | None
    capacity: RiskCategory | None
    profile: RiskCategory | None
    mismatch: bool


class PersonAnalysis(_Result):
    key: PersonKey
    name: str
    income: PersonIncome
    death: NeedResult
    deathIncomeYears: float
    deathIncomeMonthly: float
    disability: NeedResult
    incomeProtection: NeedResult
    severeIllness: NeedResult
    funeral: NeedResult
    estate: EstateResult
    retirement: RetirementResult


class CompletenessItem(_Result):
    section: str
    complete: bool
    detail: str


class Analysis(_Result):
    table: TaxTable  # holds the whole TaxTableFile; dumps the TypeScript TaxTable fields only
    people: list[PersonKey]
    persons: dict[PersonKey, PersonAnalysis]  # people not analysed are absent, as JSON drops undefined
    incomes: list[PersonIncome]
    cashflow: Cashflow
    ratios: Ratios
    netWorth: NetWorth
    emergency: NeedResult
    education: list[EducationResult]
    educationTotal: NeedResult
    goals: list[GoalResult]
    risk: RiskProfileResult
    findings: list[Finding]
    completeness: list[CompletenessItem]
    completenessScore: float
