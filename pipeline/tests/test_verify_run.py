"""Stage 5 entry points: verify_facts, score_profile and verify_stage (§9, §13.3)."""

from __future__ import annotations

import json
import re

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from advisor_intel.config import Settings
from advisor_intel.extract.reducer import apply_state_ops, check_profile, make_ops
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
from advisor_intel.verify.run import fingerprint, needs_verification, score_profile, verify_facts, verify_stage

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
        claims = re.findall(r"^- (c\d+): (.*)$", prompt, flags=re.MULTILINE)
        return ModelResponse(
            parts=[TextPart(json.dumps({"verdicts": [{"claim_id": c, "verdict": rule(t), "reason": "r"} for c, t in claims]}))]
        )

    return FunctionModel(fn)


async def _reextract(fact, variant):
    if fact.slot_id == "retirement.{p}.retirementAge":
        return ProbeResult(status="stated", value=point(60 if variant < 2 else 70, "age"))
    return ProbeResult(status="stated", value=fact.value)


async def test_verify_facts_annotates_and_applies_corrective_ops():
    p, ra, inc, bonus, _rep, _ = _profile()
    rule = lambda t: "contradicted" if ("65" in t or "R30,000" in t) else "entailed"
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
    p, _ra, _inc, _bonus, _rep, _ = _profile()

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
        qs = re.findall(r"^- (q\d+): (.*)$", prompt, flags=re.MULTILINE)
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


async def test_after_the_agent_only_new_or_changed_facts_are_verified_again():
    p, ra, inc, bonus, rep, seed = _profile()
    with entailment_agent.override(model=_verifier(lambda t: "entailed")):
        first = await verify_facts(p, [ra, inc], transcript=_t(), pins=VersionPins(), catalog=CATALOG, reextract=None)
    p = first.profile
    assert not needs_verification(p.facts[ra]) and not needs_verification(p.facts[inc])
    assert needs_verification(p.facts[bonus]) and needs_verification(p.facts[rep])
    assert not needs_verification(p.facts[seed])  # a prior value is not an extraction
    # a later, revising statement adds a reading of the retirement age: what the checks saw is stale
    revise = {"add_hypotheses": [Hypothesis(value=point(62, "age"), weight=0.5, rationale="r", revises=True)], "weights": [0.3, 0.2, 0.5]}
    p = apply_state_ops(p, make_ops(p, [("UPDATE", ra, "s0002", revise, "extractor")])).profile
    assert needs_verification(p.facts[ra]) and p.facts[ra].verification["checked_fingerprint"] != fingerprint(p.facts[ra])

    seen: list[list[str]] = []

    def fn(messages, info: AgentInfo):
        seen.append(re.findall(r"^- (c\d+): (.*)$", str(messages[-1].parts[-1].content), flags=re.MULTILINE))
        return _verifier(lambda t: "entailed").function(messages, info)

    with entailment_agent.override(model=FunctionModel(fn)):
        res = await verify_stage(
            p, transcript=_t(), pins=VersionPins(), catalog=CATALOG, reextract=None, reopen=None, calibrator=None, conformal=None
        )
    assert len(seen) == 3 and set(res.verify.results) == {ra, bonus, rep}  # the income fact was not asked again
    assert not any(needs_verification(f) for f in res.profile.facts.values())


def _broken_verifier():
    """A verifier whose output never names the claims: the retries run out and the check fails."""

    def fn(messages, info: AgentInfo):
        return ModelResponse(parts=[TextPart(json.dumps({"verdicts": [{"claim_id": "zzz", "verdict": "entailed", "reason": "r"}]}))])

    return FunctionModel(fn)


async def test_nothing_to_check_counts_as_checked_but_a_failed_check_is_retried():
    b = ProfileBuilder()
    unknown = b.add("income.{p}.annualBonus", "client", None, status="explicitly_unknown", evidence=[])  # no claim to verify
    valued = b.add("income.{p}.grossMonthly", "client", money(45000), evidence=[ev("s0003", "about forty-five thousand a month")])
    p = b.build()
    # a stub verifier: the valued fact's check did not run and stays due; the unknown fact had nothing to check
    res = await verify_facts(p, [unknown, valued], transcript=_t(), pins=VersionPins(), catalog=CATALOG, reextract=None)
    assert not needs_verification(res.profile.facts[unknown]) and res.profile.facts[unknown].verification["entailment"]["error"] == "no claims"
    assert needs_verification(res.profile.facts[valued]) and res.errors == []
    # a verifier that fails: the fact is reported, keeps NaN features and is retried on the next pass
    with entailment_agent.override(model=_broken_verifier()):
        res = await verify_facts(p, [valued], transcript=_t(), pins=VersionPins(), catalog=CATALOG, reextract=None)
    f = res.profile.facts[valued]
    assert needs_verification(f) and f.verification["entail_score"] is None and f.verification["entailment"]["status"] == "not_run"
    assert len(res.errors) == 1 and res.errors[0].startswith(f"{valued}: entailment UnexpectedModelBehavior")
    assert res.usage["failures"] == 1


async def test_probe_failures_are_counted_in_the_usage_and_reported_when_nothing_answered():
    p, ra, inc, *_ = _profile()

    async def flaky(fact, variant):
        if fact.fact_id == ra:
            raise RuntimeError("small model down")
        return ProbeResult(status="stated", value=fact.value)

    with entailment_agent.override(model=_verifier(lambda t: "entailed")):
        res = await verify_facts(p, [ra, inc], transcript=_t(), pins=VersionPins(), catalog=CATALOG, reextract=flaky)
    detail = res.profile.facts[ra].verification["consistency_detail"]
    assert detail["failures"] == 3 and detail["answered"] == 0 and "RuntimeError" in detail["error"] and "failed" in detail["skipped"]
    assert res.profile.facts[ra].verification["consistency"] is None
    assert res.profile.facts[inc].verification["consistency"] == 1.0 and res.profile.facts[inc].verification["consistency_detail"]["failures"] == 0
    assert res.usage["failures"] == 3 and [e for e in res.errors if e.startswith(ra)] and not [e for e in res.errors if e.startswith(inc)]


def _present_for(label, segs):
    def answers(messages, info):
        prompt = str(messages[-1].parts[-1].content)
        qs = re.findall(r"^- (q\d+): (.*)$", prompt, flags=re.MULTILINE)
        out = [
            {"question_id": q, "answer": "present" if label in line else "absent", "segment_ids": segs if label in line else [], "reason": "r"}
            for q, line in qs
        ]
        return ModelResponse(parts=[TextPart(json.dumps({"answers": out}))])

    return FunctionModel(answers)


def _contradict_once(pattern):
    """A verifier that contradicts the first claim matching ``pattern`` and entails everything after it, so a field the
    omission check reopens is not retracted again when it is verified a second time."""
    hit: list[str] = []

    def rule(t):
        if pattern in t and not hit:
            hit.append(t)
            return "contradicted"
        return "entailed"

    return _verifier(rule)


def _reopen_gross(seen):
    """A reopen that records its calls and re-adds the client's gross salary."""

    async def reopen(slot_id, instance, segs):
        if slot_id != "income.{p}.grossMonthly" or instance != "client":
            return []
        seen.append(slot_id)
        payload = {
            "slot_id": slot_id,
            "instance_key": "client",
            "fna_path": "income.client.grossMonthly",
            "status": "stated",
            "hypotheses": [Hypothesis(value=money(45000), weight=1, rationale="said").model_dump(mode="json")],
            "evidence": [ev("s0003", "forty-five thousand").model_dump(mode="json")],
            "provenance": PROV.model_dump(mode="json"),
        }
        return [StateOp(op="ADD", fact_id="income.{p}.grossMonthly#client", segment_id="s0003", payload=payload, actor="extractor", seq=1)]

    return reopen


async def test_verify_stage_tells_a_live_view_every_change_and_names_what_did_not_run():
    p, _ra, inc, bonus, _rep, _ = _profile()
    live: list = []
    seen: list = []
    reopen = _reopen_gross(seen)
    # the verifier contradicts the salary: the whole fact is retracted (no prior), then the omission check finds it again
    with (
        entailment_agent.override(model=_contradict_once("R45,000")),
        coverage_agent.override(model=_present_for("gross salary", ["s0003"])),
    ):
        res = await verify_stage(
            p,
            transcript=_t(),
            pins=VersionPins(),
            catalog=CATALOG,
            reextract=None,
            reopen=reopen,
            calibrator=None,
            conformal=None,
            on_profile=live.append,
        )
    assert seen == ["income.{p}.grossMonthly"] and res.coverage is not None and res.coverage.reopened == [inc]
    # the live view saw the retraction before the omission check reopened the field, and the final scored profile last
    assert inc not in live[0].facts and bonus in live[0].facts
    assert live[-1] is res.profile and res.profile.facts[inc].reopened_by_coverage and res.profile.facts[inc].p_correct != 0.5
    assert res.skipped == ["consistency probes (no re-extraction callable: the small role is a stub)"] and res.errors == []
    assert check_profile(res.profile) == []


async def test_verify_after_agent_binds_the_probes_to_the_live_profile(monkeypatch):
    """The reopen built from ``extract.probes`` reads the profile as it stands after the verifier's retractions, not the
    one the stage was given: an ADD for a fact the verifier removed, rather than an UPDATE the reducer would reject."""
    from advisor_intel.extract import probes
    from advisor_intel.models.entities import DiscourseOutput
    from advisor_intel.verify.services import verify_after_agent

    p, _ra, inc, _bonus, _rep, _ = _profile()
    views: list = []

    def fake_make_reopen(ctx, get_profile):
        inner = _reopen_gross([])

        async def reopen(slot_id, instance, segs):
            views.append(get_profile())
            return await inner(slot_id, instance, segs)

        return reopen

    monkeypatch.setattr(probes, "make_reopen", fake_make_reopen)
    monkeypatch.setattr(probes, "make_reextract", lambda ctx, get_profile: None)
    pins = VersionPins(reasoning_model="openai:gpt-4.1-mini")  # a live-looking reasoning pin: the reopen probe is built (never called here)
    with (
        entailment_agent.override(model=_contradict_once("R45,000")),
        coverage_agent.override(model=_present_for("gross salary", ["s0003"])),
    ):
        res = await verify_after_agent(
            p, transcript=_t(), discourse=DiscourseOutput(entities=p.entities), pins=pins, catalog=CATALOG, run_id="wf-1"
        )
    assert len(views) >= 1 and inc not in views[0].facts  # the verifier's retraction was visible to the reopen
    assert res.coverage is not None and res.coverage.reopened == [inc] and res.coverage.rejected == []
    assert res.profile.facts[inc].status == "stated" and check_profile(res.profile) == []
