"""Many documents at once (§12.7, "two uses"): ``analyse`` over K materialised draws of a profile, and the
p10/p50/p90 band of every need's shortfall across them ("life cover shortfall R2.1m, likely R1.6m–R2.9m")."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime

import numpy as np

from ..models.fna import FnaDocument
from ._js import Now
from .analysis import analyse
from .results import Analysis

__all__ = ["analyse_batch", "need_bands", "need_shortfalls"]


def analyse_batch(docs: Iterable[FnaDocument], *, today: date | datetime | None = None) -> list[Analysis]:
    """``analyse`` each document against one shared "today" (the current instant if None)."""
    now = Now.of(today)
    return [analyse(doc, today=now) for doc in docs]


def need_shortfalls(analysis: Analysis) -> dict[str, float]:
    """Every need's shortfall in one analysis, keyed ``"<person>.<area>"`` for personal needs (client, spouse)
    and by area for household needs; children and goals by their document ids.

    Income protection is a monthly benefit; retirement is the capital short at retirement (nominal); estate is
    the liquidity shortfall; ``education.<dependantId>`` is the shortfall in today's money."""
    out: dict[str, float] = {}
    for key, p in analysis.persons.items():
        out[f"{key}.life"] = p.death.shortfall
        out[f"{key}.disability"] = p.disability.shortfall
        out[f"{key}.income-protection"] = p.incomeProtection.shortfall
        out[f"{key}.severe-illness"] = p.severeIllness.shortfall
        out[f"{key}.funeral"] = p.funeral.shortfall
        out[f"{key}.estate"] = p.estate.liquidityShortfall
        out[f"{key}.retirement"] = p.retirement.shortfall
    out["emergency"] = analysis.emergency.shortfall
    out["education"] = analysis.educationTotal.shortfall
    for e in analysis.education:
        out[f"education.{e.dependantId}"] = e.shortfallToday
    for g in analysis.goals:
        out[f"goal.{g.id}"] = g.shortfall
    return out


def need_bands(analyses: Sequence[Analysis]) -> dict[str, dict[str, float]]:
    """p10, p50 and p90 of each need's shortfall over the analyses (linear interpolation between draws).

    A need present in only some draws (a spouse included in some materialisations, say) is summarised over
    the draws that have it."""
    columns: dict[str, list[float]] = {}
    for a in analyses:
        for k, v in need_shortfalls(a).items():
            columns.setdefault(k, []).append(v)
    bands: dict[str, dict[str, float]] = {}
    for k, values in columns.items():
        p10, p50, p90 = np.percentile(np.asarray(values, dtype=float), [10, 50, 90])
        bands[k] = {"p10": float(p10), "p50": float(p50), "p90": float(p90)}
    return bands
