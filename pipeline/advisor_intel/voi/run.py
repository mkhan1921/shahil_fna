"""``run_voi_and_followups`` — Stage 6 for one call (spec §10, §13.1).

Sensitivity runs (PoS through the injected ``pos_runs``, FNA through the injected ``analyse``) → priorities
and the advisor's ``why`` lines on each fact → the §10.4 action per fact → follow-up questions for the
client (at most 8) → blocking flags. Facts are only annotated (``reducer.annotate``); the follow-ups and
flags are written to ``Profile.open_questions`` and ``Profile.blocking_flags``, keeping follow-ups already
asked or answered and every flag decision the reviewer made. ``VoiOutput.followups`` and ``.flags`` are
exactly those two lists (so a caller may write them back); ``.drafted`` holds this round's new questions.
A view that failed (the PoS runs, the FNA analysis) leaves its terms at 0 and is named in ``.errors``, as
is a drafting failure that fell back to template questions: a zero from a failure is never silent.

The in-process runner and the activity call it as ``await run_voi_and_followups(profile, catalog=…,
pins=…, pos_runs=forecast.sensitivity.sensitivity_runs, analyse=fna_engine.analysis.analyse,
transcript=…, settings=…, base_document=<prior FnaDocument>, seed=<per-run seed>)``; ``analyse`` is
bound to the call's anchor date unless the caller bound ``today`` itself.

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
from .sensitivity import N_PATHS, Analyse, Extremes, PosRuns, SensitivityRow, bind_today, get_field, sensitivity

log = logging.getLogger(__name__)


class VoiOutput(BaseModel):
    profile: Profile
    followups: list[FollowUp] = []  # = profile.open_questions: earlier asked/answered ones, then this round's
    drafted: list[FollowUp] = []  # this round's new questions (status "open")
    flags: list[BlockingFlag] = []  # = profile.blocking_flags
    priorities: list[PriorityItem] = []
    sensitivity: list[SensitivityRow] = []
    decisions: dict[str, ClarifyDecision] = {}
    review_list: list[str] = []  # the top of the review rail
    drafted_by: str = "none"
    usage: dict[str, int] = {}
    errors: list[str] = []  # a view or the drafting that failed: its terms are 0 (or templates were used) because it did not run


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
    analyse = bind_today(analyse, profile)
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
    errors = sorted({r.error for r in rows if r.error})
    if drafted.error:
        errors.append(f"follow-up drafting: {drafted.error} (template questions used)")

    kept = [q for q in profile.open_questions if q.status in ("asked", "answered")]
    kept_ids = {q.followup_id for q in kept}
    questions = kept + [f for f in drafted.followups if f.followup_id not in kept_ids]

    violations = check_constraints(profile, catalog, net_income_monthly=household_take_home(analyse, profile, base_document, catalog))
    fresh = compute_flags(profile, catalog=catalog, transcript=transcript, sensitivity=rows, violations=violations)
    flags = merge_flags(profile.blocking_flags, fresh, profile)
    profile = placeholders(profile, catalog, [f.fact_id for f in flags if f.fact_id] + [fid for q in questions for fid in q.fact_ids])
    profile = annotate_priorities(profile, items)  # again: the placeholders now carry their rows' priority and why line
    profile = profile.model_copy(update={"open_questions": questions, "blocking_flags": flags})
    if errors:
        log.warning("stage 6 on call %s: %s", profile.call_id, "; ".join(errors))
    return VoiOutput(
        profile=profile,
        followups=questions,
        drafted=drafted.followups,
        flags=flags,
        priorities=items,
        sensitivity=rows,
        decisions=decisions,
        review_list=review_list(items, top),
        drafted_by=drafted.drafted_by,
        usage=drafted.usage,
        errors=errors,
    )


def placeholders(profile: Profile, catalog: Catalog, fact_ids: list[str]) -> Profile:
    """Expected fields a flag or a follow-up names but no fact holds get one: not discussed, no value, a seed.

    The review places every flag and question on its field in the client file (the sidecar's concrete path),
    so a missing tier A field needs a fact there for the adviser to fill in, mark unknown or dismiss. A later
    statement or reviewer edit replaces the seed like any other. Like every other seed (``score_profile``), a
    placeholder is not an extraction: it is scored ``p_correct = 1`` and never auto-accepted, rather than
    left at the model's default, which would read as a real 50% score in the review.
    """
    from ..extract.context import concrete_path
    from ..extract.reducer import annotate, apply_state_ops, make_ops
    from ..models.facts import Provenance

    specs = []
    for fid in dict.fromkeys(fact_ids):
        if fid in profile.facts:
            continue
        slot_id, _, inst = fid.partition("#")
        try:
            slot = catalog.slot(slot_id)
        except (KeyError, ValueError):
            log.warning("flag or follow-up names unknown slot %s", slot_id)
            continue
        instance = inst or None
        specs.append(("ADD", fid, None, {"slot_id": slot.slot_id, "instance_key": instance, "fna_path": concrete_path(profile, slot, instance),
                                         "source": "fna_default", "status": "not_discussed",
                                         "provenance": Provenance(run_id=profile.run_id, schema_version=catalog.version)}, "loader"))
    if not specs:
        return profile
    res = apply_state_ops(profile, make_ops(profile, specs))
    for r in res.rejected:
        log.warning("placeholder for %s rejected: %s", r.op.fact_id, r.reason)
    out = res.profile
    for op in res.applied:
        out = annotate(out, op.fact_id, p_correct=1.0, p_raw=1.0, auto_accepted=False)
    return out
