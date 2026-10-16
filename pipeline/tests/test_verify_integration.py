"""Stages 5–6 with the extraction stage's real callables on the sample call, through the gold-call oracle.

The consistency probes (``extract.probes.make_reextract``), the omission check's reopening (``make_reopen``),
follow-up intake (``extract.followup.followup_ops``) and the in-process runner's stage 6 call.
"""

from __future__ import annotations

import asyncio
import functools
import json
import re
from datetime import date, datetime, timezone

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from advisor_intel import llm, stats
from advisor_intel.discourse.run import run_discourse
from advisor_intel.extract.followup import followup_ops
from advisor_intel.extract.reducer import check_profile
from advisor_intel.models.profile import FollowUp, Profile
from advisor_intel.models.runs import FollowupAnswer, VersionPins
from advisor_intel.pipeline import CallInput, process_call
from advisor_intel.schema import load_catalog
from advisor_intel.testing.calls import SAMPLE_CALL, SAMPLE_GOLD, SAMPLE_OBJECTS, _amt
from advisor_intel.testing.fakes import scripted_model
from advisor_intel.testing.oracle import Oracle
from advisor_intel.testing.transcripts import ingested_transcript, transcript_from_turns
from advisor_intel.verify.coverage import coverage_agent
from advisor_intel.verify.services import verify_after_agent
from advisor_intel.voi.intake import intake_answer

CAT = load_catalog()
T = transcript_from_turns(SAMPLE_CALL)
DISC = asyncio.run(run_discourse(T))
SPOUSE_GROSS = "income.{p}.grossMonthly#spouse"
SPOKEN = next(s.segment_id for s in T.segments if "thirty-five a month gross" in s.text)


@pytest.fixture
def oracle_pins():
    """The oracle answers the value stage on the reasoning role (reopening) and the small role (probes)."""
    name = llm.register_local_model("oracle-stage5", scripted_model(Oracle(T, SAMPLE_OBJECTS, SAMPLE_GOLD).handler))
    yield VersionPins(reasoning_model=name, small_model=name)
    llm.unregister_local_model("oracle-stage5")


def _empty_profile() -> Profile:
    return Profile(call_id=T.call_id, run_id="wf-s5", schema_version=CAT.version, anchor_date=T.anchor_date, entities=dict(DISC.entities))


def _coverage_answers(target: str, segment_ids: list[str]) -> FunctionModel:
    """The coverage verifier: ``present`` for the question naming ``target``, ``absent`` for every other one."""

    def fn(messages, info: AgentInfo):
        prompt = str(messages[-1].parts[-1].content)
        answers = [
            {"question_id": q, "answer": "present", "segment_ids": segment_ids, "reason": "said"}
            if target in line
            else {"question_id": q, "answer": "absent", "segment_ids": [], "reason": "not discussed"}
            for q, line in re.findall(r"^- (q\d+): (.*)$", prompt, flags=re.M)
        ]
        return ModelResponse(parts=[TextPart(json.dumps({"answers": answers}))])

    return FunctionModel(fn)


async def test_stage5_after_the_agent_reopens_probes_and_scores_with_the_real_extraction(oracle_pins):
    with coverage_agent.override(model=_coverage_answers("the client's spouse's gross salary", [SPOKEN])):
        res = await verify_after_agent(_empty_profile(), transcript=T, discourse=DISC, pins=oracle_pins, catalog=CAT, run_id="wf-s5")
    assert res.coverage is not None and res.coverage.reopened == [SPOUSE_GROSS]
    f = res.profile.facts[SPOUSE_GROSS]
    assert f.reopened_by_coverage and f.status == "stated" and f.value.dist.mode == 35_000
    assert f.verification["coverage"]["segments"] == [SPOKEN]
    # the reopened fact was verified: three paraphrased re-extractions through the value stage agree
    assert res.reverify is not None and f.verification["consistency"] == 1.0
    assert f.verification["consistency_detail"]["answered"] == 3
    # then scored: the cold-start calibrator, and no auto-accept without conformal thresholds
    assert 0 < f.p_correct < 1 and f.features and f.verification["auto_accept"]["accepted"] is False
    assert check_profile(res.profile) == []


async def test_a_recorded_answer_goes_through_the_value_stage_and_the_reducer(oracle_pins):
    with coverage_agent.override(model=_coverage_answers("the client's spouse's gross salary", [SPOKEN])):
        res = await verify_after_agent(_empty_profile(), transcript=T, discourse=DISC, pins=oracle_pins, catalog=CAT, run_id="wf-s5")
    fu = FollowUp(
        followup_id="fu1",
        fact_ids=[SPOUSE_GROSS],
        question="Roughly where would you put Lerato's gross monthly salary: closer to R32k or to R38k?",
        why="moves the household's emergency fund shortfall",
        expected_pos_change=0.05,
        answer_type="AMOUNT_RANGE",
    )
    p = res.profile.model_copy(update={"open_questions": [fu]})
    gold = {SPOUSE_GROSS: {"forms": [_amt("exact", 36_500, basis="gross")], "said": "thirty-six thousand five hundred gross a month"}}
    name = llm.register_local_model("oracle-intake", scripted_model(Oracle(T, [], gold).handler))
    try:
        extract = functools.partial(followup_ops, catalog=CAT, pins=VersionPins(reasoning_model=name), run_id="wf-s5")
        answer = FollowupAnswer(
            call_id=T.call_id,
            followup_id="fu1",
            text="It's thirty-six thousand five hundred gross a month.",
            answered_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
        )
        out = await intake_answer(p, answer, extract=extract)
    finally:
        llm.unregister_local_model("oracle-intake")
    g = out.profile.facts[SPOUSE_GROSS]
    assert [o.op for o in out.ops] == ["UPDATE"] and not out.rejected
    assert g.source == "followup" and stats.median(g.value.dist) == 36_500
    assert {(e.segment_id, e.role) for e in g.evidence} == {(SPOKEN, "supports"), ("fu-fu1", "revises")}
    assert out.followup.status == "answered" and out.profile.open_questions == [out.followup]
    assert check_profile(out.profile) == []


async def test_the_in_process_runner_writes_back_stage_6():
    t = await ingested_transcript(SAMPLE_CALL, call_id="call-voi")
    name = llm.register_local_model("oracle-voi", scripted_model(Oracle(t, SAMPLE_OBJECTS, SAMPLE_GOLD).handler))
    try:
        script = "\n".join(f"{role}: {text}" for role, text in SAMPLE_CALL)
        roles = {"advisor": "advisor", "client_primary": "client_primary", "client_secondary": "client_secondary"}
        call = CallInput(call_id="call-voi", anchor_date=date(2026, 10, 15), script=script, script_roles=roles)
        res = await process_call(call, VersionPins(reasoning_model=name), note=False)
    finally:
        llm.unregister_local_model("oracle-voi")
    assert "voi" not in res.skipped and not res.partial
    assert res.profile.open_questions == res.followups and 0 < len(res.followups) <= 8
    assert all(q.status == "open" and q.why for q in res.followups)
    assert res.profile.blocking_flags == res.flags and all(f.message for f in res.flags)
    top = max(res.profile.facts.values(), key=lambda f: f.review_priority)
    assert top.review_priority > 0 and top.voi["why_text"]
    assert any(field.get("why") == top.voi["why_text"] for field in res.sidecar["fields"].values())  # the overlay's line
    # the harness's verify_facts and check_constraints tools ran this package's services
    assert any("entailment" in f.verification for f in res.profile.facts.values())
    assert all("constraint" in f.verification for f in res.profile.facts.values() if f.source == "call")
