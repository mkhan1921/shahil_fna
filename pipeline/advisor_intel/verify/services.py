"""Stage 5 behind the orchestrator's tools (spec §13.3): ``verify_facts`` and ``check_constraints``.

Each factory returns a coroutine with the ``harness.HarnessServices`` signature, ready for
``harness.services.default_services(verify=…, constraints=…)``. The tools read the run's transcript,
pins and catalog from the harness deps; the consistency probes come from an injected ``reextract`` or,
by default, from the extraction stage's ``make_reextract`` bound to the run (imported when first used).
"""

from __future__ import annotations

from typing import Any, Callable

from ..models.profile import Profile
from ..schema.constraints import annotate_constraints, check_constraints
from .consistency import Reextract
from .minicheck import GroundingScorer
from .run import verify_facts


def probe_reextract(deps: Any) -> Reextract:
    """The extraction stage's consistency-probe callable, bound to this run (§9.1b)."""
    from ..extract.probes import ProbeContext, make_reextract

    ctx = ProbeContext(
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
