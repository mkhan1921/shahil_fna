"""The Shahil FNA engine ported to Python (spec §12.7), with the TypeScript as the reference.

``analyse(doc)`` gives the numbers the adviser sees in the app; ``analyse_batch`` and ``need_bands`` turn
materialised draws into shortfall bands; :mod:`.tax` is the one tax implementation the Monte Carlo layer
uses (§12.3). SARS values come from the shared ``fna_tables/`` files (:mod:`.tables`).
"""

from .analysis import FALLBACK_AGE, analyse, compute_education, compute_estate, compute_income, compute_retirement, living_expenses
from .batch import analyse_batch, need_bands, need_shortfalls
from .id_number import age_on, exact_age, parse_id, parse_sa_id
from .recommend import draft_recommendations
from .results import (
    Analysis,
    Cashflow,
    EducationResult,
    EstateResult,
    Finding,
    GoalResult,
    Line,
    NeedResult,
    NetWorth,
    PersonAnalysis,
    PersonIncome,
    PersonTaxResult,
    Ratios,
    RetirementResult,
    RetirementYear,
    RiskProfileResult,
)
from .tables import (
    TableError,
    TaxTable,
    TaxTableFile,
    available_tax_years,
    get_table,
    indexed_table,
    latest_tax_year,
    table_for,
    table_for_year,
)

__all__ = [
    "FALLBACK_AGE",
    "Analysis",
    "Cashflow",
    "EducationResult",
    "EstateResult",
    "Finding",
    "GoalResult",
    "Line",
    "NeedResult",
    "NetWorth",
    "PersonAnalysis",
    "PersonIncome",
    "PersonTaxResult",
    "Ratios",
    "RetirementResult",
    "RetirementYear",
    "RiskProfileResult",
    "TableError",
    "TaxTable",
    "TaxTableFile",
    "age_on",
    "analyse",
    "analyse_batch",
    "available_tax_years",
    "compute_education",
    "compute_estate",
    "compute_income",
    "compute_retirement",
    "draft_recommendations",
    "exact_age",
    "get_table",
    "indexed_table",
    "latest_tax_year",
    "living_expenses",
    "need_bands",
    "need_shortfalls",
    "parse_id",
    "parse_sa_id",
    "table_for",
    "table_for_year",
]
