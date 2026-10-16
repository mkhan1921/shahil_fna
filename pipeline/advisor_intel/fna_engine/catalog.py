"""Catalogs: a port of ``lib/fna/catalog.ts`` (§12.7). The 45-item expense catalog (with its essential flags)
is also the target for budget extraction (§1.5). The parity harness checks these against the TypeScript."""

from __future__ import annotations

from typing import Literal, NamedTuple

from ..models.fna import (
    AssetType,
    Beneficiary,
    DependantRelationship,
    DisabilityDefinition,
    EmploymentType,
    LiabilityType,
    MaritalRegime,
    MaritalStatus,
    NeedArea,
    PolicyType,
    RecommendationAction,
    RetirementFundType,
    ScopeArea,
)
from .results import ExpenseCategory

ReportStyle = Literal["standard", "vintage"]
AssetGroup = Literal["property", "lifestyle", "liquid", "investment", "business"]


class ExpenseItem(NamedTuple):
    key: str
    label: str
    category: ExpenseCategory
    essential: bool
    hint: str | None = None


class AssetBehaviour(NamedTuple):
    liquid: bool  # can be realised within ~30 days without major loss
    cgtExempt: bool  # personal-use asset excluded from CGT (8th Schedule para 53)
    group: AssetGroup


EXPENSE_CATEGORIES: list[tuple[ExpenseCategory, str]] = [
    ("housing", "Housing"),
    ("transport", "Transport"),
    ("household", "Groceries & household"),
    ("communication", "Connectivity & subscriptions"),
    ("insurance", "Short-term insurance"),
    ("medical", "Medical"),
    ("children", "Children & education"),
    ("personal", "Personal & lifestyle"),
    ("family", "Family support & giving"),
    ("other", "Other"),
]

# Living expenses only: debt repayments, risk premiums and savings have their own sections.
EXPENSE_ITEMS: list[ExpenseItem] = [
    ExpenseItem("rent", "Rent", "housing", True, "Bond repayments are captured under Liabilities"),
    ExpenseItem("rates", "Rates & taxes", "housing", True),
    ExpenseItem("levies", "Levies / HOA", "housing", True),
    ExpenseItem("utilities", "Electricity & water", "housing", True),
    ExpenseItem("security", "Security / armed response", "housing", True),
    ExpenseItem("domestic", "Domestic worker & garden", "housing", False),
    ExpenseItem("maintenance", "Home maintenance", "housing", False),
    ExpenseItem("backup-power", "Solar / backup power", "housing", False),
    ExpenseItem("fuel", "Fuel", "transport", True, "Vehicle finance is captured under Liabilities"),
    ExpenseItem("vehicle-maintenance", "Vehicle service & tyres", "transport", True),
    ExpenseItem("tolls-parking", "Tolls, parking & licence", "transport", True),
    ExpenseItem("tracking", "Tracking device", "transport", False),
    ExpenseItem("public-transport", "Taxi, Uber & public transport", "transport", True),
    ExpenseItem("groceries", "Groceries & toiletries", "household", True),
    ExpenseItem("cleaning", "Cleaning & household goods", "household", True),
    ExpenseItem("cellphone", "Cellphone & data", "communication", True),
    ExpenseItem("internet", "Fibre / internet", "communication", True),
    ExpenseItem("tv", "DStv & streaming", "communication", False),
    ExpenseItem("subscriptions", "Other subscriptions", "communication", False),
    ExpenseItem("home-insurance", "Buildings & contents insurance", "insurance", True),
    ExpenseItem("car-insurance", "Vehicle insurance", "insurance", True),
    ExpenseItem("other-st", "Other short-term insurance", "insurance", False),
    ExpenseItem("medical-aid", "Medical aid (not via payroll)", "medical", True),
    ExpenseItem("gap-cover", "Gap cover", "medical", True),
    ExpenseItem("medical-oop", "Out-of-pocket medical", "medical", True),
    ExpenseItem("school-fees", "School fees", "children", True),
    ExpenseItem("childcare", "Crèche, aftercare & au pair", "children", True),
    ExpenseItem("extramurals", "Extra-murals & tutoring", "children", False),
    ExpenseItem("school-transport", "School transport", "children", True),
    ExpenseItem("school-other", "Uniforms, books & stationery", "children", True),
    ExpenseItem("clothing", "Clothing", "personal", False),
    ExpenseItem("personal-care", "Personal care & grooming", "personal", False),
    ExpenseItem("gym", "Gym & sport", "personal", False),
    ExpenseItem("dining", "Eating out & entertainment", "personal", False),
    ExpenseItem("holidays", "Holidays (monthly provision)", "personal", False),
    ExpenseItem("hobbies", "Hobbies", "personal", False),
    ExpenseItem("pets", "Pets", "personal", False),
    ExpenseItem("family-support", "Support to parents / extended family", "family", True),
    ExpenseItem("maintenance-paid", "Maintenance / child support paid", "family", True),
    ExpenseItem("donations", "Tithes & donations", "family", False),
    ExpenseItem("gifts", "Gifts & celebrations", "family", False),
    ExpenseItem("bank-fees", "Bank fees", "other", True),
    ExpenseItem("other", "Other", "other", False),
]
EXPENSE_ITEM: dict[str, ExpenseItem] = {i.key: i for i in EXPENSE_ITEMS}

MARITAL_STATUS: dict[MaritalStatus, str] = {
    "single": "Single",
    "married": "Married",
    "life-partner": "Life partner",
    "divorced": "Divorced",
    "widowed": "Widowed",
}

MARITAL_REGIME: dict[MaritalRegime, str] = {
    "icop": "In community of property",
    "anc-accrual": "Out of community — with accrual",
    "anc-no-accrual": "Out of community — without accrual",
    "customary": "Customary marriage",
    "foreign": "Foreign marriage / other",
    "na": "Not applicable",
}

EMPLOYMENT_TYPE: dict[EmploymentType, str] = {
    "salaried": "Salaried employee",
    "self-employed": "Self-employed / business owner",
    "commission": "Commission earner",
    "contract": "Contractor",
    "retired": "Retired",
    "unemployed": "Not working",
    "student": "Student",
}

RELATIONSHIP: dict[DependantRelationship, str] = {
    "child": "Child",
    "parent": "Parent",
    "sibling": "Sibling",
    "other": "Other dependant",
}

RETIREMENT_FUND_TYPE: dict[RetirementFundType, str] = {
    "pension": "Pension fund",
    "provident": "Provident fund",
    "retirement-annuity": "Retirement annuity",
    "preservation-pension": "Pension preservation fund",
    "preservation-provident": "Provident preservation fund",
    "living-annuity": "Living annuity",
    "life-annuity": "Guaranteed (life) annuity",
}

PRE_RETIREMENT_FUNDS: list[RetirementFundType] = [
    "pension",
    "provident",
    "retirement-annuity",
    "preservation-pension",
    "preservation-provident",
]

ASSET_TYPE: dict[AssetType, str] = {
    "primary-residence": "Primary residence",
    "residential-property": "Other residential property",
    "commercial-property": "Commercial property",
    "vehicle": "Vehicle",
    "personal-effects": "Household contents & personal effects",
    "cash": "Bank / savings account",
    "money-market": "Money market / notice deposit",
    "unit-trust": "Unit trusts (discretionary)",
    "shares": "Shares / ETFs",
    "tfsa": "Tax-free savings account",
    "endowment": "Endowment / sinking fund",
    "offshore": "Offshore investments",
    "business-interest": "Business interest",
    "loan-account": "Loan account",
    "crypto": "Crypto assets",
    "other": "Other asset",
}

ASSET_BEHAVIOUR: dict[AssetType, AssetBehaviour] = {
    "primary-residence": AssetBehaviour(False, False, "property"),
    "residential-property": AssetBehaviour(False, False, "property"),
    "commercial-property": AssetBehaviour(False, False, "property"),
    "vehicle": AssetBehaviour(False, True, "lifestyle"),
    "personal-effects": AssetBehaviour(False, True, "lifestyle"),
    "cash": AssetBehaviour(True, True, "liquid"),
    "money-market": AssetBehaviour(True, True, "liquid"),
    "unit-trust": AssetBehaviour(True, False, "investment"),
    "shares": AssetBehaviour(True, False, "investment"),
    "tfsa": AssetBehaviour(True, True, "investment"),
    "endowment": AssetBehaviour(True, True, "investment"),
    "offshore": AssetBehaviour(True, False, "investment"),
    "business-interest": AssetBehaviour(False, False, "business"),
    "loan-account": AssetBehaviour(False, True, "business"),
    "crypto": AssetBehaviour(True, False, "investment"),
    "other": AssetBehaviour(False, False, "lifestyle"),
}

LIABILITY_TYPE: dict[LiabilityType, str] = {
    "home-loan": "Home loan / bond",
    "vehicle-finance": "Vehicle finance",
    "personal-loan": "Personal loan",
    "credit-card": "Credit card",
    "store-card": "Store / retail account",
    "overdraft": "Overdraft",
    "student-loan": "Student loan",
    "business-loan": "Business loan / surety",
    "tax": "SARS liability",
    "other": "Other debt",
}

UNSECURED_DEBT: list[LiabilityType] = ["personal-loan", "credit-card", "store-card", "overdraft", "other"]

POLICY_TYPE: dict[PolicyType, str] = {
    "life": "Life cover",
    "disability-lump": "Disability — lump sum",
    "income-protection": "Income protection",
    "severe-illness": "Severe / critical illness",
    "funeral": "Funeral cover",
    "family-income": "Family income benefit / spouse pension",
}

BENEFICIARY: dict[Beneficiary, str] = {
    "estate": "Estate (no nomination)",
    "spouse": "Spouse / partner",
    "children": "Children",
    "trust": "Trust",
    "other": "Other nominee",
    "business": "Business (buy-and-sell / key person)",
}

DISABILITY_DEFINITION: dict[DisabilityDefinition, str] = {
    "own-occupation": "Own occupation",
    "suited-occupation": "Suited occupation",
    "any-occupation": "Any occupation",
    "functional": "Functional impairment",
    "activities-of-daily-living": "Activities of daily living",
}

NEED_AREA: dict[NeedArea, str] = {
    "life": "Life cover (death)",
    "disability": "Disability — capital",
    "income-protection": "Income protection",
    "severe-illness": "Severe illness",
    "funeral": "Funeral",
    "estate": "Estate liquidity & planning",
    "retirement": "Retirement",
    "education": "Education funding",
    "emergency": "Emergency fund",
    "investment": "Investments",
    "debt": "Debt management",
    "medical": "Medical aid & gap cover",
    "other": "Other",
}

ACTION: dict[RecommendationAction, str] = {
    "new": "New",
    "increase": "Increase",
    "reduce": "Reduce",
    "replace": "Replace",
    "cancel": "Cancel",
    "maintain": "Maintain",
    "restructure": "Restructure",
    "refer": "Refer to specialist",
}

REPORT_STYLE: dict[ReportStyle, dict[str, str]] = {
    "standard": {
        "label": "Standard",
        "description": "Clean and modern, in your accent colour, with colour charts and icons. Compact in print.",
    },
    "vintage": {
        "label": "Vintage",
        "description": (
            "A period financial survey on cream paper: engraved plates, the verdict in plain words, and terms explained in the margin."
        ),
    },
}


def as_report_style(value: object, fallback: ReportStyle = "standard") -> ReportStyle:
    """A report style from untrusted input, or the fallback."""
    return value if value in REPORT_STYLE else fallback  # type: ignore[return-value]


SCOPE_AREA: dict[ScopeArea, str] = {
    "risk": "Risk protection (life, disability, illness)",
    "retirement": "Retirement planning",
    "investment": "Investments & savings",
    "estate": "Estate planning",
    "education": "Education funding",
    "debt": "Debt & cash-flow management",
    "medical": "Medical aid & gap cover",
    "short-term": "Short-term insurance",
}

PROVINCES = [
    "Eastern Cape",
    "Free State",
    "Gauteng",
    "KwaZulu-Natal",
    "Limpopo",
    "Mpumalanga",
    "North West",
    "Northern Cape",
    "Western Cape",
]

TITLES = ["Mr", "Mrs", "Ms", "Miss", "Dr", "Prof", "Adv", "Rev"]

EDUCATION_LEVELS = [
    "Below matric",
    "Matric / NSC",
    "Certificate / diploma",
    "Bachelor's degree",
    "Honours / postgraduate diploma",
    "Master's degree",
    "Doctorate",
    "Professional qualification (CA, CFP®, etc.)",
]


def as_json() -> dict:
    """Every catalog in the shape the TypeScript exports, for the parity check."""
    return {
        "EXPENSE_CATEGORIES": [{"key": k, "label": v} for k, v in EXPENSE_CATEGORIES],
        "EXPENSE_ITEMS": [{k: v for k, v in i._asdict().items() if v is not None} for i in EXPENSE_ITEMS],
        "MARITAL_STATUS": MARITAL_STATUS,
        "MARITAL_REGIME": MARITAL_REGIME,
        "EMPLOYMENT_TYPE": EMPLOYMENT_TYPE,
        "RELATIONSHIP": RELATIONSHIP,
        "RETIREMENT_FUND_TYPE": RETIREMENT_FUND_TYPE,
        "PRE_RETIREMENT_FUNDS": PRE_RETIREMENT_FUNDS,
        "ASSET_TYPE": ASSET_TYPE,
        "ASSET_BEHAVIOUR": {k: v._asdict() for k, v in ASSET_BEHAVIOUR.items()},
        "LIABILITY_TYPE": LIABILITY_TYPE,
        "UNSECURED_DEBT": UNSECURED_DEBT,
        "POLICY_TYPE": POLICY_TYPE,
        "BENEFICIARY": BENEFICIARY,
        "DISABILITY_DEFINITION": DISABILITY_DEFINITION,
        "NEED_AREA": NEED_AREA,
        "ACTION": ACTION,
        "REPORT_STYLE": REPORT_STYLE,
        "SCOPE_AREA": SCOPE_AREA,
        "PROVINCES": PROVINCES,
        "TITLES": TITLES,
        "EDUCATION_LEVELS": EDUCATION_LEVELS,
    }
