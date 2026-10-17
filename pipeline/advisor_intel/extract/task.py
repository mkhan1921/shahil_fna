"""The extraction work behind the ``extract_slot_group`` tool (spec §7.2–7.5, §13.3).

objects → field statuses → values (validated, field-level retries) → span alignment → scale
resolution and normalisation → ops → the reducer. Returns the new profile and an
``ExtractionResult`` with the ops applied, new facts, tier A gaps, warnings, dropped extractions
and the sub-agents' usage (which the orchestrator's own usage limits cannot see).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..extract.reducer import ReducerResult, apply_state_ops, make_ops
from ..lexicon.loader import load_lexicon
from ..models.extraction import ExtractionResult, ExtractionTask
from ..models.facts import Provenance
from ..models.profile import Profile
from .align import align_values
from .context import ExtractionDeps, concrete_path
from .ops import FactDraft, draft_fact, field_problems, status_evidence, to_state_ops
from .subagent import run_stages

if TYPE_CHECKING:
    from ..harness.deps import HarnessDeps


def _assumptions(deps: HarnessDeps):
    base = deps.inputs.base_document
    return base.assumptions if base is not None else None


def _refers(fv) -> bool:
    """A value read relative to another fact ('a thirteenth cheque', 'in five years from my birthday')."""
    for h in fv.hypotheses:
        f = h.intermediate
        if getattr(f, "reference_slot", "") or getattr(f, "anchor", "call") not in ("", "call"):
            return True
    return False


def _merge(a: ReducerResult, b: ReducerResult) -> ReducerResult:
    return ReducerResult(profile=b.profile, applied=a.applied + b.applied, rejected=a.rejected + b.rejected,
                         touched=list(dict.fromkeys(a.touched + b.touched)))


async def extract_task(task: ExtractionTask, hdeps: HarnessDeps) -> tuple[Profile, ExtractionResult]:
    profile = hdeps.profile
    pins = hdeps.pins
    lex = load_lexicon(pins.lexicon_version)
    entities = {**hdeps.discourse.entities, **profile.entities}
    deps = ExtractionDeps(task=task, catalog=hdeps.catalog, profile=profile, transcript=hdeps.transcript, discourse=hdeps.discourse, pins=pins,
                          crm=hdeps.inputs.crm, glossary=hdeps.inputs.glossary, entities=entities)
    assumptions = _assumptions(hdeps)

    def validator(fv, slot, instance):
        # During stage 3 a field value is keyed by its fact id; numeric spans are matched later, at drafting.
        return field_problems(fv, slot, fv.fna_path, profile, lex, hdeps.transcript, [], assumptions)

    out = await run_stages(deps, validator=validator)
    # Rows found during the call join the registry (and the profile, so later tasks see them).
    if out.new_entities:
        profile = profile.model_copy(update={"entities": {**profile.entities, **out.new_entities}})
        deps.profile = profile
    keys = {}
    for k in out.statuses:
        slot_id, _, inst = k.partition("#")
        keys[k] = (hdeps.catalog.slot(slot_id), inst or None)
    slots = {k: keys[k][0] for k in out.values}
    aligned = await align_values(deps, out.values, {k: s.segment_ids for k, s in out.statuses.items()}, slots)
    rm_name = pins.reasoning_model
    provenance = Provenance(
        run_id=hdeps.inputs.run_id,
        extractor_model=rm_name,
        extractor_prompt_version=";".join(f"{k}={v}" for k, v in sorted(out.prompt_versions.items())),
        calibrator_version=pins.calibrator_version,
        lexicon_version=lex.version,
        schema_version=hdeps.catalog.version,
    )
    # Values read relative to another fact are drafted after the rest of the batch is applied, so a
    # 'thirteenth cheque' finds the gross income stated in the same task.
    later = {k for k, al in aligned.aligned.items() if _refers(al.value)}
    drafts = []
    for k, al in aligned.aligned.items():
        if k in later:
            continue
        slot, inst = keys[k]
        drafts.append(draft_fact(al, out.statuses[k], slot, k, inst, profile, lex, hdeps.transcript, assumptions=assumptions, retries=out.retries.get(k, 0)))
    for k, reason in out.failures.items():
        if k in aligned.aligned or k not in keys:
            continue
        slot, inst = keys[k]
        st = out.statuses.get(k)
        ev = status_evidence(hdeps.transcript, st.segment_ids if st else [])
        if not ev:
            continue  # nothing to show the reviewer: the gap goes through coverage instead
        drafts.append(FactDraft(key=k, slot=slot, instance=inst, fna_path=concrete_path(profile, slot, inst, deps.entities), status="contradicted", value=None,
                                hypotheses=[], hedges=[], evidence=ev, failure=reason, retries=out.retries.get(k, 0)))
    unknown = []
    for k, st in out.statuses.items():
        if st.status == "explicitly_unknown":
            slot, inst = keys[k]
            unknown.append((k, slot, inst, concrete_path(profile, slot, inst, deps.entities), status_evidence(hdeps.transcript, st.segment_ids)))
    mentions: dict[str, set[str]] = {}
    for m in hdeps.discourse.mentions:
        if m.entity_id:
            mentions.setdefault(m.segment_id, set()).add(m.entity_id)
    specs, dropped = to_state_ops(drafts, unknown, profile, provenance, mentions, lex)
    result = apply_state_ops(profile, make_ops(profile, specs))
    if later:
        mid = result.profile
        rel = []
        for k in later:
            slot, inst = keys[k]
            rel.append(draft_fact(aligned.aligned[k], out.statuses[k], slot, k, inst, mid, lex, hdeps.transcript, assumptions=assumptions,
                                  retries=out.retries.get(k, 0)))
        specs2, dropped2 = to_state_ops(rel, [], mid, provenance, mentions, lex)
        result = _merge(result, apply_state_ops(mid, make_ops(mid, specs2)))
        drafts += rel
        dropped += dropped2
    usage = out.usage.as_dict()
    usage["requests"] += aligned.requests
    usage["input_tokens"] += aligned.input_tokens
    usage["output_tokens"] += aligned.output_tokens
    gaps = []
    for s in deps.slots:
        if s.sensitivity_tier != "A":
            continue
        present = any(f.slot_id == s.slot_id and f.source != "prior_fna" and f.status in ("stated", "implied") for f in result.profile.facts.values())
        if not present:
            gaps.append(s.slot_id)
    return result.profile, ExtractionResult(
        task_id=task.task_id,
        ops_applied=len(result.applied),
        new_fact_ids=[f for f in result.touched if f in result.profile.facts],
        gaps=gaps,
        warnings=out.warnings + [f"{r.op.fact_id}: {r.reason}" for r in result.rejected] + [f"{k}: {v}" for k, v in out.failures.items()],
        dropped=[f"{k}: {v}" for k, v in aligned.dropped.items()] + dropped + [d for dr in drafts for d in dr.dropped],
        usage=usage,
    )
