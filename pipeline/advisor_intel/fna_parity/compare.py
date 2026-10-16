"""Compare a TypeScript result with the Python one (§12.7): the same keys, strings, statuses and booleans
exactly, money to within R1 and ratios, percentages, ages and years to within 1e-6."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

MONEY_TOLERANCE = 1.0
RATIO_TOLERANCE = 1e-6

# Fields that are not rand amounts: ratios, rates, percentages, scores, ages, years and counts.
NOT_MONEY = frozenset(
    {
        "funded",
        "rate",
        "inclusionRate",
        "rateLow",
        "rateHigh",
        "marginalRate",
        "effectiveRate",
        "debtToIncome",
        "unsecuredDebtToIncome",
        "housingToIncome",
        "savingsRate",
        "premiumsToIncome",
        "emergencyMonths",
        "replacementRatio",
        "targetReplacementRatio",
        "initialDrawdown",
        "completenessScore",
        "toleranceScore",
        "capacityScore",
        "premiumEscalation",
        "age",
        "taxAge",
        "exactAge",
        "currentAge",
        "retirementAge",
        "planningAge",
        "depletionAge",
        "yearsToRetirement",
        "yearsInRetirement",
        "yearsToTertiary",
        "years",
        "year",
        "deathIncomeYears",
        "answered",
        "total",
        "schemaVersion",
    }
)


@dataclass
class Report:
    """Differences found, and the largest gap seen in each kind of number."""

    diffs: list[str] = field(default_factory=list)
    max_money: float = 0.0
    max_ratio: float = 0.0
    numbers: int = 0

    @property
    def ok(self) -> bool:
        return not self.diffs

    def merge(self, other: Report) -> None:
        self.diffs.extend(other.diffs)
        self.max_money = max(self.max_money, other.max_money)
        self.max_ratio = max(self.max_ratio, other.max_ratio)
        self.numbers += other.numbers


def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def compare(
    ts: Any, py: Any, *, ignore: frozenset[str] = frozenset(), path: str = "$", key: str = "", report: Report | None = None
) -> Report:
    """Walk both JSON values; ``ignore`` names keys whose values are not compared (they must still be present)."""
    r = report if report is not None else Report()
    if isinstance(ts, dict) and isinstance(py, dict):
        if ts.keys() != py.keys():
            missing, extra = sorted(ts.keys() - py.keys()), sorted(py.keys() - ts.keys())
            r.diffs.append(f"{path}: keys differ (missing in Python {missing}, extra in Python {extra})")
        for k in ts.keys() & py.keys():
            if k not in ignore:
                compare(ts[k], py[k], ignore=ignore, path=f"{path}.{k}", key=k, report=r)
        return r
    if isinstance(ts, list) and isinstance(py, list):
        if len(ts) != len(py):
            r.diffs.append(f"{path}: {len(ts)} items in TypeScript, {len(py)} in Python")
        for i, (a, b) in enumerate(zip(ts, py)):
            compare(a, b, ignore=ignore, path=f"{path}[{i}]", key=key, report=r)
        return r
    if _is_number(ts) and _is_number(py):
        r.numbers += 1
        a, b = float(ts), float(py)
        if math.isnan(a) or math.isnan(b) or math.isinf(a) or math.isinf(b):
            if not (a == b or (math.isnan(a) and math.isnan(b))):
                r.diffs.append(f"{path}: {ts!r} vs {py!r}")
            return r
        gap = abs(a - b)
        if key in NOT_MONEY:
            r.max_ratio = max(r.max_ratio, gap)
            if gap > RATIO_TOLERANCE:
                r.diffs.append(f"{path}: {ts!r} vs {py!r} (differs by {gap:.3g})")
        else:
            r.max_money = max(r.max_money, gap)
            if gap > MONEY_TOLERANCE:
                r.diffs.append(f"{path}: R{ts!r} vs R{py!r} (differs by R{gap:.3g})")
        return r
    if type(ts) is not type(py) or ts != py:
        r.diffs.append(f"{path}: {ts!r} vs {py!r}")
    return r
