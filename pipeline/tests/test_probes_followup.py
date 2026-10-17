"""Coverage reopening (§9.1d) and follow-up answers (§10.6) through the real value stage, lexicon and reducer."""

from __future__ import annotations

import asyncio
from datetime import UTC

import pytest

from advisor_intel import llm, stats
from advisor_intel.discourse.run import run_discourse
from advisor_intel.extract.followup import answer_transcript, extract_followup_answer, extract_followup_ops
from advisor_intel.extract.probes import ProbeContext, make_reopen
from advisor_intel.extract.reducer import apply_state_ops, check_profile
from advisor_intel.models.profile import FollowUp, Profile
from advisor_intel.models.runs import VersionPins
from advisor_intel.schema import load_catalog
from advisor_intel.testing.calls import SAMPLE_CALL, SAMPLE_GOLD, SAMPLE_OBJECTS, _amt
from advisor_intel.testing.fakes import scripted_model
from advisor_intel.testing.oracle import Oracle
from advisor_intel.testing.transcripts import transcript_from_turns

CAT = load_catalog()
T = transcript_from_turns(SAMPLE_CALL)
DISC = asyncio.run(run_discourse(T))
SPOUSE_GROSS = "income.{p}.grossMonthly#spouse"


def pins_for(name: str, oracle: Oracle) -> VersionPins:
    return VersionPins(reasoning_model=llm.register_local_model(name, scripted_model(oracle.handler)))


@pytest.fixture
def profile() -> Profile:
    return Profile(call_id=T.call_id, run_id="wf-p", schema_version=CAT.version, anchor_date=T.anchor_date, entities=dict(DISC.entities))


async def reopened(profile: Profile) -> Profile:
    oracle = Oracle(T, SAMPLE_OBJECTS, SAMPLE_GOLD)
    ctx = ProbeContext(catalog=CAT, transcript=T, discourse=DISC, pins=pins_for("oracle-reopen", oracle), run_id="wf-p")
    seg = next(s.segment_id for s in T.segments if "thirty-five a month gross" in s.text)
    ops = await make_reopen(ctx, lambda: profile)("income.{p}.grossMonthly", "spouse", [seg])
    assert [o.op for o in ops] == ["ADD"] and ops[0].fact_id == SPOUSE_GROSS
    assert ops[0].payload["reopened_by_coverage"]
    res = apply_state_ops(profile, ops)
    assert not res.rejected
    return res.profile


async def test_coverage_reopen_adds_the_missed_fact(profile):
    p = await reopened(profile)
    f = p.facts[SPOUSE_GROSS]
    assert f.fna_path == "income.spouse.grossMonthly" and f.status == "stated"
    assert f.value.dist.mode == 35_000 and f.evidence[0].quote in T.index()[f.evidence[0].segment_id].text
    assert check_profile(p) == []


async def test_followup_answer_replaces_the_vague_value(profile):
    p = await reopened(profile)
    before = p.facts[SPOUSE_GROSS]
    answer = "It's thirty-six thousand five hundred gross a month."
    gold = {SPOUSE_GROSS: {"forms": [_amt("exact", 36_500, basis="gross")], "said": "thirty-six thousand five hundred gross a month"}}
    fu = FollowUp(
        followup_id="fu1",
        fact_ids=[SPOUSE_GROSS],
        question="What is Lerato's gross monthly salary?",
        why="moves the shortfall",
        expected_pos_change=0.05,
        answer_type="AMOUNT_EXACT",
    )
    t, ops = await extract_followup_answer(p, fu, answer, catalog=CAT, pins=pins_for("oracle-fu", Oracle(T, [], gold)), run_id="wf-p")
    assert [o.op for o in ops] == ["UPDATE"]
    res = apply_state_ops(p, ops)
    assert not res.rejected
    f = res.profile.facts[SPOUSE_GROSS]
    assert f.source == "followup" and stats.median(f.value.dist) == 36_500
    assert {e.segment_id for e in before.evidence} <= {e.segment_id for e in f.evidence}  # the call's evidence stays
    assert any(e.segment_id == t.segments[0].segment_id and e.role == "revises" for e in f.evidence)


async def test_lookup_term_service_uses_the_glossary_and_the_prior_file():
    from datetime import datetime
    from types import SimpleNamespace

    from advisor_intel.harness.deps import AgentInputs
    from advisor_intel.harness.services import default_services
    from advisor_intel.lexicon.glossary import GlossaryEntry, InMemoryGlossary

    store = InMemoryGlossary(
        [
            GlossaryEntry(
                term="Max Income",
                canonical_name="Sanlam Max Income Plan",
                product_type="living-annuity",
                provider="Sanlam",
                confirmed_by="adviser",
                confirmed_at=datetime(2026, 1, 5, tzinfo=UTC),
            )
        ]
    )
    services = default_services(glossary_store=store)
    prof = Profile(call_id=T.call_id, run_id="wf-p", schema_version=CAT.version, anchor_date=T.anchor_date)
    inputs = AgentInputs(call_id=T.call_id, run_id="wf-p", transcript=T, discourse=DISC, profile=prof)
    deps = SimpleNamespace(inputs=inputs)  # the service reads only the run's inputs
    res = await services.lookup_term("max income", "my max income from Sanlam", deps)
    assert res.data["resolved"] and res.data["candidate"]["canonical_name"] == "Sanlam Max Income Plan"
    res = await services.lookup_term("Zorblax Wealth Booster", "", deps)
    assert not res.data["resolved"] and res.data["product_type"] == "other"


async def test_followup_intake_entry_takes_the_answer_as_a_transcript(profile):
    p = await reopened(profile)
    gold = {SPOUSE_GROSS: {"forms": [_amt("exact", 36_500, basis="gross")], "said": "thirty-six thousand five hundred gross a month"}}
    fu = FollowUp(
        followup_id="fu2",
        fact_ids=[SPOUSE_GROSS],
        question="What is Lerato's gross monthly salary?",
        why="",
        expected_pos_change=0.05,
        answer_type="AMOUNT_EXACT",
    )
    t = answer_transcript(p, fu, "It's thirty-six thousand five hundred gross a month.", call_id=p.call_id)
    ops = await extract_followup_ops(p, t, followup=fu, pins=pins_for("oracle-fu2", Oracle(T, [], gold)), catalog=CAT)
    assert [o.op for o in ops] == ["UPDATE"] and ops[0].payload["source"] == "followup"
