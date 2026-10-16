"""Calibration labels from reviewer actions (§9.3, §11.2)."""

from __future__ import annotations

from datetime import datetime, timezone

from advisor_intel.extract.reducer import annotate, make_ops
from advisor_intel.models.dists import Categorical, Triangular
from advisor_intel.models.facts import Hypothesis, ReviewState
from advisor_intel.models.review import ReviewResult
from advisor_intel.verify.labels import build_labels, labels_from_review, summarise, within_original, wrong_auto_accepts

from .verify_factories import CATALOG, PROV, ProfileBuilder, ev, money, point, qty

AT = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)


def rv(status="accepted"):
    return ReviewState(status=status, reviewer_id="adv1", reviewed_at=AT)


def _before():
    b = ProfileBuilder()
    b.entity("person.neo", "person", "Neo").entity("person.kagiso", "person", "Kagiso").entity("policy.old", "policy", "Old policy")
    ids = {
        "acc": b.add("income.{p}.grossMonthly", "client", money(45000)),
        "in": b.add("retirement.{p}.retirementAge", "client", qty(Triangular(low=58, mode=62, high=66), "age")),
        "out": b.add("retirement.{p}.planningAge", "client", qty(Triangular(low=85, mode=88, high=90), "age")),
        "cat_same": b.add("{p}.employmentType", "client", qty(Categorical(probs={"self-employed": 0.7, "contract": 0.3}), "none")),
        "rej": b.add("{p}.smoker", "client", point(True)),
        "nd": b.seed("income.{p}.annualBonus", "client", money(60000, "annual")),
        "unk": b.add("estate.{p}.hasWill", "client", point("yes")),
        "wrong_child": b.add("dependants[].tertiaryStartAge", "person.neo", point(18, "age")),
        "row": b.seed("policies[].type", "policy.old", point("life")),
    }
    p = b.build()
    for i, fid in enumerate(ids.values()):
        p = annotate(
            p,
            fid,
            p_correct=0.5 + i * 0.05,
            p_raw=0.4,
            features={"entail_score": 1.0, "self_score": None},
            auto_accepted=(fid == ids["acc"]),
        )
    return p, ids


def _ops(p, ids):
    hyp = lambda q: [Hypothesis(value=q, weight=1.0, rationale="reviewer")]  # noqa: E731
    specs = [
        ("CONFIRM", ids["acc"], None, {"review": rv()}, "reviewer"),
        ("UPDATE", ids["in"], None, {"replace_hypotheses": hyp(point(60, "age")), "review": rv("edited")}, "reviewer"),
        ("UPDATE", ids["out"], None, {"replace_hypotheses": hyp(point(95, "age")), "review": rv("edited")}, "reviewer"),
        ("UPDATE", ids["cat_same"], None, {"replace_hypotheses": hyp(point("self-employed")), "review": rv("edited")}, "reviewer"),
        ("RETRACT", ids["rej"], None, {"reason": "misheard", "review": rv("rejected")}, "reviewer"),
        (
            "UPDATE",
            ids["nd"],
            None,
            {"status": "stated", "replace_hypotheses": hyp(money(80000, "annual")), "review": rv("edited")},
            "reviewer",
        ),
        ("MARK_UNKNOWN", ids["unk"], None, {"reason": "client unsure", "review": rv("edited")}, "reviewer"),
        ("RETRACT", ids["wrong_child"], None, {"reason": "wrong_entity", "review": rv("rejected")}, "reviewer"),
        (
            "ADD",
            "dependants[].tertiaryStartAge#person.kagiso",
            None,
            {
                "slot_id": "dependants[].tertiaryStartAge",
                "instance_key": "person.kagiso",
                "fna_path": "dependants[k].tertiaryStartAge",
                "source": "reviewer",
                "status": "stated",
                "hypotheses": hyp(point(18, "age")),
                "evidence": [ev("s0002", "after matric")],
                "entity_refs": ["person.kagiso"],
                "provenance": PROV,
            },
            "reviewer",
        ),
        (
            "ADD",
            "goals[].cost#goal.car",
            None,
            {
                "slot_id": "goals[].cost",
                "instance_key": "goal.car",
                "fna_path": "goals[g].cost",
                "source": "reviewer",
                "status": "stated",
                "hypotheses": hyp(money(300000, "once")),
                "provenance": PROV,
                "review": rv("accepted"),
            },
            "reviewer",
        ),
        ("RETRACT", ids["row"], None, {"reason": "removed", "review": rv("rejected")}, "reviewer"),
    ]
    return make_ops(p, specs)


def test_every_label_rule():
    p, ids = _before()
    batch = build_labels(p, _ops(p, ids), catalog=CATALOG)
    by = {lb.fact_id: lb for lb in batch.labels}
    assert (by[ids["acc"]].correct, by[ids["acc"]].action) == (1, "accepted")
    assert (by[ids["in"]].correct, by[ids["in"]].reason) == (1, "edited_within")  # 60 inside p10–p90 of 58–66
    assert (by[ids["out"]].correct, by[ids["out"]].reason) == (0, "edited_outside")
    assert (by[ids["cat_same"]].correct, by[ids["cat_same"]].reason) == (1, "edited_within")  # same argmax
    assert (by[ids["rej"]].correct, by[ids["rej"]].action, by[ids["rej"]].reason) == (0, "rejected", "misheard")
    assert (by[ids["nd"]].correct, by[ids["nd"]].action) == (0, "status_changed")
    assert by[ids["nd"]].reason == "not_discussed->stated"
    assert (by[ids["unk"]].correct, by[ids["unk"]].action) == (0, "status_changed")
    assert (by[ids["wrong_child"]].correct, by[ids["wrong_child"]].reason) == (0, "wrong_entity")
    # the re-attributed value is not a recall miss; a fact the reviewer added is
    assert [m.fact_id for m in batch.recall_misses] == ["goals[].cost#goal.car"]
    assert batch.recall_misses[0].slot_group == "goals" and batch.recall_misses[0].tier == "A"
    # removing a prior row the call never discussed is not an extraction outcome
    assert ids["row"] not in by and any(s.startswith(ids["row"]) for s in batch.skipped)
    # labels carry the pre-review score, features, grouping and reviewer
    acc = by[ids["acc"]]
    assert acc.p_correct == 0.5 and acc.p_raw == 0.4 and acc.features == {"entail_score": 1.0, "self_score": None}
    assert acc.slot_group == "income" and acc.family == "income_expenses" and acc.tier == "A"
    assert acc.reviewer_id == "adv1" and acc.call_id == "c1" and acc.eligible
    assert not by[ids["nd"]].eligible  # not stated: never eligible for auto-accept
    assert wrong_auto_accepts(batch.labels) == (1, 0)
    assert summarise(batch)["recall_misses"] == 1


def test_labels_from_a_review_result_file():
    p, ids = _before()
    ops = _ops(p, ids)
    result = ReviewResult(call_id="c1", run_id="wf-1", base_profile_version=p.version, reviewer_id="adv1", ops=ops, submitted=True)
    assert labels_from_review(p, result, catalog=CATALOG).labels == build_labels(p, ops, catalog=CATALOG).labels


def test_reattribution_by_update_and_speaker_change():
    p, ids = _before()
    ops = make_ops(
        p,
        [
            ("UPDATE", ids["acc"], None, {"speaker_attribution": "advisor_suggested", "review": rv("edited")}, "reviewer"),
            ("UPDATE", ids["wrong_child"], None, {"instance_key": "person.kagiso", "review": rv("edited")}, "reviewer"),
        ],
    )
    by = {lb.fact_id: lb for lb in build_labels(p, ops, catalog=CATALOG).labels}
    assert by[ids["acc"]].reason == "wrong_entity" and by[ids["acc"]].correct == 0
    assert by[ids["wrong_child"]].reason == "wrong_entity"


def test_within_original_rules():
    assert within_original(money(45000), money(45000))
    assert within_original(qty(Triangular(low=40000, mode=45000, high=50000), "ZAR", "monthly"), money(46000))
    assert not within_original(qty(Triangular(low=40000, mode=45000, high=50000), "ZAR", "monthly"), money(60000))
    assert within_original(money(45000), money(540000, "annual"))  # same amount, other period
    assert within_original(point(True), point(True)) and not within_original(point(True), point(False))
    assert within_original(point("Allan Gray"), point("allan gray"))
