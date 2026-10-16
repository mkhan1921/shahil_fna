"""Stage 5 entry points: the ``verify_facts`` tool, the omission check and ``score_profile`` (spec §9, §13.3).

- ``verify_facts(profile, fact_ids, …)`` runs the entailment check (with the MiniCheck pre-filter when a
  scorer is given) and the consistency probes, writes their results into each fact's ``verification``,
  and applies the verifier's corrective ``RETRACT`` ops through the reducer.
- ``check_coverage`` (re-exported from ``coverage``) is the omission check of §9.1d.
- ``score_profile(profile, calibrator, conformal, settings, …)`` checks constraints, builds every fact's
  feature vector, turns it into ``p_raw`` and ``p_correct`` with the calibrator and decides auto-accept with
  the conformal thresholds. Scores are annotations (``reducer.annotate``); values never change here.
- ``verify_stage`` chains them for the activity: verify, omission check, verify what it reopened, score.
  After the orchestrator it verifies only facts the checks have not seen in their current form: each
  verified fact records a fingerprint of its status and hypotheses (``verification["checked_fingerprint"]``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel

from ..config import Settings, get_settings
from ..extract.reducer import RejectedOp, annotate, apply_state_ops, make_ops
from ..models.entities import DiscourseOutput
from ..models.facts import Fact
from ..models.profile import Profile, StateOp
from ..models.runs import VersionPins
from ..models.transcript import Transcript
from ..schema.constraints import annotate_constraints, check_constraints
from ..schema.slots import Catalog, load_catalog
from .calibrator import Calibrator
from .conformal import ConformalThresholds, auto_accept_decision
from .consistency import K_PROBES, ConsistencyResult, Reextract, run_consistency
from .coverage import CoverageResult, Reopen, check_coverage
from .entailment import EntailmentResult, FactEntailment, check_entailment, corrective_ops
from .features import EvidenceIndex, features, to_storable
from .minicheck import GroundingScorer

log = logging.getLogger(__name__)

EXTRACTED_SOURCES = frozenset({"call", "derived", "followup"})
TRUSTED_SOURCES = frozenset({"prior_fna", "crm", "fna_default", "reviewer"})
QUALITY_CAP = 0.9  # §5.5: a call failing the ASR quality checks caps p_correct (and so auto-accept)
# Entailment "errors" that mean there was nothing to check (a fact without a value or without transcript evidence):
# the fact counts as checked in its current form, unlike a provider failure, which is retried on the next pass.
NOTHING_TO_CHECK = frozenset({"no claims", "no evidence segments in the transcript"})

__all__ = [
    "Stage5Result",
    "VerifyResult",
    "check_coverage",
    "fingerprint",
    "needs_verification",
    "score_profile",
    "verify_facts",
    "verify_stage",
]


def fingerprint(fact: Fact) -> str:
    """The status and hypotheses the checks saw: a later change to either makes the verification stale."""
    hyps = [[h.value.model_dump(mode="json") if h.value is not None else None, round(h.weight, 6)] for h in fact.hypotheses]
    return hashlib.sha1(json.dumps([fact.status, hyps], sort_keys=True, default=str).encode()).hexdigest()[:16]


def needs_verification(fact: Fact) -> bool:
    """An extracted fact the checks have not seen in its current form (§9.1)."""
    return fact.source in EXTRACTED_SOURCES and fact.verification.get("checked_fingerprint") != fingerprint(fact)


class VerifyResult(BaseModel):
    profile: Profile
    ops: list[StateOp] = []  # the verifier's corrective ops that the reducer applied
    rejected: list[RejectedOp] = []
    results: dict[str, dict[str, Any]] = {}  # fact id → a compact summary for the orchestrator
    usage: dict[str, int] = {}  # requests, tokens and ``failures`` (verifier calls and probes that failed)
    errors: list[str] = []  # "fact id: what failed", for facts whose checks did not run because of a failure


def _surviving(fe: FactEntailment, retracted: set[int], weights: list[float]) -> FactEntailment:
    """The entailment record after retracting hypotheses: remaining checks re-indexed and re-weighted.

    ``weights`` are the fact's hypothesis weights after the reducer removed ``retracted`` (original indices)
    and renormalised; the fact had ``len(weights) + len(retracted)`` hypotheses before.
    """
    remaining = [i for i in range(len(weights) + len(retracted)) if i not in retracted]
    new_pos = {orig: pos for pos, orig in enumerate(remaining)}
    checks = [
        c.model_copy(update={"index": new_pos[c.index], "weight": weights[new_pos[c.index]]}) for c in fe.checks if c.index in new_pos
    ]
    pairs = [(c.weight, c.entail_score) for c in checks if c.entail_score is not None]
    total = sum(w for w, _ in pairs)
    return fe.model_copy(update={"checks": checks, "entail_score": sum(w * s for w, s in pairs) / total if total > 0 else None})


def _summary(fid: str, fe: FactEntailment | None, cr: ConsistencyResult | None, gone: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"removed": gone}
    if fe is not None:
        out.update({"entailment": fe.status, "entail_score": fe.entail_score, "contradicted_hypotheses": fe.contradicted})
        if fe.error:
            out["note"] = fe.error
    if cr is not None:
        out.update({"consistency": cr.consistency})
    return out


async def verify_facts(
    profile: Profile,
    fact_ids: Sequence[str],
    *,
    transcript: Transcript,
    pins: VersionPins | None = None,
    catalog: Catalog | None = None,
    reextract: Reextract | None,
    minicheck: GroundingScorer | None = None,
    k: int = K_PROBES,
    concurrency: int = 8,
) -> VerifyResult:
    """Entailment and consistency for ``fact_ids``; corrective ops go through the reducer (§9.1, §13.3)."""
    catalog = catalog or load_catalog(profile.schema_version)
    ids = [fid for fid in dict.fromkeys(fact_ids) if fid in profile.facts]

    async def no_probes() -> dict[str, ConsistencyResult]:
        return {}

    ent, cons = await asyncio.gather(
        check_entailment(profile, ids, transcript=transcript, pins=pins, catalog=catalog, minicheck=minicheck, concurrency=concurrency),
        run_consistency(profile, ids, catalog=catalog, reextract=reextract, k=k, concurrency=concurrency)
        if reextract is not None
        else no_probes(),
    )
    assert isinstance(ent, EntailmentResult)
    errors: list[str] = []
    probe_failures = 0
    for fid in ids:
        f = profile.facts[fid]
        ver = dict(f.verification)
        if fid in ent.facts:
            fe = ent.facts[fid]
            ver.update(fe.verification())
            # A check that failed (provider error, output never validated) is retried on the next pass; one with
            # nothing to check (no valued hypothesis, no evidence in the transcript) has seen the fact as it is.
            if fe.status != "not_run" or fe.error in NOTHING_TO_CHECK:
                ver["checked_fingerprint"] = fingerprint(f)
            if fe.error and fe.error not in NOTHING_TO_CHECK:
                errors.append(f"{fid}: entailment {fe.error}")
        if fid in cons:
            ver.update(cons[fid].verification())
            probe_failures += cons[fid].failures
            if cons[fid].failures and cons[fid].answered == 0:
                errors.append(f"{fid}: consistency probes all failed ({cons[fid].error})")
        profile = annotate(profile, fid, verification=ver)

    usage = dict(ent.usage)
    usage["failures"] = usage.get("failures", 0) + probe_failures
    result = VerifyResult(profile=profile, usage=usage, errors=errors)
    specs = corrective_ops(profile, ent)
    if specs:
        r = apply_state_ops(profile, make_ops(profile, specs))
        profile, result.ops, result.rejected = r.profile, r.applied, r.rejected
        retracted: dict[str, set[int]] = {}
        for op in r.applied:
            idx = op.payload.get("hypothesis_index")
            if idx is not None:
                retracted.setdefault(op.fact_id, set()).add(int(idx))
        # Every fact a corrective op changed is re-recorded in its new form (so the next pass does not check it
        # again): the surviving readings re-indexed after retractions; the status change to contradicted as is.
        for fid in dict.fromkeys(op.fact_id for op in r.applied):
            if fid not in profile.facts:
                continue
            f = profile.facts[fid]
            gone = retracted.get(fid, set())
            fe = _surviving(ent.facts[fid], gone, [h.weight for h in f.hypotheses]) if gone else ent.facts[fid]
            ver = {**f.verification, **fe.verification(), "checked_fingerprint": fingerprint(f)}
            if gone:
                ver["retracted_hypotheses"] = sorted(gone)
            ver["contradicted"] = True  # a contradiction was found, even where the contradicted reading is gone
            profile = annotate(profile, fid, verification=ver)
    result.profile = profile
    result.results = {fid: _summary(fid, ent.facts.get(fid), cons.get(fid), fid not in profile.facts) for fid in ids}
    return result


def score_profile(
    profile: Profile,
    calibrator: Calibrator | None,
    conformal: ConformalThresholds | None,
    settings: Settings | None = None,
    *,
    transcript: Transcript,
    catalog: Catalog | None = None,
    discourse: DiscourseOutput | None = None,
    net_income_monthly: float | None = None,
) -> Profile:
    """Constraints → features → ``p_raw`` → ``p_correct`` → ``auto_accepted`` for every extracted fact (§9).

    Facts from the prior FNA, the CRM, defaults or the reviewer are trusted values, not extractions: they
    get ``p_correct = 1`` and are never auto-accepted. Facts the reviewer already decided keep their scores.
    """
    settings = settings or get_settings()
    catalog = catalog or load_catalog(profile.schema_version)
    calibrator = calibrator or Calibrator.identity(catalog)
    profile = annotate_constraints(profile, check_constraints(profile, catalog, net_income_monthly=net_income_monthly))
    index = EvidenceIndex(transcript)
    capped = bool(transcript.quality and transcript.quality.cap_p_correct)
    rows, fids = [], []
    for fid, f in profile.facts.items():
        if f.review.status in ("accepted", "edited", "rejected"):
            continue
        if f.source in TRUSTED_SOURCES or f.source not in EXTRACTED_SOURCES:
            profile = annotate(profile, fid, p_correct=1.0, p_raw=1.0, auto_accepted=False)
            continue
        feats = features(f, transcript, profile, catalog=catalog, discourse=discourse, index=index)
        rows.append((to_storable(feats), catalog.slot(f.slot_id).group, catalog.family_of(f.slot_id)))
        fids.append(fid)
    raw, p = calibrator.predict(rows)
    for i, fid in enumerate(fids):
        p_correct = min(float(p[i]), QUALITY_CAP) if capped else float(p[i])
        profile = annotate(profile, fid, p_raw=float(raw[i]), p_correct=p_correct, features=rows[i][0])
        f = profile.facts[fid]
        ok, why = auto_accept_decision(f, conformal, catalog=catalog, settings=settings)
        tau = conformal.tau_for(catalog.family_of(f.slot_id)) if conformal is not None else None
        ver = {**f.verification, "auto_accept": {"accepted": ok, "reason": why, "tau": tau}, "calibrator_version": calibrator.version}
        profile = annotate(profile, fid, auto_accepted=ok, verification=ver)
    return profile


class Stage5Result(BaseModel):
    profile: Profile
    verify: VerifyResult
    coverage: CoverageResult | None = None
    reverify: VerifyResult | None = None
    usage: dict[str, int] = {}
    errors: list[str] = []  # every check that failed to run (never a silent NaN or "absent"): the activity logs these
    skipped: list[str] = []  # checks that did not run at all in this deployment (a stub verifier, no probes)


async def verify_stage(
    profile: Profile,
    *,
    transcript: Transcript,
    pins: VersionPins | None = None,
    catalog: Catalog | None = None,
    reextract: Reextract | None,
    reopen: Reopen | None,
    calibrator: Calibrator | None,
    conformal: ConformalThresholds | None,
    settings: Settings | None = None,
    minicheck: GroundingScorer | None = None,
    discourse: DiscourseOutput | None = None,
    fact_ids: Sequence[str] | None = None,
    on_profile: Callable[[Profile], None] | None = None,
) -> Stage5Result:
    """The whole of Stage 5 for one call: verify, omission check, verify what it reopened, then score.

    ``fact_ids`` defaults to every extracted fact the checks have not seen in its current form, so after
    the orchestrator (which verifies each slot group as it goes) only new or changed facts are checked again.
    ``on_profile`` is told the profile after every step that changes it, so ``reextract`` and ``reopen``
    callables bound to a live view of the run (``verify_after_agent``) read the profile as it now stands.
    """
    catalog = catalog or load_catalog(profile.schema_version)

    def changed(p: Profile) -> Profile:
        if on_profile is not None:
            on_profile(p)
        return p

    ids = list(fact_ids) if fact_ids is not None else [fid for fid, f in profile.facts.items() if needs_verification(f)]
    v = await verify_facts(profile, ids, transcript=transcript, pins=pins, catalog=catalog, reextract=reextract, minicheck=minicheck)
    out = Stage5Result(profile=changed(v.profile), verify=v, errors=list(v.errors))
    if reextract is None:
        out.skipped.append("consistency probes (no re-extraction callable: the small role is a stub)")
    usage = dict(v.usage)
    if reopen is not None:
        c = await check_coverage(
            v.profile,
            transcript=transcript,
            pins=pins,
            catalog=catalog,
            reopen=reopen,
            topic_index=discourse.topic_index if discourse else None,
            on_profile=on_profile,
        )
        out.coverage, out.profile = c, c.profile
        out.errors += c.errors
        if c.skipped:
            out.skipped.append(f"omission check ({c.skipped})")
        for k2, n in c.usage.items():
            usage[k2] = usage.get(k2, 0) + n
        new = [fid for fid in c.reopened if fid in c.profile.facts]
        if new:
            rv = await verify_facts(
                c.profile, new, transcript=transcript, pins=pins, catalog=catalog, reextract=reextract, minicheck=minicheck
            )
            out.reverify, out.profile = rv, changed(rv.profile)
            out.errors += rv.errors
            for k2, n in rv.usage.items():
                usage[k2] = usage.get(k2, 0) + n
    else:
        out.skipped.append("omission check (no reopen callable: the reasoning role is a stub)")
    out.profile = changed(
        score_profile(out.profile, calibrator, conformal, settings, transcript=transcript, catalog=catalog, discourse=discourse)
    )
    out.usage = usage
    if out.errors:
        log.warning("stage 5 on call %s: %d check(s) failed to run: %s", profile.call_id, len(out.errors), "; ".join(out.errors[:5]))
    return out
