"""The Shahil FNA app's client file (``FnaDocument``, schema v2), mirrored from ``lib/fna/types.ts``.

Field names are kept exactly as in TypeScript (camelCase) so a document round-trips through
the app's ``shahil-fna`` export format unchanged. Defaults follow ``lib/fna/defaults.ts``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PersonKey = Literal["client", "spouse"]
Owner = Literal["client", "spouse", "joint"]
Gender = Literal["male", "female", ""]
EmploymentType = Literal["salaried", "self-employed", "commission", "contract", "retired", "unemployed", "student"]
MaritalStatus = Literal["single", "married", "life-partner", "divorced", "widowed"]
MaritalRegime = Literal["icop", "anc-accrual", "anc-no-accrual", "customary", "foreign", "na"]
DependantRelationship = Literal["child", "parent", "sibling", "other"]
SchoolType = Literal["none", "public", "private"]
RetirementFundType = Literal[
    "pension", "provident", "retirement-annuity", "preservation-pension", "preservation-provident", "living-annuity", "life-annuity"
]
AssetType = Literal[
    "primary-residence",
    "residential-property",
    "commercial-property",
    "vehicle",
    "personal-effects",
    "cash",
    "money-market",
    "unit-trust",
    "shares",
    "tfsa",
    "endowment",
    "offshore",
    "business-interest",
    "loan-account",
    "crypto",
    "other",
]
Earmark = Literal["general", "emergency", "retirement", "education", "goal"]
LiabilityType = Literal[
    "home-loan", "vehicle-finance", "personal-loan", "credit-card", "store-card", "overdraft", "student-loan", "business-loan", "tax", "other"
]
PolicyType = Literal["life", "disability-lump", "income-protection", "severe-illness", "funeral", "family-income"]
Beneficiary = Literal["estate", "spouse", "children", "trust", "other", "business"]
DisabilityDefinition = Literal["own-occupation", "suited-occupation", "any-occupation", "functional", "activities-of-daily-living"]
ExpenseCategory = Literal["housing", "transport", "household", "communication", "insurance", "medical", "children", "personal", "family", "other"]
IncomeTargetMode = Literal["percent", "amount"]
ScopeArea = Literal["risk", "retirement", "investment", "estate", "education", "debt", "medical", "short-term"]
NeedArea = Literal[
    "life",
    "disability",
    "income-protection",
    "severe-illness",
    "funeral",
    "estate",
    "retirement",
    "education",
    "emergency",
    "investment",
    "debt",
    "medical",
    "other",
]
RecommendationAction = Literal["new", "increase", "reduce", "replace", "cancel", "maintain", "restructure", "refer"]
ClientDecision = Literal["pending", "accepted", "declined", "deferred"]

SCHEMA_VERSION = 2
DEFAULT_TAX_YEAR = "2026/27"


def uid() -> str:
    return str(uuid.uuid4())


class _FnaModel(BaseModel):
    # Unknown fields from a newer app version are kept, so nothing is lost on the way through.
    model_config = ConfigDict(extra="allow", validate_assignment=False)


class Person(_FnaModel):
    title: str = ""
    firstName: str = ""
    surname: str = ""
    idNumber: str = ""
    passportNumber: str = ""
    nationality: str = "South African"
    dateOfBirth: str = ""  # ISO yyyy-mm-dd
    gender: Gender = ""
    smoker: bool = False
    healthNotes: str = ""
    hazardousPursuits: str = ""
    occupation: str = ""
    employer: str = ""
    employmentType: EmploymentType = "salaried"
    educationLevel: str = ""
    email: str = ""
    mobile: str = ""
    taxNumber: str = ""


class Household(_FnaModel):
    maritalStatus: MaritalStatus = "single"
    maritalRegime: MaritalRegime = "na"
    dateOfMarriage: str = ""
    includeSpouse: bool = False
    physicalAddress: str = ""
    city: str = ""
    province: str = ""
    postalCode: str = ""


class Dependant(_FnaModel):
    id: str = Field(default_factory=uid)
    name: str = ""
    relationship: DependantRelationship = "child"
    dateOfBirth: str = ""
    gender: Gender = ""
    dependencyEndAge: float = 23
    monthlySupport: float = 0
    supportYears: float = 0
    schoolType: SchoolType = "public"
    schoolFeesAnnual: float = 0
    tertiary: bool = True
    tertiaryStartAge: float = 19
    tertiaryYears: float = 4
    tertiaryCostAnnual: float = 150_000
    educationSavings: float = 0
    educationSavingsMonthly: float = 0
    specialNeeds: bool = False
    notes: str = ""


class Income(_FnaModel):
    grossMonthly: float = 0
    annualBonus: float = 0
    otherTaxableMonthly: float = 0
    nonTaxableMonthly: float = 0
    medicalAidPayrollMonthly: float = 0
    medicalSchemeMembers: float = 0
    otherPayrollDeductionsMonthly: float = 0
    payeOverrideMonthly: float | None = None
    uifApplies: bool = True


class RetirementFund(_FnaModel):
    id: str = Field(default_factory=uid)
    owner: PersonKey = "client"
    type: RetirementFundType = "pension"
    provider: str = ""
    name: str = ""
    value: float = 0
    savingsPot: float = 0
    employeeMonthly: float = 0
    employerMonthly: float = 0
    viaPayroll: bool = True
    beneficiariesNominated: bool = False
    drawdownRate: float = 0.05
    notes: str = ""


class Asset(_FnaModel):
    id: str = Field(default_factory=uid)
    owner: Owner = "client"
    type: AssetType = "cash"
    description: str = ""
    provider: str = ""
    value: float = 0
    baseCost: float = 0
    monthlyContribution: float = 0
    earmark: Earmark = "general"
    monthlyIncome: float = 0
    bequeathToSpouse: bool = True
    notes: str = ""


class Liability(_FnaModel):
    id: str = Field(default_factory=uid)
    owner: Owner = "client"
    type: LiabilityType = "home-loan"
    lender: str = ""
    description: str = ""
    balance: float = 0
    interestRate: float = 0.1075  # prime from 25 Sept 2026
    monthlyRepayment: float = 0
    linkedAssetId: str = ""
    creditLifeCover: bool = False
    settleOnDeath: bool = True
    settleOnDisability: bool = True


class Policy(_FnaModel):
    id: str = Field(default_factory=uid)
    lifeAssured: PersonKey = "client"
    type: PolicyType = "life"
    insurer: str = ""
    policyNumber: str = ""
    isGroup: bool = False
    cover: float = 0
    monthlyBenefit: float = 0
    benefitTermYears: float = 0
    waitingPeriodMonths: float = 3
    benefitToAge: float = 65
    beneficiary: Beneficiary = "spouse"
    definition: DisabilityDefinition = "own-occupation"
    accelerated: bool = False
    premiumMonthly: float = 0
    premiumEscalation: float = 0
    coverEscalation: float = 0
    inceptionDate: str = ""
    notes: str = ""


class CustomExpense(_FnaModel):
    id: str = Field(default_factory=uid)
    category: ExpenseCategory = "other"
    label: str = ""
    amount: float = 0
    essential: bool = False


class Expenses(_FnaModel):
    items: dict[str, float] = {}
    custom: list[CustomExpense] = []


class RetirementGoal(_FnaModel):
    retirementAge: float = 65
    planningAge: float = 92
    targetMode: IncomeTargetMode = "percent"
    targetPercent: float = 0.75
    targetMonthly: float = 0
    otherIncomeMonthly: float = 0


class OtherGoal(_FnaModel):
    id: str = Field(default_factory=uid)
    owner: Owner = "client"
    name: str = ""
    cost: float = 0
    targetYear: int = Field(default_factory=lambda: date.today().year + 5)
    existingSavings: float = 0
    monthlyContribution: float = 0
    priority: Literal["high", "medium", "low"] = "medium"


class EstateProfile(_FnaModel):
    hasWill: Literal["yes", "no", "unsure"] = "unsure"
    willDate: str = ""
    willLocation: str = ""
    executor: str = ""
    guardianNominated: Literal["yes", "no", "na"] = "na"
    hasTrust: bool = False
    trustNotes: str = ""
    additionalIncomeNeedMonthly: float = 0
    additionalCapitalNeed: float = 0
    portedAbatement: float = 0
    cashBequests: float = 0
    accrualCommencementValue: float = 0
    funeralCost: float = 50_000
    notes: str = ""


class Assumptions(_FnaModel):
    taxYear: str = DEFAULT_TAX_YEAR
    cpi: float = 0.045
    salaryEscalation: float = 0.055
    preRetirementReturn: float = 0.085
    postRetirementReturn: float = 0.075
    riskCapitalReturn: float = 0.07
    educationInflation: float = 0.065
    deathIncomeReplacement: float = 0.75
    incomeProtectionTarget: float = 1
    disabilityAdjustments: float = 150_000
    disabilityIncludeRetirement: bool = True
    severeIllnessMonths: float = 24
    emergencyMonths: float = 3
    funeralAdult: float = 50_000
    funeralChild: float = 25_000
    executorFeeRate: float = 0.035
    vatRate: float = 0.15
    deathIncomeTerm: Literal["auto", "youngest-independent", "spouse-retirement", "fixed"] = "auto"
    deathIncomeFixedYears: float = 15


class Engagement(_FnaModel):
    meetingDate: str = Field(default_factory=lambda: date.today().isoformat())
    meetingType: Literal["in-person", "virtual", "telephonic"] = "in-person"
    adviceType: Literal["comprehensive", "limited"] = "comprehensive"
    scope: list[ScopeArea] = Field(default_factory=lambda: ["risk", "retirement", "investment", "estate", "education", "debt"])
    limitations: str = ""
    disclosureLetterProvided: bool = False
    popiaConsent: bool = False
    popiaConsentDate: str = ""
    specialInfoConsent: bool = False
    ficaVerified: bool = False
    clientDeclinedFna: bool = False
    declineReason: str = ""
    conflictsDisclosed: bool = False
    remunerationDisclosed: bool = False
    pepStatus: Literal["no", "yes", "unknown"] = "no"
    marketingOptIn: bool = False
    copyProvided: bool = False
    reasonForAdvice: str = ""
    clientObjectives: str = ""
    clientConcerns: str = ""
    otherAdvisers: str = ""


class Recommendation(_FnaModel):
    id: str = Field(default_factory=uid)
    area: NeedArea = "life"
    lifeAssured: PersonKey = "client"
    action: RecommendationAction = "new"
    productType: str = ""
    provider: str = ""
    productName: str = ""
    amount: float = 0
    premiumMonthly: float = 0
    premiumEscalation: float = 0.06
    rationale: str = ""
    isReplacement: bool = False
    replacementChecklist: list[str] = []
    replacedPolicy: str = ""
    replacementReasons: str = ""
    replacementCostComparison: str = ""
    replacementConsequences: str = ""
    clientDecision: ClientDecision = "pending"
    clientReason: str = ""


DEFAULT_FEES_AND_COMMISSION = (
    "Commission on long-term insurance products is paid by the product supplier within the limits prescribed by the "
    "Regulations under the Long-term Insurance Act and disclosed in the product quotation. No fee is charged to the client "
    "unless agreed in writing."
)


class AdviceRecord(_FnaModel):
    summary: str = ""
    recommendations: list[Recommendation] = []
    productsConsidered: str = ""
    clientDeviations: str = ""
    feesAndCommission: str = DEFAULT_FEES_AND_COMMISSION
    conflictsOfInterest: str = ""
    nextReviewDate: str = ""
    clientSignedAt: str = ""
    adviserSignedAt: str = ""


class RiskProfile(_FnaModel):
    answers: dict[str, float] = {}
    notes: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class FnaDocument(_FnaModel):
    id: str = Field(default_factory=uid)
    schemaVersion: int = SCHEMA_VERSION
    createdAt: str = Field(default_factory=_now_iso)
    updatedAt: str = Field(default_factory=_now_iso)
    status: Literal["draft", "in-progress", "advice-given", "archived"] = "draft"
    client: Person = Field(default_factory=Person)
    spouse: Person = Field(default_factory=Person)
    household: Household = Field(default_factory=Household)
    dependants: list[Dependant] = []
    income: dict[PersonKey, Income] = Field(default_factory=lambda: {"client": Income(), "spouse": Income()})
    expenses: Expenses = Field(default_factory=Expenses)
    retirementFunds: list[RetirementFund] = []
    assets: list[Asset] = []
    liabilities: list[Liability] = []
    policies: list[Policy] = []
    retirement: dict[PersonKey, RetirementGoal] = Field(default_factory=lambda: {"client": RetirementGoal(), "spouse": RetirementGoal()})
    goals: list[OtherGoal] = []
    estate: dict[PersonKey, EstateProfile] = Field(default_factory=lambda: {"client": EstateProfile(), "spouse": EstateProfile()})
    riskProfile: RiskProfile = Field(default_factory=RiskProfile)
    assumptions: Assumptions = Field(default_factory=Assumptions)
    engagement: Engagement = Field(default_factory=Engagement)
    advice: AdviceRecord = Field(default_factory=AdviceRecord)
    notes: str = ""


class ExportFile(_FnaModel):
    """The app's plain (unencrypted) ``shahil-fna`` export (lib/store/transfer.ts)."""

    format: Literal["shahil-fna"] = "shahil-fna"
    version: Literal[1] = 1
    encrypted: Literal[False] = False
    exportedAt: str = Field(default_factory=_now_iso)
    document: FnaDocument


LIST_FIELDS: dict[str, type[_FnaModel]] = {
    "dependants": Dependant,
    "retirementFunds": RetirementFund,
    "assets": Asset,
    "liabilities": Liability,
    "policies": Policy,
    "goals": OtherGoal,
}
