"""Investor risk profile questionnaire: a port of ``lib/fna/riskProfile.ts`` (§12.7).

Assesses risk tolerance (willingness) and risk capacity (ability), as suitability under section 8 of the
FAIS General Code of Conduct requires. The profile is the lower of the two, so a client is never profiled
above their capacity to absorb losses. The eight questions are categorical slots for extraction (§1.5).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, NamedTuple

from .results import RiskCategory, RiskProfileResult

Dimension = Literal["tolerance", "capacity"]


class RiskOption(NamedTuple):
    label: str
    score: int


class RiskQuestion(NamedTuple):
    id: str
    dimension: Dimension
    question: str
    options: list[RiskOption]


def _options(*labels: str) -> list[RiskOption]:
    return [RiskOption(label, i + 1) for i, label in enumerate(labels)]


RISK_QUESTIONS: list[RiskQuestion] = [
    RiskQuestion(
        "horizon",
        "capacity",
        "When will you need to start drawing on the majority of this money?",
        _options("Within 1 year", "1 – 3 years", "3 – 5 years", "5 – 10 years", "More than 10 years"),
    ),
    RiskQuestion(
        "income-stability",
        "capacity",
        "How secure is your current and future income?",
        _options(
            "Very insecure / no regular income",
            "Somewhat insecure (commission, contract)",
            "Reasonably secure",
            "Secure",
            "Very secure, with other income sources",
        ),
    ),
    RiskQuestion(
        "emergency",
        "capacity",
        "If you lost your income, how long could you cover your expenses from savings (excluding retirement funds)?",
        _options("Less than 1 month", "1 – 3 months", "3 – 6 months", "6 – 12 months", "More than 12 months"),
    ),
    RiskQuestion(
        "proportion",
        "capacity",
        "What proportion of your total investable assets does this investment represent?",
        _options("More than 75%", "50% – 75%", "25% – 50%", "10% – 25%", "Less than 10%"),
    ),
    RiskQuestion(
        "knowledge",
        "tolerance",
        "How would you describe your investment knowledge and experience?",
        _options(
            "None — I have only used bank savings",
            "Limited — some unit trusts or a retirement annuity",
            "Moderate — I understand how markets and asset classes work",
            "Good — I actively follow and manage investments",
            "Advanced — professional or extensive experience",
        ),
    ),
    RiskQuestion(
        "objective",
        "tolerance",
        "Which statement best describes your main investment objective?",
        _options(
            "Protect my capital — I cannot accept losses",
            "Mostly stability, with some growth",
            "A balance of growth and stability",
            "Mostly growth, accepting short-term volatility",
            "Maximum long-term growth",
        ),
    ),
    RiskQuestion(
        "drawdown",
        "tolerance",
        "Your R100,000 investment falls to R80,000 in a market crash. What would you do?",
        _options(
            "Sell everything immediately",
            "Sell some to limit further losses",
            "Do nothing and wait for recovery",
            "Stay invested and review with my adviser",
            "Invest more while prices are low",
        ),
    ),
    RiskQuestion(
        "tradeoff",
        "tolerance",
        "Which range of one-year outcomes on R100,000 would you be most comfortable with?",
        _options("R99,000 to R107,000", "R94,000 to R112,000", "R88,000 to R118,000", "R80,000 to R126,000", "R70,000 to R140,000"),
    ),
]

RISK_CATEGORIES: list[RiskCategory] = [
    RiskCategory(
        key="conservative",
        label="Conservative",
        description="Capital preservation is the priority. Low tolerance for short-term losses.",
        equity="0 – 25% growth assets",
        horizon="1 – 3 years",
        target="CPI + 1% to 2%",
    ),
    RiskCategory(
        key="moderately-conservative",
        label="Cautious",
        description="Mostly stable returns with modest growth; limited short-term volatility.",
        equity="25 – 40% growth assets",
        horizon="3 years +",
        target="CPI + 2% to 3%",
    ),
    RiskCategory(
        key="moderate",
        label="Moderate",
        description="Balanced growth and stability; accepts periodic negative returns.",
        equity="40 – 60% growth assets",
        horizon="5 years +",
        target="CPI + 3% to 5%",
    ),
    RiskCategory(
        key="moderately-aggressive",
        label="Moderately aggressive",
        description="Long-term growth focus; comfortable with meaningful volatility.",
        equity="60 – 75% growth assets",
        horizon="7 years +",
        target="CPI + 5% to 6%",
    ),
    RiskCategory(
        key="aggressive",
        label="Aggressive",
        description="Maximum long-term growth; accepts large short-term drawdowns.",
        equity="75 – 100% growth assets",
        horizon="10 years +",
        target="CPI + 6% +",
    ),
]


def _category_index(average: float) -> int:
    if average < 1.8:
        return 0
    if average < 2.6:
        return 1
    if average < 3.4:
        return 2
    if average < 4.2:
        return 3
    return 4


def _option_score(q: RiskQuestion, answer: float) -> int | None:
    """``q.options[answer]?.score``: JavaScript indexes an array only with a whole number in range (-0 included)."""
    if answer != answer or answer in (float("inf"), float("-inf")) or answer != int(answer):
        return None
    i = int(answer)
    return q.options[i].score if 0 <= i < len(q.options) else None


def _scores(answers: Mapping[str, float], dimension: Dimension) -> tuple[list[int], int]:
    qs = [q for q in RISK_QUESTIONS if q.dimension == dimension]
    values = []
    for q in qs:
        if q.id in answers and answers[q.id] is not None:
            s = _option_score(q, answers[q.id])
            if s is not None:
                values.append(s)
    return values, len(qs)


def _average(values: list[int]) -> float:
    total = 0
    for v in values:
        total += v
    return total / len(values) if values else 0.0


def score_risk_profile(answers: Mapping[str, float]) -> RiskProfileResult:
    """``scoreRiskProfile``: tolerance and capacity categories, and the profile (the lower of the two)."""
    tol, tol_total = _scores(answers, "tolerance")
    cap, cap_total = _scores(answers, "capacity")
    ti = _category_index(_average(tol)) if tol else None
    ci = _category_index(_average(cap)) if cap else None
    answered = len(tol) + len(cap)
    total = tol_total + cap_total
    return RiskProfileResult.build(
        {
            "complete": answered == total,
            "answered": answered,
            "total": total,
            "toleranceScore": _average(tol),
            "capacityScore": _average(cap),
            "tolerance": RISK_CATEGORIES[ti] if ti is not None else None,
            "capacity": RISK_CATEGORIES[ci] if ci is not None else None,
            "profile": RISK_CATEGORIES[min(ti, ci)] if ti is not None and ci is not None else None,
            "mismatch": ti is not None and ci is not None and abs(ti - ci) >= 2,
        }
    )


def as_json() -> dict:
    """The questions and categories in the shape the TypeScript exports, for the parity check."""
    return {
        "RISK_QUESTIONS": [
            {"id": q.id, "dimension": q.dimension, "question": q.question, "options": [o._asdict() for o in q.options]}
            for q in RISK_QUESTIONS
        ],
        "RISK_CATEGORIES": [c.model_dump() for c in RISK_CATEGORIES],
    }
