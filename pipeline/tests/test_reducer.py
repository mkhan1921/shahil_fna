"""The reducer is pure, deterministic and the only write path (§3.5, §7.5)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from advisor_intel.extract.reducer import apply_state_ops, check_profile, make_ops, merged_value
from advisor_intel.models import Entity, Evidence, Hypothesis, Point, Profile, Provenance, Quantity, Triangular
from advisor_intel.models.dists import Mixture
from advisor_intel.models.facts import ReviewState

PROV = Provenance(run_id="wf-1", lexicon_version="1.3", schema_version="profile_v1")


def ev(seg="s0010", quote="I want to retire at sixty", role="supports"):
    return Evidence(
        segment_id=seg,
        char_start=0,
        char_end=len(quote),
        quote=quote,
        speaker_role="client_primary",
        t_start=10,
        t_end=12,
        asr_mean_conf=0.93,
        role=role,
    )


def age(v):
    return Quantity(dist=Point(value=v), unit="age")


def profile():
    return Profile(
        call_id="c1",
        run_id="wf-1",
        schema_version="profile_v1",
        anchor_date=date(2026, 10, 15),
        entities={"person.client_primary": Entity(entity_id="person.client_primary", type="person", canonical_name="Thabo")},
    )


FID = "retirement.client.retirementAge"


def add_payload(value=60, quote="I want to retire at sixty", seg="s0010", status="stated", source="call", evidence=True):
    return {
        "slot_id": "retirement.{p}.retirementAge",
        "instance_key": "client",
        "fna_path": FID,
        "source": source,
        "status": status,
        "hypotheses": [Hypothesis(value=age(value), weight=1.0, rationale="said")],
        "evidence": [ev(seg, quote)] if evidence else [],
        "entity_refs": ["person.client_primary"],
        "provenance": PROV,
    }


def test_add_creates_fact_with_mixture_value_and_replays():
    p = profile()
    ops = make_ops(p, [("ADD", FID.replace("retirement.client", "retirement.{p}") + "#client", "s0010", add_payload(), "extractor")])
    r = apply_state_ops(p, ops)
    assert not r.rejected
    f = r.profile.facts["retirement.{p}.retirementAge#client"]
    assert f.value == age(60) and f.change == "new"
    assert check_profile(r.profile) == []


def _fid():
    return "retirement.{p}.retirementAge#client"


def test_add_merges_hypotheses_and_update_revises():
    p = profile()
    r = apply_state_ops(p, make_ops(p, [("ADD", _fid(), "s0010", add_payload(60), "extractor")])).profile
    r = apply_state_ops(r, make_ops(r, [("ADD", _fid(), "s0020", add_payload(65, "or maybe sixty-five", "s0020"), "extractor")])).profile
    f = r.facts[_fid()]
    assert [round(h.weight, 6) for h in f.hypotheses] == [0.5, 0.5]
    assert isinstance(f.value.dist, Mixture)
    # A later statement revises: the revising value gets 0.8, the revised 0.2 (§7.3 step 7).
    upd = {
        "add_hypotheses": [Hypothesis(value=age(62), weight=0, rationale="actually 62", revises=True)],
        "weights": [0.1, 0.1, 0.8],
        "evidence": [ev("s0030", "actually no, sixty-two")],
    }
    r = apply_state_ops(r, make_ops(r, [("UPDATE", _fid(), "s0030", upd, "extractor")])).profile
    f = r.facts[_fid()]
    assert [round(h.weight, 6) for h in f.hypotheses] == [0.1, 0.1, 0.8]
    assert f.evidence[-1].role == "revises"
    assert check_profile(r) == []


def test_ops_on_unknown_facts_are_rejected():
    p = profile()
    ops = make_ops(p, [("UPDATE", "nope#x", "s1", {"weights": []}, "extractor"), ("CONFIRM", "nope#y", "s1", {}, "extractor")])
    r = apply_state_ops(p, ops)
    assert len(r.rejected) == 2 and not r.applied and r.profile.ops_log == []


def test_seed_is_replaced_by_call_and_becomes_prior():
    p = profile()
    seed = add_payload(65, status="not_discussed", source="prior_fna", evidence=False)
    seed["prior_value"] = age(65)
    r = apply_state_ops(p, make_ops(p, [("ADD", _fid(), None, seed, "loader")])).profile
    assert r.facts[_fid()].change == "unchanged_not_discussed"
    r = apply_state_ops(r, make_ops(r, [("ADD", _fid(), "s0010", add_payload(60), "extractor")])).profile
    f = r.facts[_fid()]
    assert f.source == "call" and f.prior_value == age(65) and f.change == "changed"
    r2 = apply_state_ops(p, make_ops(p, [("ADD", _fid(), None, seed, "loader")])).profile
    r2 = apply_state_ops(r2, make_ops(r2, [("ADD", _fid(), "s0010", add_payload(65.5, "sixty-five"), "extractor")])).profile
    assert r2.facts[_fid()].change == "confirmed"  # within 2%
    assert check_profile(r) == [] and check_profile(r2) == []


def test_retract_variants():
    p = profile()
    base = apply_state_ops(p, make_ops(p, [("ADD", _fid(), "s0010", add_payload(60), "extractor")])).profile
    mind = apply_state_ops(
        base,
        make_ops(
            base, [("RETRACT", _fid(), "s0040", {"reason": "change_of_mind", "evidence": [ev("s0040", "we haven't decided")]}, "extractor")]
        ),
    ).profile
    assert mind.facts[_fid()].status == "explicitly_unknown" and mind.facts[_fid()].value is None
    gone = apply_state_ops(base, make_ops(base, [("RETRACT", _fid(), None, {"reason": "extraction_error"}, "verifier")])).profile
    assert _fid() not in gone.facts
    rej = apply_state_ops(base, make_ops(base, [("RETRACT", _fid(), None, {"reason": "misheard"}, "reviewer")])).profile
    assert rej.facts[_fid()].review.status == "rejected" and rej.facts[_fid()].review.reason == "misheard"
    for prof in (mind, gone, rej):
        assert check_profile(prof) == []


def test_confirm_and_mark_unknown_and_reviewer_edit():
    p = profile()
    base = apply_state_ops(p, make_ops(p, [("ADD", _fid(), "s0010", add_payload(60), "extractor")])).profile
    conf = apply_state_ops(
        base, make_ops(base, [("CONFIRM", _fid(), "s0011", {"evidence": [ev("s0011", "yes sixty")]}, "extractor")])
    ).profile
    assert conf.facts[_fid()].speaker_attribution == "advisor_confirmed_by_client"
    acc = apply_state_ops(
        conf,
        make_ops(
            conf,
            [
                (
                    "CONFIRM",
                    _fid(),
                    None,
                    {"review": ReviewState(status="accepted", reviewer_id="adv1", reviewed_at=datetime(2026, 10, 16, tzinfo=UTC))},
                    "reviewer",
                )
            ],
        ),
    ).profile
    assert acc.facts[_fid()].review.status == "accepted"
    edit = {
        "replace_hypotheses": [
            Hypothesis(value=Quantity(dist=Triangular(low=60, mode=62, high=65), unit="age"), weight=1, rationale="reviewer")
        ],
        "review": ReviewState(status="edited", reviewer_id="adv1"),
    }
    ed = apply_state_ops(acc, make_ops(acc, [("UPDATE", _fid(), None, edit, "reviewer")])).profile
    f = ed.facts[_fid()]
    assert f.review.status == "edited" and f.review.original_value == age(60)
    assert isinstance(f.value.dist, Triangular)
    unk = apply_state_ops(
        p,
        make_ops(
            p,
            [
                (
                    "MARK_UNKNOWN",
                    "goals[].cost#goal.trip",
                    "s0050",
                    {
                        "slot_id": "goals[].cost",
                        "instance_key": "goal.trip",
                        "fna_path": "goals[g1].cost",
                        "provenance": PROV,
                        "evidence": [ev("s0050", "no idea what it'll cost")],
                    },
                    "extractor",
                )
            ],
        ),
    ).profile
    assert unk.facts["goals[].cost#goal.trip"].status == "explicitly_unknown"
    for prof in (conf, acc, ed, unk):
        assert check_profile(prof) == []


def test_invariants_catch_tampering():
    p = profile()
    good = apply_state_ops(p, make_ops(p, [("ADD", _fid(), "s0010", add_payload(60), "extractor")])).profile
    f = good.facts[_fid()]
    bad = good.model_copy(update={"facts": {_fid(): f.model_copy(update={"value": age(70)})}})
    assert any("mixture" in x for x in check_profile(bad))
    bad2 = good.model_copy(update={"facts": {_fid(): f.model_copy(update={"evidence": []})}})
    probs = check_profile(bad2)
    assert any("no supporting evidence" in x for x in probs) and any("replaying" in x for x in probs)
    bad3 = good.model_copy(update={"facts": {_fid(): f.model_copy(update={"entity_refs": ["person.ghost"]})}})
    assert any("registry" in x for x in check_profile(bad3, replay=False))
    with pytest.raises(ValueError):
        bad.validate()


def test_pure_and_deterministic():
    p = profile()
    ops = make_ops(p, [("ADD", _fid(), "s0010", add_payload(60), "extractor")])
    a = apply_state_ops(p, ops).profile
    b = apply_state_ops(p, ops).profile
    assert a.model_dump() == b.model_dump()
    assert p.facts == {} and p.ops_log == []
    stale = apply_state_ops(a, ops)  # same seqs again: not after the log
    assert len(stale.rejected) == 1


def test_merged_value_basis():
    h1 = Hypothesis(value=Quantity(dist=Point(value=40000), unit="ZAR", period="monthly", basis="gross"), weight=0.5, rationale="")
    h2 = Hypothesis(value=Quantity(dist=Point(value=30000), unit="ZAR", period="monthly", basis="net"), weight=0.5, rationale="")
    assert merged_value([h1, h2]).basis == "unknown"


def test_reviewer_restates_a_slot_they_rejected():
    """A re-attribution back onto a rejected slot (§11.2) replaces the rejected fact, keeping its prior."""
    p = profile()
    seed = add_payload(65, status="not_discussed", source="prior_fna", evidence=False)
    seed["prior_value"] = age(65)
    r = apply_state_ops(p, make_ops(p, [("ADD", _fid(), None, seed, "loader")])).profile
    r = apply_state_ops(r, make_ops(r, [("ADD", _fid(), "s0010", add_payload(60), "extractor")])).profile
    rej = apply_state_ops(r, make_ops(r, [("RETRACT", _fid(), None, {"reason": "wrong_entity"}, "reviewer")])).profile
    assert rej.facts[_fid()].review.status == "rejected"
    again = add_payload(62, "sixty-two", source="reviewer")
    back = apply_state_ops(rej, make_ops(rej, [("ADD", _fid(), "s0010", again, "reviewer")])).profile
    f = back.facts[_fid()]
    assert f.review.status == "pending" and f.source == "reviewer"
    assert [h.value.dist.value for h in f.hypotheses] == [62]  # replaced, not merged with the rejected 60
    assert f.prior_value == age(65) and f.change == "changed"
    # The extractor's ADD onto a rejected fact still merges: only the reviewer's statement replaces.
    merged = apply_state_ops(rej, make_ops(rej, [("ADD", _fid(), "s0011", add_payload(62, "sixty-two", "s0011"), "extractor")])).profile
    assert len(merged.facts[_fid()].hypotheses) == 2 and merged.facts[_fid()].review.status == "rejected"
    assert check_profile(back) == [] and check_profile(merged) == []


def test_reviewer_mark_unknown_and_reject_keep_the_originals():
    """What the reviewer replaced stays on record for the label of §9.3 (status changed → 0 for the original)."""
    p = profile()
    base = apply_state_ops(p, make_ops(p, [("ADD", _fid(), "s0010", add_payload(60), "extractor")])).profile
    review = ReviewState(status="edited", reviewer_id="adv1", note="HR to confirm")
    unk = apply_state_ops(
        base, make_ops(base, [("MARK_UNKNOWN", _fid(), None, {"reason": "unknown", "review": review}, "reviewer")])
    ).profile
    f = unk.facts[_fid()]
    assert f.status == "explicitly_unknown" and f.hypotheses == [] and f.value is None
    assert (
        f.review.status == "edited"
        and f.review.original_value == age(60)
        and f.review.original_status == "stated"
        and f.review.note == "HR to confirm"
    )
    # Marked unknown twice: the first originals stay.
    unk2 = apply_state_ops(
        unk, make_ops(unk, [("MARK_UNKNOWN", _fid(), None, {"review": ReviewState(status="edited", reviewer_id="adv1")}, "reviewer")])
    ).profile
    assert unk2.facts[_fid()].review.original_value == age(60) and unk2.facts[_fid()].review.original_status == "stated"
    # An edit, then a rejection: the rejection keeps the edit's original.
    edit = {
        "replace_hypotheses": [Hypothesis(value=age(62), weight=1, rationale="reviewer")],
        "review": ReviewState(status="edited", reviewer_id="adv1"),
    }
    ed = apply_state_ops(base, make_ops(base, [("UPDATE", _fid(), None, edit, "reviewer")])).profile
    rej = apply_state_ops(
        ed,
        make_ops(
            ed,
            [("RETRACT", _fid(), None, {"reason": "misheard", "review": ReviewState(status="rejected", reviewer_id="adv1")}, "reviewer")],
        ),
    ).profile
    assert rej.facts[_fid()].review.status == "rejected" and rej.facts[_fid()].review.original_value == age(60)
    # The extractor's MARK_UNKNOWN records nothing: it is not a review decision.
    ext = apply_state_ops(base, make_ops(base, [("MARK_UNKNOWN", _fid(), "s0040", {"reason": "undecided"}, "extractor")])).profile
    assert ext.facts[_fid()].review.original_value is None and ext.facts[_fid()].review.original_status is None
    for prof in (unk, unk2, rej, ext):
        assert check_profile(prof) == []
