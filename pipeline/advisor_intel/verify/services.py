"""Stage 5 behind the orchestrator's tools (spec §13.3), and after it (§9).

``verify_service`` and ``constraints_service`` return coroutines with the ``harness.HarnessServices``
signature; ``harness.services.default_services`` wires them when this package is installed. The tools read
the run's transcript, pins and catalog from the harness deps. The consistency probes come from an injected
``reextract`` or, by default, from the extraction stage's ``make_reextract`` bound to the run (imported
when first used, and skipped when the small role is a stub that would only return filler).

``verify_after_agent`` is Stage 5 after the orchestrator, for the in-process runner and the activity:
verify what the agent left unverified or changed since, the omission check with the extraction stage's
``make_reopen``, verify what it reopened, then constraints, features, calibration and auto-accept.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .. import llm
from ..config import Settings
from ..models.entities import DiscourseOutput
from ..models.profile import Profile
from ..models.runs import VersionPins
from ..models.transcript import Transcript
from ..schema.constraints import annotate_constraints, check_constraints
from ..schema.slots import Catalog
from .calibrator import Calibrator
from .conformal import ConformalThresholds
from .consistency import Reextract
from .coverage import Reopen
from .minicheck import GroundingScorer
from .run import Stage5Result, verify_facts, verify_stage

PROBE_ROLES = {"reextract": "small", "reopen": "reasoning"}  # the roles extract.probes runs its value stage on


def probes_run(pins: VersionPins | None, kind: str) -> bool:
    """Whether a probe's model is real: a stub would answer with schema-valid filler (``llm.is_stub``)."""
    return not llm.is_stub(llm.role_model_name(PROBE_ROLES[kind], pins))  # type: ignore[arg-type]


def _probe_context(*, catalog: Catalog, transcript: Transcript, discourse: DiscourseOutput, pins: VersionPins, run_id: str) -> Any:
    from ..extract.probes import ProbeContext

    return ProbeContext(catalog=catalog, transcript=transcript, discourse=discourse, pins=pins, run_id=run_id)


def probe_reextract(deps: Any) -> Reextract | None:
    """The extraction stage's consistency-probe callable, bound to this run (§9.1b); None on a stub small model."""
    if not probes_run(deps.pins, "reextract"):
        return None
    from ..extract.probes import make_reextract

    ctx = _probe_context(
        catalog=deps.catalog, transcript=deps.transcript, discourse=deps.discourse, pins=deps.pins, run_id=deps.inputs.run_id
    )
    return make_reextract(ctx, lambda: deps.profile)


def verify_service(
    *,
    reextract: Reextract | None = None,
    reextract_factory: Callable[[Any], Reextract | None] | None = probe_reextract,
    minicheck: GroundingScorer | None = None,
) -> Callable[..., Any]:
    """The ``verify_facts`` tool's service: entailment, consistency and the verifier's corrective ops."""

    async def verify(profile: Profile, fact_ids: list[str], deps: Any) -> Any:
        from ..harness.deps import ServiceResult

        rx = reextract if reextract is not None else (reextract_factory(deps) if reextract_factory is not None else None)
        res = await verify_facts(
            profile, fact_ids, transcript=deps.transcript, pins=deps.pins, catalog=deps.catalog, reextract=rx, minicheck=minicheck
        )
        data = {
            "verified": len(res.results),
            "results": res.results,
            "retracted": [
                {"fact_id": op.fact_id, "hypothesis": op.payload.get("hypothesis_index"), "note": op.payload.get("note", "")}
                for op in res.ops
            ],
            "rejected": [{"fact_id": r.op.fact_id, "reason": r.reason} for r in res.rejected],
        }
        return ServiceResult(profile=res.profile, data=data, usage=dict(res.usage))

    return verify


def constraints_service() -> Callable[..., Any]:
    """The ``check_constraints`` tool's service: every rule, with hard failures marked on the facts (§9.1c)."""

    async def constraints(profile: Profile, deps: Any) -> Any:
        from ..harness.deps import ServiceResult

        violations = check_constraints(profile, deps.catalog)
        return ServiceResult(profile=annotate_constraints(profile, violations), data=[v.model_dump(mode="json") for v in violations])

    return constraints


async def verify_after_agent(
    profile: Profile,
    *,
    transcript: Transcript,
    discourse: DiscourseOutput,
    pins: VersionPins,
    catalog: Catalog,
    run_id: str,
    calibrator: Calibrator | None = None,
    conformal: ConformalThresholds | None = None,
    settings: Settings | None = None,
    minicheck: GroundingScorer | None = None,
    reextract: Reextract | None = None,
    reopen: Reopen | None = None,
) -> Stage5Result:
    """Stage 5 after the orchestrator (§9): the remaining checks, the omission check, then scoring.

    ``reextract`` and ``reopen`` default to the extraction stage's probes bound to this run (each skipped
    when its role is a stub). They read the profile through a live cell that ``verify_stage`` updates after
    every change, so a field the omission check reopens is extracted against the profile as it stands then
    (after the verifier's retractions and earlier reopenings), not the one this function was given.
    ``calibrator`` defaults to the cold-start identity; without ``conformal`` thresholds nothing is auto-accepted.
    """
    live: dict[str, Profile] = {"profile": profile}
    if (reextract is None and probes_run(pins, "reextract")) or (reopen is None and probes_run(pins, "reopen")):
        from ..extract.probes import make_reextract, make_reopen

        ctx = _probe_context(catalog=catalog, transcript=transcript, discourse=discourse, pins=pins, run_id=run_id)
        if reextract is None and probes_run(pins, "reextract"):
            reextract = make_reextract(ctx, lambda: live["profile"])
        if reopen is None and probes_run(pins, "reopen"):
            reopen = make_reopen(ctx, lambda: live["profile"])
    return await verify_stage(
        profile,
        transcript=transcript,
        pins=pins,
        catalog=catalog,
        reextract=reextract,
        reopen=reopen,
        calibrator=calibrator,
        conformal=conformal,
        settings=settings,
        minicheck=minicheck,
        discourse=discourse,
        on_profile=lambda p: live.__setitem__("profile", p),
    )
