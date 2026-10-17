"""Blocking flags (§10.3): one per rule and fact, merging with reviewer decisions, clearing."""

from __future__ import annotations

from datetime import UTC, datetime

from advisor_intel.models.profile import BlockingFlag
from advisor_intel.models.review import FlagClearance
from advisor_intel.models.transcript import QualityReport
from advisor_intel.voi.flags import blocks_submission, clear_flags, compute_flags, merge_flags
from advisor_intel.voi.sensitivity import SensitivityRow

from .verify_factories import CATALOG, ProfileBuilder, ev, money, point, segment, span, transcript

AT = datetime(2026, 10, 16, tzinfo=UTC)


def _kinds(flags):
    return {(f.kind, f.fact_id) for f in flags}


def _household():
    b = ProfileBuilder()
    b.add("household.maritalStatus", None, point("single"))
    b.seed("{p}.dateOfBirth", "client", point("1974-07-01", "date"))
    b.add("{p}.employmentType", "client", point("salaried"))
    inc = b.add(
        "income.{p}.grossMonthly",
        "client",
        [(money(25000), 0.6), (money(250000), 0.4)],
        evidence=[ev("s0002", "two fifty", start=13)],
        normalization={"scale": {"scale_ambiguous": True}},
    )
    ra = b.add("retirement.{p}.retirementAge", "client", point(45, "age"))  # below the client's current age: a hard failure
    will = b.add("estate.{p}.hasWill", "client", [(point("yes"), 0.5), (point("no"), 0.5)], status="contradicted")
    b.entity("policy.x", "policy", "Xyz cover")
    pol = b.add(
        "policies[].cover", "policy.x", money(1_000_000, "once"), term_resolution={"term": "Xyz", "status": "unresolved", "candidates": []}
    )
    b.add("policies[].type", "policy.x", point("life"))
    return b.build(), inc, ra, will, pol


def _transcript(**qkw):
    q = QualityReport(
        mean_word_conf=0.9,
        low_asr_share=0.0,
        crosstalk_share=0.0,
        n_speakers=2,
        role_conf_min=qkw.get("role_conf_min", 0.95),
        role_review_needed=qkw.get("role_review_needed", False),
    )
    return transcript(
        [segment("s0002", "I earn about two fifty a month", t0=1)],
        spans=[span("sp1", "s0002", 3, 5, [(25000, 0.6), (250000, 0.4)], second=250000.0, agree=False)],
        quality=q,
    )


def test_each_rule_raises_one_flag_per_fact():
    p, inc, ra, will, pol = _household()
    rows = [
        SensitivityRow(
            fact_id=ra,
            slot_id="retirement.{p}.retirementAge",
            tier="A",
            reasons=["tier A"],
            status_flip=True,
            flips=["retirement:client shortfall→partial"],
        ),
        SensitivityRow(
            fact_id="retirement.{p}.targetPercent#client",
            slot_id="retirement.{p}.targetPercent",
            tier="A",
            reasons=[],
            virtual=True,
            status_flip=True,
            flips=["x a→b"],
        ),
    ]
    flags = compute_flags(p, catalog=CATALOG, transcript=_transcript(role_conf_min=0.7), sensitivity=rows)
    kinds = _kinds(flags)
    assert ("tier_a_missing", "retirement.{p}.targetPercent#client") in kinds  # an expected tier A field with no value
    assert ("tier_a_missing", "estate.{p}.hasWill#client") not in kinds
    assert ("tier_a_contradicted", will) in kinds
    assert ("status_flip", ra) in kinds and (
        "status_flip",
        "retirement.{p}.targetPercent#client",
    ) not in kinds  # virtual rows: no flip flag
    assert ("scale_ambiguous", inc) in kinds  # tier A amount, top reading 60% < 90%
    assert ("second_pass_disagrees", inc) in kinds  # the re-check heard R250,000; the chosen reading is R25,000
    assert ("hard_constraint", ra) in kinds and "retirement_age_vs_current_age" in next(
        f.flag_id for f in flags if f.kind == "hard_constraint"
    )
    assert ("unresolved_term", pol) in kinds
    assert ("unresolved_term", "policies[].type#policy.x") in kinds  # tier A fact on the same policy depends on the term too
    assert ("role_review", None) in kinds
    ids = [f.flag_id for f in flags]
    assert len(ids) == len(set(ids))  # one flag per rule and fact
    assert all(not f.cleared for f in flags)


def test_clean_facts_raise_nothing_beyond_missing_fields():
    b = ProfileBuilder()
    b.add("household.maritalStatus", None, point("single"))
    b.add("income.{p}.grossMonthly", "client", money(45000), evidence=[ev("s0002", "forty five")])
    flags = compute_flags(b.build(), catalog=CATALOG, transcript=transcript([segment("s0002", "forty five thousand")]))
    assert {f.kind for f in flags} == {"tier_a_missing"}
    assert all(not fid.startswith("income.{p}.grossMonthly") for _, fid in _kinds(flags))


def test_merge_keeps_reviewer_decisions_and_closes_resolved_flags():
    p, _inc, _ra, _will, _pol = _household()
    first = compute_flags(p, catalog=CATALOG)
    p = p.model_copy(update={"blocking_flags": first})
    hc = next(f for f in first if f.kind == "hard_constraint")
    p = clear_flags(p, [FlagClearance(flag_id=hc.flag_id, resolution="edited", reviewer_id="adv1", at=AT)])
    cleared = next(f for f in p.blocking_flags if f.flag_id == hc.flag_id)
    assert cleared.cleared and cleared.cleared_by == "adv1" and cleared.resolution == "edited"
    # recomputing raises the same hard failure: it stays cleared
    merged = merge_flags(p.blocking_flags, compute_flags(p, catalog=CATALOG), p)
    assert next(f for f in merged if f.flag_id == hc.flag_id).cleared
    assert blocks_submission(p)  # other flags are still open
    # a condition that went away closes its open flag, with the fact's review outcome as the resolution
    gone = merge_flags([BlockingFlag(flag_id="scale_ambiguous:x#client", kind="scale_ambiguous", fact_id="x#client", message="m")], [], p)
    assert gone[0].cleared and gone[0].cleared_by == "system" and gone[0].resolution == "dismissed"
    unknown = ProfileBuilder()
    fid = unknown.add("income.{p}.annualBonus", "client", None, status="explicitly_unknown", evidence=[])
    closed = merge_flags([BlockingFlag(flag_id=f"x:{fid}", kind="tier_a_missing", fact_id=fid, message="m")], [], unknown.build())
    assert closed[0].resolution == "marked_unknown"


def test_no_open_flags_no_block():
    b = ProfileBuilder()
    p = b.build().model_copy(
        update={
            "blocking_flags": [
                BlockingFlag(
                    flag_id="role_review:call", kind="role_review", fact_id=None, message="m", cleared=True, resolution="dismissed"
                )
            ]
        }
    )
    assert not blocks_submission(p)
