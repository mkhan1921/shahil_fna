"""The sample call through ingestion, the real harness, extraction stages, lexicon, reducer and derivations (oracle model)."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from advisor_intel import llm, stats
from advisor_intel.discourse.run import run_discourse
from advisor_intel.extract.reducer import check_profile
from advisor_intel.harness.deps import AgentInputs
from advisor_intel.harness.run import run_agent
from advisor_intel.harness.services import default_services
from advisor_intel.models.profile import Profile
from advisor_intel.models.runs import VersionPins
from advisor_intel.testing.calls import SAMPLE_CALL, SAMPLE_GOLD, SAMPLE_OBJECTS
from advisor_intel.testing.fakes import scripted_model
from advisor_intel.testing.oracle import Oracle
from advisor_intel.testing.transcripts import ingested_transcript


@pytest.fixture(scope="module")
def run():
    async def go():
        t = await ingested_transcript(SAMPLE_CALL)  # scripted ASR, segmentation, grammar spans
        disc = await run_discourse(t)  # no prior file: the second client on the call becomes the spouse
        prof = Profile(call_id=t.call_id, run_id="wf-e2e", schema_version="profile_v1", anchor_date=t.anchor_date, entities=dict(disc.entities))
        oracle = Oracle(t, SAMPLE_OBJECTS, SAMPLE_GOLD)
        pins = VersionPins(reasoning_model=llm.register_local_model("oracle-e2e", scripted_model(oracle.handler)))
        inputs = AgentInputs(call_id=t.call_id, run_id="wf-e2e", transcript=t, discourse=disc, profile=prof, pins=pins)
        return t, oracle, await run_agent(inputs, services=default_services())

    try:
        yield asyncio.run(go())
    finally:
        llm.unregister_local_model("oracle-e2e")


def fact(res, key: str):
    _, oracle, r = res
    hits = [f for fid, f in r.profile.facts.items() if oracle._gold_key(fid) == key]
    assert len(hits) == 1, (key, [f.fact_id for f in hits])
    return hits[0]


def test_run_completes_and_every_gold_field_is_a_fact(run):
    t, oracle, r = run
    assert not r.outcome.partial, r.outcome.reason
    assert not r.state["tool_errors"]
    found = {oracle._gold_key(fid) for fid in r.profile.facts}
    assert set(SAMPLE_GOLD) <= found, sorted(set(SAMPLE_GOLD) - found)
    assert check_profile(r.profile) == []  # invariants hold and the ops log replays to the same facts


def test_every_call_fact_quotes_the_transcript(run):
    t, _, r = run
    idx = t.index()
    injected = next(s.segment_id for s in t.segments if "Ignore previous instructions" in s.text)
    for f in r.profile.facts.values():
        if f.source != "call":
            continue
        assert f.evidence, f.fact_id
        for e in f.evidence:
            assert e.quote and e.quote in idx[e.segment_id].text, (f.fact_id, e.quote)
            assert e.segment_id != injected, f.fact_id


def test_values_follow_the_lexicon_and_the_derivations(run):
    gross = fact(run, "income.{p}.grossMonthly#client")
    assert gross.status == "implied" and gross.value.basis == "gross"  # 'take home ... more like forty-five' grossed up
    assert 52_000 < stats.median(gross.value.dist) < 66_000
    bonus = fact(run, "income.{p}.annualBonus#client")  # a thirteenth cheque: one month's gross pay
    assert bonus.value.basis == "gross" and bonus.value.period == "annual"
    assert stats.median(bonus.value.dist) == pytest.approx(stats.median(gross.value.dist), rel=0.08)
    spouse = fact(run, "income.{p}.grossMonthly#spouse")  # 'thirty-five a month': 35 or 35 000 at ingestion
    assert spouse.value.dist.mode == 35_000 and spouse.value.period == "monthly"
    span = next(s for s in run[0].numeric_spans if s.segment_id == spouse.evidence[0].segment_id and s.kind == "amount")
    assert "scale_ambiguous" in span.flags and spouse.normalization["scale"]["grammar_readings"] == 2
    age = fact(run, "retirement.{p}.retirementAge#client")
    assert stats.median(age.value.dist) == 60 and max(stats.sample(age.value.dist, np.random.default_rng(1), 500)) == 62
    fees = fact(run, "dependants[].schoolFeesAnnual#@thandi")  # 'four thousand a month' into an annual field
    assert fees.value.period == "annual" and fees.value.dist.mode == 48_000
    groceries = fact(run, "expenses.items[]#groceries")
    assert 8_000 <= stats.median(groceries.value.dist) <= 10_000
    cover = fact(run, "policies[].cover#@discovery life cover")
    assert cover.value.dist.value == 3_000_000
    will = fact(run, "estate.{p}.hasWill#spouse")  # 'I think' leaves room for doubt
    assert 0.5 < will.value.dist.probs["yes"] < 1.0
    overseas = fact(run, "event[].occurs#@thandi studies overseas")
    assert 0.2 < overseas.value.dist.p < 0.6
