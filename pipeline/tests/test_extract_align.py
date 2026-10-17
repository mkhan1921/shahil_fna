"""Grounding, object validation, the planner's keyword scan and revision ops (§7.1, §7.2, §7.3, §7.5)."""

from __future__ import annotations

import asyncio

import pytest

from advisor_intel import stats
from advisor_intel.extract.align import align_values, canon, deterministic_score
from advisor_intel.extract.context import ExtractionDeps
from advisor_intel.extract.ops import FactDraft, to_state_ops
from advisor_intel.extract.planner import build_plan, keyword_scan
from advisor_intel.extract.reducer import apply_state_ops, make_ops
from advisor_intel.extract.subagent import FoundObject, validate_objects
from advisor_intel.lexicon import Lexicon, load_lexicon
from advisor_intel.models import Entity, Evidence, Hypothesis, Point, Profile, Provenance, Quantity
from advisor_intel.models.entities import DiscourseOutput, Revision
from advisor_intel.models.extraction import AmountApprox, ExtractionTask, FieldValue, RawHypothesis
from advisor_intel.models.runs import VersionPins
from advisor_intel.schema import load_catalog
from advisor_intel.testing.transcripts import transcript_from_turns

CAT = load_catalog()
LEX = load_lexicon()
PINS = VersionPins()  # stub models: no aligner, so grounding is the deterministic path only

CALL = [
    ("advisor", "What retirement savings do you have?"),
    ("client_primary", "My company pension fund, about one point two million, and an RA of about three hundred thousand."),
    ("advisor", "Any debt?"),
    ("client_primary", "The bond, we owe about one point one million, it's fifteen thousand a month."),
    ("advisor", "Life cover?"),
    ("client_primary", "I have life cover with Discovery, three million, it pays to Lerato."),
    ("advisor", "And your salary?"),
    ("client_primary", "I take home about forty-two thousand a month."),
    ("advisor", "Is that right?"),
    ("client_primary", "Actually no, sorry, it went up in April, it's more like forty-five now."),
]
T = transcript_from_turns(CALL)


def sid(part: str) -> str:
    return next(s.segment_id for s in T.segments if part in s.text)


def disc(revisions: list[Revision] = ()) -> DiscourseOutput:
    return DiscourseOutput(entities={"person.client_primary": Entity(entity_id="person.client_primary", type="person", canonical_name="Sipho")}, revisions=list(revisions))


def deps_for(slot_id: str, group: str, d: DiscourseOutput, instance: str | None = "client") -> ExtractionDeps:
    profile = Profile(call_id=T.call_id, run_id="wf-a", schema_version=CAT.version, anchor_date=T.anchor_date, entities=dict(d.entities))
    task = ExtractionTask(task_id=group, slot_group=group, slot_ids=[slot_id], instance_keys=[instance], segment_ids=[s.segment_id for s in T.segments])
    return ExtractionDeps(task=task, catalog=CAT, profile=profile, transcript=T, discourse=d, pins=PINS)


def value(said: str, hyps: list[RawHypothesis], path: str = "x") -> FieldValue:
    return FieldValue(fna_path=path, hypotheses=hyps, hedges=[], speaker_attribution="client", said=said, interpretation="", self_score=0.8)


def approx(v: float, word: str = "about", revises: bool = False) -> RawHypothesis:
    return RawHypothesis(intermediate=AmountApprox(value=v, approx_word=word, currency="ZAR", period="once", basis="na"), weight=0, rationale="", revises=revises)


# --------------------------------------------------------------------------- deterministic grounding


def test_deterministic_score_needs_the_numbers_to_agree():
    said = canon("about one point two million")
    assert deterministic_score(said, canon("my company pension fund, about one point two million, and an RA")) >= 90
    assert deterministic_score(said, canon("the bond, we owe about one point one million")) == 0.0  # a different number
    assert deterministic_score(canon("forty-two thousand a month"), canon("I take home about forty-two a month")) > 0  # an elided scale word is not a different number
    assert deterministic_score(canon("fifteen thousand a month"), canon("it's fifty thousand a month")) == 0.0


def test_canon_keeps_adjacent_numbers_apart():
    assert canon("Fifty-eight. Actually, let's say sixty, fifty-eight is too ambitious.") == "58 actually let s say 60 58 is too ambitious"
    assert canon("sixty or sixty-two") == "60 or 62"
    assert canon("forty-two thousand a month, maybe forty-five") == "42000 a month maybe 45"
    assert canon("two three") == "2 3" and canon("twenty twenty six") == "20 26"


def test_deterministic_score_runs_in_one_direction_only():
    said = canon("life cover with Discovery")
    assert deterministic_score(said, canon("I have life cover with Discovery, three million")) >= 90
    assert deterministic_score(said, canon("Life cover?")) == 0.0  # the short segment is inside the quote, not the other way round
    assert deterministic_score("", canon("anything")) == 0.0


async def test_alignment_never_cites_a_segment_with_another_number():
    d = disc()
    deps = deps_for("retirementFunds[].value", "retirement_funds", d, "account.pension_1")
    key = "retirementFunds[].value#account.pension_1"
    fv = value("about one point two million", [approx(1_200_000)])
    res = await align_values(deps, {key: fv}, {key: [sid("company pension")]}, {key: CAT.slot("retirementFunds[].value")})
    segs = [e.segment_id for e in res.aligned[key].evidence]
    assert segs == [sid("company pension")]
    assert sid("The bond") not in segs


async def test_alignment_never_cites_a_short_segment_contained_in_the_quote():
    d = disc()
    deps = deps_for("policies[].insurer", "policies", d, "policy.disc_1")
    key = "policies[].insurer#policy.disc_1"
    fv = value("life cover with Discovery", [RawHypothesis(intermediate={"type": "TEXT", "text": "Discovery"}, weight=0, rationale="", revises=False)])
    res = await align_values(deps, {key: fv}, {key: [sid("I have life cover")]}, {key: CAT.slot("policies[].insurer")})
    assert [e.segment_id for e in res.aligned[key].evidence] == [sid("I have life cover")]
    assert all(e.speaker_role != "advisor" for e in res.aligned[key].evidence)


async def test_a_revising_hypothesis_is_grounded_in_the_revising_segment():
    first, second = sid("I take home"), sid("Actually no")
    d = disc([Revision(segment_id=second, revises_segment_id=first, scope="income", trigger="Actually")])
    deps = deps_for("income.{p}.grossMonthly", "income", d)
    key = "income.{p}.grossMonthly#client"
    fv = value("I take home about forty-two thousand a month", [approx(42_000), approx(45_000, "more like", revises=True)])
    fv = fv.model_copy(update={"hypotheses": [h.model_copy(update={"intermediate": h.intermediate.model_copy(update={"period": "monthly", "basis": "net"})}) for h in fv.hypotheses]})
    res = await align_values(deps, {key: fv}, {key: [first, second]}, {key: CAT.slot("income.{p}.grossMonthly")})
    ev = res.aligned[key].evidence
    assert [e.segment_id for e in ev] == [first, second]
    assert [e.role for e in ev] == ["supports", "revises"]
    assert ev[1].quote == T.segment(second).text and ev[1].aligner_score == pytest.approx(0.9)
    # Without a revising hypothesis the revision segment is not cited.
    plain = value("I take home about forty-two thousand a month", [approx(42_000)])
    res2 = await align_values(deps, {key: plain}, {key: [first]}, {key: CAT.slot("income.{p}.grossMonthly")})
    assert [e.segment_id for e in res2.aligned[key].evidence] == [first]


# --------------------------------------------------------------------------- objects and the planner


def test_objects_without_a_window_segment_are_rejected():
    deps = deps_for("retirementFunds[].value", "retirement_funds", disc(), "account.pension_1")
    found = [FoundObject(entity_id="new:pension", catalog_key=None, segment_ids=[], is_new=True, name="pension"),
             FoundObject(entity_id="new:ra", catalog_key=None, segment_ids=[sid("company pension")], is_new=True, name="RA"),
             FoundObject(entity_id="account.ghost", catalog_key=None, segment_ids=[sid("company pension")])]
    errors = validate_objects(found, deps, {})
    assert set(errors) == {"new:pension", "account.ghost"}
    assert "no segment" in errors["new:pension"] and "not in the registry" in errors["account.ghost"]


def test_keyword_scan_finds_only_groups_the_call_talks_about():
    assert sid("The bond") in keyword_scan(T, CAT, "liabilities")
    assert sid("I have life cover") in keyword_scan(T, CAT, "policies")
    assert keyword_scan(T, CAT, "estate") == []  # nobody mentioned wills, executors or trusts
    plan = build_plan(CAT, disc(), T, None)
    estate = next(t for t in plan.tasks if t.task_id == "estate:client")
    assert estate.status == "todo" and estate.segment_ids == []  # tier A: a task, confirming not_discussed without a window
    liabilities = next(t for t in plan.tasks if t.task_id == "liabilities")
    assert sid("The bond") in liabilities.segment_ids


# --------------------------------------------------------------------------- ops


def _draft(key: str, hyps: list[Hypothesis], seg: str) -> FactDraft:
    slot = CAT.slot("retirement.{p}.retirementAge")
    ev = [Evidence(segment_id=seg, char_start=0, char_end=5, quote="sixty", speaker_role="client_primary", t_start=0, t_end=1, asr_mean_conf=0.9, role="supports")]
    fv = value("sixty", [], path="retirement.client.retirementAge")
    return FactDraft(key=key, slot=slot, instance="client", fna_path="retirement.client.retirementAge", status="stated", value=fv, hypotheses=hyps, hedges=[], evidence=ev)


def age(v: float) -> Quantity:
    return Quantity(dist=Point(value=v), unit="age")


def test_update_of_an_earlier_extraction_uses_the_lexicon_recency_weights():
    key = "retirement.{p}.retirementAge#client"
    prov = Provenance(run_id="wf-a", lexicon_version=LEX.version, schema_version=CAT.version)
    profile = Profile(call_id="c", run_id="wf-a", schema_version=CAT.version, anchor_date=T.anchor_date)
    first = _draft(key, [Hypothesis(value=age(60), weight=1.0, rationale="")], "s0001")
    specs, _ = to_state_ops([first], [], profile, prov, {}, LEX)
    profile = apply_state_ops(profile, make_ops(profile, specs)).profile
    later = _draft(key, [Hypothesis(value=age(62), weight=1.0, rationale="", revises=True)], "s0002")
    tuned = Lexicon.from_text(LEX.source_text.replace("recency_weight: {revising: 0.8, revised: 0.2}", "recency_weight: {revising: 0.7, revised: 0.3}"))
    specs, _ = to_state_ops([later], [], profile, prov, {}, tuned)
    assert [s[0] for s in specs] == ["UPDATE"]
    assert specs[0][3]["weights"] == pytest.approx([0.3, 0.7])
    out = apply_state_ops(profile, make_ops(profile, specs)).profile.facts[key]
    assert [round(h.weight, 6) for h in out.hypotheses] == [0.3, 0.7] and out.evidence[-1].role == "revises"
    assert stats.median(out.value.dist) == 62
    # The default lexicon gives the spec's 0.8 / 0.2.
    specs, _ = to_state_ops([later], [], profile, prov, {})
    assert specs[0][3]["weights"] == pytest.approx([0.2, 0.8])


def test_sync_entry_point_for_async_tests():
    assert asyncio.iscoroutinefunction(align_values)
