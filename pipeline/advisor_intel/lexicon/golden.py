"""Golden lexicon cases (spec §8.4): phrase → expected distribution summary (median, p10, p90).

``tests/lexicon_cases.yaml`` holds at least 200 cases. CI fails when a lexicon change moves any
case's median by more than 10% without the case being updated; ``propose_lexicon_fit`` uses the
same check to list the cases its proposal would move, so the pull request says which to update.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from .. import stats
from .loader import Lexicon, Lookup

CaseKind = Literal["approx", "vague_count", "vague_amount", "age_band", "relative", "rate", "likelihood", "certainty", "hedge", "scale"]
MEDIAN_REL_TOL = 0.10  # §8.4
ABS_FLOOR: dict[str, float] = {"rate": 0.002, "likelihood": 0.01, "certainty": 0.01, "hedge": 0.01, "relative": 0.005}


class GoldenCase(BaseModel):
    id: str
    kind: CaseKind
    phrase: str
    value: float | None = None  # the stated number, for approx words
    match: Literal["exact", "near_miss", "miss", "default"] = "exact"
    median: float
    p10: float
    p90: float


def lookup(lex: Lexicon, kind: str, phrase: str, value: float | None = None) -> Lookup:
    if kind == "approx":
        return lex.approx(phrase, float(value if value is not None else 1.0))
    if kind == "certainty":
        return lex.certainty(phrase)
    if kind == "hedge":
        return lex.hedge_lookup(phrase)
    return getattr(lex, kind)(phrase)


def summarise(lk: Lookup, kind: str) -> dict[str, float] | None:
    """(median, p10, p90) of a lookup: a distribution's quantiles; a likelihood's mean, low, high; a scalar three times."""
    if kind == "likelihood":
        if lk.likelihood is None:
            return None
        return {"median": lk.likelihood.mean, "p10": lk.likelihood.low, "p90": lk.likelihood.high}
    if kind in ("certainty", "hedge", "scale"):
        if lk.value is None:
            return None
        return {"median": float(lk.value), "p10": float(lk.value), "p90": float(lk.value)}
    if lk.dist is None:
        return None
    s = stats.summary(lk.dist)
    return {"median": s["p50"], "p10": s["p10"], "p90": s["p90"]}


def load_cases(path: Path | str) -> list[GoldenCase]:
    data = yaml.safe_load(Path(path).read_text())
    return [GoldenCase.model_validate(c) for c in data["cases"]]


def check_case(lex: Lexicon, case: GoldenCase, rel_tol: float = MEDIAN_REL_TOL) -> str | None:
    """None when the case holds; otherwise why it fails (a lost key, or a median moved by more than ``rel_tol``)."""
    lk = lookup(lex, case.kind, case.phrase, case.value)
    if lk.match != case.match:
        return f"{case.id}: {case.kind} {case.phrase!r} now resolves as {lk.match} (key {lk.key!r}), golden says {case.match}"
    got = summarise(lk, case.kind)
    if got is None:
        return f"{case.id}: {case.kind} {case.phrase!r} has no value"
    tol = max(rel_tol * abs(case.median), ABS_FLOOR.get(case.kind, 1e-9))
    if not math.isfinite(got["median"]) or abs(got["median"] - case.median) > tol:
        return f"{case.id}: {case.kind} {case.phrase!r} median {got['median']:.6g} vs golden {case.median:.6g} (tolerance {tol:.3g})"
    return None


def check_cases(lex: Lexicon, cases: Sequence[GoldenCase], rel_tol: float = MEDIAN_REL_TOL) -> list[str]:
    return [msg for c in cases if (msg := check_case(lex, c, rel_tol)) is not None]


def make_case(lex: Lexicon, id: str, kind: str, phrase: str, value: float | None = None, digits: int = 6) -> dict[str, Any]:
    """A golden case from the current lexicon (for adding or updating cases after review)."""
    lk = lookup(lex, kind, phrase, value)
    s = summarise(lk, kind)
    if s is None:
        raise ValueError(f"{kind} {phrase!r} has no value in lexicon {lex.version}")
    case: dict[str, Any] = {"id": id, "kind": kind, "phrase": phrase}
    if value is not None:
        case["value"] = value
    case["match"] = lk.match
    case.update({k: float(f"{v:.{digits}g}") for k, v in s.items()})
    return case


__all__ = ["ABS_FLOOR", "MEDIAN_REL_TOL", "GoldenCase", "check_case", "check_cases", "load_cases", "lookup", "make_case", "summarise"]
