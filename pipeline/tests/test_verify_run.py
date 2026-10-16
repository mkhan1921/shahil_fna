"""Stage 5 entry points: verify_facts, score_profile and verify_stage (§9, §13.3)."""

from __future__ import annotations

import json
import re

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from advisor_intel.config import Settings
from advisor_intel.extract.reducer import check_profile
from advisor_intel.models.facts import Hypothesis
from advisor_intel.models.profile import StateOp
from advisor_intel.models.runs import VersionPins
from advisor_intel.models.transcript import QualityReport
from advisor_intel.verify.calibrator import Calibrator
from advisor_intel.verify.conformal import ConformalThresholds
from advisor_intel.verify.consistency import ProbeResult
from advisor_intel.verify.coverage import coverage_agent
from advisor_intel.verify.entailment import entailment_agent
from advisor_intel.verify.minicheck import FakeMiniCheck
from advisor_intel.verify.run import score_profile, verify_facts, verify_stage

from .verify_factories import CATALOG, PROV, ProfileBuilder, ev, money, point, segment, transcript


def _t(quality=None):
    return transcript(
        [
            segment("s0001", "When do you want to retire?", "advisor"),
            segment("s0002", "Sixty, or maybe sixty-five", t0=3),
            segment("s0003", "I earn about forty-five thousand a month", t0=8),
            segment("s0004", "We pay twelve thousand on the bond", t0=14),
        ],
        quality=quality,
    )


def _profile():
    b = ProfileBuilder()
    ra = b.add(
        "retirement.{p}.retirementAge",
        "client",
        [(point(60, "age"), 0.6), (point(65, "age"), 0.4)],
        evidence=[ev("s0002", "Sixty, or maybe sixty-five")],
    )
    inc = b.add("income.{p}.grossMonthly", "client", money(45000), evidence=[ev("s0003", "about forty-five thousand a month")])
    bonus = b.add("income.{p}.annualBonus", "client", money(30000, "annual"), evidence=[ev("s0003", "forty-five thousand")])
    b.entity("liability.bond", "liability", "Home loan")
    rep = b.add("liabilities[].monthlyRepayment", "liability.bond", money(12000), evidence=[ev("s0004", "twelve thousand on the bond")])
    seed = b.seed("{p}.employer", "client", point("Eskom"))
    return b.build(), ra, inc, bonus, rep, seed


def _verifier(rule):
    def fn(messages, info: AgentInfo):
        prompt = str(messages[-1].parts[-1].content)
        claims = re.findall(r"^- (c\d+): (.*)$", prompt, flags=re.M)
        return ModelResponse(
            parts=[TextPart(json.dumps({"verdicts": [{"claim_id": c, "verdict": rule(t), "reason": "r"} for c, t in claims]}))]
        )

    return FunctionModel(fn)


async def _reextract(fact, variant):
    if fact.slot_id == "retirement.{p}.retirementAge":
        return ProbeResult(status="stated", value=point(60 if variant < 2 else 70, "age"))
    return ProbeResult(status="stated", value=fact.value)


async def test_verify_facts_annotates_and_applies_corrective_ops():
    p, ra, inc, bonus, rep, _ = _profile()
    rule = lambda t: "contradicted" if ("65" in t or "R30,000" in t) else "entailed"  # noqa: E731
    with entailment_agent.override(model=_verifier(rule)):
        res = await verify_facts(
            p,
            [ra, inc, bonus],
            transcript=_t(),
            pins=VersionPins(),
            catalog=CATALOG,
            reextract=_reextract,
            minicheck=FakeMiniCheck(default=0.8),
        )
    f = res.profile.facts[ra]
    assert [h.value.dist.value for h in f.hypotheses] == [60]  # the contradicted reading was retracted
    ver = f.verification
    assert ver["retracted_hypotheses"] == [1] and ver["contradicted"] is True
    assert ver["entail_score"] == pytest.approx(1.0)  # recomputed over the surviving hypothesis
    assert ver["entailment"]["hypotheses"][0]["index"] == 0 and ver["entailment"]["hypotheses"][0]["weight"] == pytest.approx(1.0)
    assert ver["consistency"] == pytest.approx(2 / 3)
    assert ver["minicheck_score"] == pytest.approx(0.8)
    assert bonus not in res.profile.facts and res.results[bonus]["removed"]  # a single contradicted reading: extraction error
    assert res.profile.facts[inc].verification["entailment"]["status"] == "entailed"
    assert {op.actor for op in res.ops} == {"verifier"} and not res.rejected
    assert res.usage["requests"] == 3
    assert check_profile(res.profile) == []


async def test_verify_facts_without_probes_or_a_live_verifier():
    p, ra, *_ = _profile()
    res = await verify_facts(p, [ra, "nope#x"], transcript=_t(), pins=VersionPins(), catalog=CATALOG, reextract=None)
    ver = res.profile.facts[ra].verification
    assert ver["entailment"]["status"] == "not_run" and ver["entail_score"] is None and "consistency" not in ver
    assert res.ops == [] and list(res.results) == [ra]


def test_score_profile_features_scores_and_auto_accept():
    p, ra, inc, bonus, rep, seed = _profile()
    from advisor_intel.extract.reducer import annotate

    for fid in (ra, inc, bonus, rep):
        p = annotate(p, fid, verification={"entail_score": 1.0, "consistency": 1.0})
    th = ConformalThresholds(
        enabled=True, reason="t", tau_by_family={"income_expenses": 0.5, "goals": 0.5, "assets_liabilities": 0.5}, tau_pooled_family=0.5
    )
    out = score_profile(p, Calibrator.identity(CATALOG), th, Settings(), transcript=_t(), catalog=CATALOG)
    b = out.facts[bonus]  # tier B, stated, strong checks → above τ
    assert b.p_correct > 0.5 and b.p_raw == pytest.approx(b.p_correct) and b.auto_accepted
    assert b.verification["auto_accept"]["accepted"] and b.verification["auto_accept"]["tau"] == 0.5
    assert b.features["entail_score"] == 1.0 and b.features["resolution_conf"] is None  # NaN stored as None
    assert not out.facts[inc].auto_accepted and "tier A" in out.facts[inc].verification["auto_accept"]["reason"]
    assert out.facts[seed].p_correct == 1.0 and not out.facts[seed].auto_accepted  # a prior value, not an extraction
    assert out.facts[ra].verification["constraint"] == "pass"
    assert check_profile(out) == []
    # a low-quality call caps p_correct
    capped = score_profile(
        p,
        Calibrator.identity(CATALOG),
        th,
        Settings(),
        transcript=_t(
            QualityReport(mean_word_conf=0.7, low_asr_share=0.3, crosstalk_share=0, n_speakers=2, role_conf_min=0.9, cap_p_correct=True)
        ),
        catalog=CATALOG,
    )
    assert capped.facts[bonus].p_correct <= 0.9 and capped.facts[bonus].features["call_quality_capped"] == 1.0
    # no thresholds: nothing is auto-accepted
    off = score_profile(p, None, None, Settings(), transcript=_t(), catalog=CATALOG)
    assert not any(f.auto_accepted for f in off.facts.values())


def test_hard_constraint_failures_are_never_auto_accepted():
    b = ProfileBuilder()
    b.seed("{p}.dateOfBirth", "client", point("1974-07-01", "date"))
    fid = b.add("retirement.{p}.planningAge", "client", point(45, "age"), evidence=[ev("s0002", "Sixty")])  # planning age before retirement
    b.add("retirement.{p}.retirementAge", "client", point(60, "age"), evidence=[ev("s0002", "Sixty")])
    p = b.build()
    th = ConformalThresholds(enabled=True, reason="t", tau_by_family={"goals": 0.0}, tau_pooled_family=0.0)
    out = score_profile(p, Calibrator.identity(CATALOG), th, Settings(), transcript=_t(), catalog=CATALOG)
    assert out.facts[fid].verification["constraint"] == "fail" and not out.facts[fid].auto_accepted


async def test_verify_stage_reverifies_what_the_omission_check_reopened():
    p, ra, inc, bonus, rep, _ = _profile()

    async def reopen(slot_id, instance, segs):
        if slot_id != "{p}.employmentType":
            return []
        payload = {
            "slot_id": slot_id,
            "instance_key": "client",
            "fna_path": "client.employmentType",
            "status": "stated",
            "hypotheses": [Hypothesis(value=point("salaried"), weight=1, rationale="said").model_dump(mode="json")],
            "evidence": [ev("s0003", "I earn").model_dump(mode="json")],
            "provenance": PROV.model_dump(mode="json"),
        }
        return [StateOp(op="ADD", fact_id="{p}.employmentType#client", segment_id="s0003", payload=payload, actor="extractor", seq=1)]

    def answers(messages, info):
        prompt = str(messages[-1].parts[-1].content)
        qs = re.findall(r"^- (q\d+): (.*)$", prompt, flags=re.M)
        out = [
            {
                "question_id": q,
                "answer": "present" if "employment type" in line else "absent",
                "segment_ids": ["s0003"] if "employment type" in line else [],
                "reason": "r",
            }
            for q, line in qs
        ]
        return ModelResponse(parts=[TextPart(json.dumps({"answers": out}))])

    with entailment_agent.override(model=_verifier(lambda t: "entailed")), coverage_agent.override(model=FunctionModel(answers)):
        res = await verify_stage(
            p,
            transcript=_t(),
            pins=VersionPins(),
            catalog=CATALOG,
            reextract=_reextract,
            reopen=reopen,
            calibrator=Calibrator.identity(CATALOG),
            conformal=None,
            settings=Settings(),
        )
    emp = res.profile.facts["{p}.employmentType#client"]
    assert emp.reopened_by_coverage and emp.verification["entailment"]["status"] == "entailed"
    assert res.reverify is not None and list(res.reverify.results) == ["{p}.employmentType#client"]
    assert all(f.p_correct != 0.5 for f in res.profile.facts.values())  # everything scored
    assert check_profile(res.profile) == []
