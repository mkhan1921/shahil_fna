"""Stage 6 behind the orchestrator's tools (spec §13.3): ``run_sensitivity`` and ``draft_followups``.

Factories returning coroutines with the ``harness.HarnessServices`` signature, ready for
``harness.services.default_services(sensitivity=…, followups=…)``. ``run_sensitivity`` ranks facts and
writes their priorities and ``why`` lines; its table (``PriorityItem`` dicts) is what the harness keeps in
its state, and ``draft_followups`` reads it back to decide the actions and draft the questions. The
``run_voi_and_followups`` activity after the agent recomputes everything, flags included.

``analyse`` may take a ``today`` keyword (the FNA engine's ``analyse(doc, today=…)``): it is then bound to
the call's anchor date. ``pos_runs`` (the forecast's ``sensitivity_runs``) gets the run's prior FNA as
``base_document``.
"""

from __future__ import annotations

from typing import Any, Callable

from ..models.profile import Profile
from .clarify import clarify_all
from .followups import draft_followups
from .priority import PriorityItem, annotate_priorities, prioritise
from .sensitivity import PosRuns, bind_today, sensitivity

__all__ = ["bind_today", "followups_service", "sensitivity_service"]


def sensitivity_service(*, pos_runs: PosRuns | None, analyse: Callable[..., Any] | None) -> Callable[..., Any]:
    """The ``run_sensitivity`` tool's service: the §10.2 runs and the §10.3 priorities."""

    async def run_sensitivity(profile: Profile, deps: Any) -> Any:
        from ..harness.deps import ServiceResult

        rows = await sensitivity(
            profile,
            catalog=deps.catalog,
            analyse=bind_today(analyse, profile),
            pos_runs=pos_runs,
            base_document=getattr(deps.inputs, "base_document", None),
        )
        items = prioritise(profile, rows, catalog=deps.catalog)
        return ServiceResult(profile=annotate_priorities(profile, items), data=[it.model_dump(mode="json") for it in items])

    return run_sensitivity


def followups_service(*, pos_runs: PosRuns | None = None, analyse: Callable[..., Any] | None = None) -> Callable[..., Any]:
    """The ``draft_followups`` tool's service: the §10.4 actions and the §10.5 questions (at most 8)."""

    async def followups(profile: Profile, deps: Any) -> Any:
        from ..harness.deps import ServiceResult

        table = list(getattr(getattr(deps, "state", None), "sensitivity", None) or [])
        if table:
            items = [PriorityItem.model_validate(row) for row in table]
        else:  # run_sensitivity was not called: rank first
            rows = await sensitivity(
                profile,
                catalog=deps.catalog,
                analyse=bind_today(analyse, profile),
                pos_runs=pos_runs,
                base_document=getattr(deps.inputs, "base_document", None),
            )
            items = prioritise(profile, rows, catalog=deps.catalog)
        decisions = clarify_all(profile, items)
        drafted = await draft_followups(profile, decisions, items, catalog=deps.catalog, pins=deps.pins)
        kept = [q for q in profile.open_questions if q.status in ("asked", "answered")]
        kept_ids = {q.followup_id for q in kept}
        updated = profile.model_copy(update={"open_questions": kept + [f for f in drafted.followups if f.followup_id not in kept_ids]})
        return ServiceResult(profile=updated, data=drafted.followups, usage=dict(drafted.usage))

    return followups
