"""Every gold field of the sample call is extracted and grounded through the real stages (oracle model)."""

from __future__ import annotations

import asyncio

from advisor_intel import llm
from advisor_intel.discourse.run import run_discourse
from advisor_intel.extract.align import align_values
from advisor_intel.extract.context import ExtractionDeps
from advisor_intel.extract.planner import build_plan
from advisor_intel.extract.subagent import run_stages
from advisor_intel.models import Entity
from advisor_intel.models.profile import Profile
from advisor_intel.models.runs import VersionPins
from advisor_intel.schema import load_catalog
from advisor_intel.testing.calls import SAMPLE_CALL, SAMPLE_GOLD, SAMPLE_OBJECTS
from advisor_intel.testing.fakes import scripted_model
from advisor_intel.testing.oracle import Oracle
from advisor_intel.testing.transcripts import transcript_from_turns


async def test_oracle_extracts_and_grounds_every_gold_field():
    cat = load_catalog()
    t = transcript_from_turns(SAMPLE_CALL)
    disc = await run_discourse(t)
    disc.entities["person.spouse"] = Entity(entity_id="person.spouse", type="person", canonical_name="Lerato")
    profile = Profile(call_id=t.call_id, run_id="wf-o", schema_version=cat.version, anchor_date=t.anchor_date, entities=dict(disc.entities))
    oracle = Oracle(t, SAMPLE_OBJECTS, SAMPLE_GOLD)
    pins = VersionPins(reasoning_model=llm.register_local_model("oracle", scripted_model(oracle.handler)))
    plan = build_plan(cat, disc, t, profile)
    entities = dict(profile.entities)
    grounded: set[str] = set()
    for task in plan.tasks:
        if task.status != "todo":
            continue
        deps = ExtractionDeps(task=task, catalog=cat, profile=profile, transcript=t, discourse=disc, pins=pins, entities=entities)
        out = await run_stages(deps)
        assert not out.failures, (task.task_id, out.failures)
        slots = {k: cat.slot(k.split("#")[0]) for k in out.values}
        al = await align_values(deps, out.values, {k: s.segment_ids for k, s in out.statuses.items()}, slots)
        assert not al.dropped, (task.task_id, al.dropped)
        for k in al.aligned:
            grounded.add(oracle._gold_key(k) or k)
    missing = set(SAMPLE_GOLD) - grounded
    assert not missing, sorted(missing)
