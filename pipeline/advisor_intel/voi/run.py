"""``run_voi_and_followups`` — Stage 6 for one call (spec §10, §13.1).

Sensitivity runs (PoS through the injected ``pos_runs``, FNA through the injected ``analyse``) → priorities
and the advisor's ``why`` lines on each fact → the §10.4 action per fact → follow-up questions for the
client (at most 8) → blocking flags. Facts are only annotated (``reducer.annotate``); the follow-ups and
flags are written to ``Profile.open_questions`` and ``Profile.blocking_flags``, keeping follow-ups already
asked or answered and every flag decision the reviewer made.

Weights, the collapse threshold and the caps are the spec defaults (w_c = 1, w_e = 2, 0.005, 8 follow-ups,
top 15 review items). A deployment can override them through attributes on ``settings``
(``voi_w_content``, ``voi_w_extract``, ``voi_threshold``, ``max_followups``, ``review_top``).
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from ..config import Settings, get_settings
from ..models.fna import FnaDocument
from ..models.profile import BlockingFlag, FollowUp, Profile
from ..models.runs import VersionPins
from ..models.transcript import Transcript
from ..schema.constraints import check_constraints
from ..schema.slots import Catalog, load_catalog
from .clarify import ClarifyDecision, clarify_all
from .flags import compute_flags, merge_flags
from .followups import MAX_QUESTIONS, draft_followups
from .priority import COLLAPSE_BELOW, REVIEW_TOP, W_CONTENT, W_EXTRACT, PriorityItem, annotate_priorities, prioritise, review_list
from .sensitivity import N_PATHS, Analyse, Extremes, PosRuns, SensitivityRow, get_field, sensitivity

log = logging.getLogger(__name__)


class VoiOutput(BaseModel):
    profile: Profile
    followups: list[FollowUp] = []
    flags: list[BlockingFlag] = []
    priorities: list[PriorityItem] = []
    sensitivity: list[SensitivityRow] = []
    decisions: dict[str, ClarifyDecision] = {}
    review_list: list[str] = []  # the top of the review rail
    drafted_by: str = "none"
    usage: dict[str, int] = {}


def _setting(settings: Settings, name: str, default: Any) -> Any:
    v = getattr(settings, name, None)
    return default if v is None else v


def household_take_home(analyse: Analyse | None, profile: Profile, base: FnaDocument | None, catalog: Catalog) -> float | None:
    """The FNA engine's monthly take-home for the median document: the net income the budget rule wants."""
    if analyse is None:
        return None
    from ..binding import materialise

    try:
        cash = get_field(analyse(materialise(profile, "median", base=base, catalog=catalog)), "cashflow")
        v = get_field(cash, "takeHome")
        return float(v) if v is not None and float(v) > 0 else None
    except Exception as e:  # noqa: BLE001 - the budget rule falls back to gross income
        log.warning("take-home for the budget rule failed: %s", e)
        return None


async def run_voi_and_followups(
    profile: Profile,
    *,
    catalog: Catalog | None = None,
    pins: VersionPins | None = None,
    pos_runs: PosRuns | None,
    analyse: Analyse | None,
    transcript: Transcript | None = None,
    settings: Settings | None = None,
    base_document: FnaDocument | None = None,
    extremes: Extremes | None = None,
    n_paths: int = N_PATHS,
    seed: int = 0,
) -> VoiOutput:
    """Stage 6 for one call (see the module docstring)."""
    settings = settings or get_settings()
    catalog = catalog or load_catalog(profile.schema_version)
    w_c = float(_setting(settings, "voi_w_content", W_CONTENT))
    w_e = float(_setting(settings, "voi_w_extract", W_EXTRACT))
    threshold = float(_setting(settings, "voi_threshold", COLLAPSE_BELOW))
    max_q = int(_setting(settings, "max_followups", MAX_QUESTIONS))
    top = int(_setting(settings, "review_top", REVIEW_TOP))

    rows = await sensitivity(
        profile,
        catalog=catalog,
        analyse=analyse,
        pos_runs=pos_runs,
        base_document=base_document,
        extremes=extremes,
        n_paths=n_paths,
        seed=seed,
    )
    items = prioritise(profile, rows, catalog=catalog, w_content=w_c, w_extract=w_e, collapse_below=threshold)
    profile = annotate_priorities(profile, items)
    decisions = clarify_all(profile, items, threshold=threshold)
    drafted = await draft_followups(profile, decisions, items, catalog=catalog, pins=pins, max_questions=max_q)

    kept = [q for q in profile.open_questions if q.status in ("asked", "answered")]
    kept_ids = {q.followup_id for q in kept}
    questions = kept + [f for f in drafted.followups if f.followup_id not in kept_ids]

    violations = check_constraints(profile, catalog, net_income_monthly=household_take_home(analyse, profile, base_document, catalog))
    fresh = compute_flags(profile, catalog=catalog, transcript=transcript, sensitivity=rows, violations=violations)
    flags = merge_flags(profile.blocking_flags, fresh, profile)
    profile = profile.model_copy(update={"open_questions": questions, "blocking_flags": flags})
    return VoiOutput(
        profile=profile,
        followups=drafted.followups,
        flags=flags,
        priorities=items,
        sensitivity=rows,
        decisions=decisions,
        review_list=review_list(items, top),
        drafted_by=drafted.drafted_by,
        usage=drafted.usage,
    )
