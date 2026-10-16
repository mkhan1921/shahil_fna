"""Priority score, forced items, the why line (§10.3) and the action table (§10.4)."""

from __future__ import annotations

import pytest

from advisor_intel import binding
from advisor_intel.extract.reducer import annotate, check_profile
from advisor_intel.models.dists import Triangular
from advisor_intel.voi.clarify import clarify_all, should_clarify
from advisor_intel.voi.priority import PriorityItem, annotate_priorities, prioritise, review_list
from advisor_intel.voi.sensitivity import SensitivityRow

from .verify_factories import CATALOG, ProfileBuilder, money, point, qty


def _row(fid, slot, **kw):
    base = {"fact_id": fid, "slot_id": slot, "tier": CATALOG.slot(slot).sensitivity_tier, "reasons": ["tier"]}
    base.update(kw)
    return SensitivityRow(**base)


def test_priority_is_wc_content_plus_we_extract_plus_forced():
    b = ProfileBuilder()
    ra = b.add("retirement.{p}.retirementAge", "client", qty(Triangular(low=58, mode=62, high=66), "age"))
    bonus = b.add("income.{p}.annualBonus", "client", money(30000, "annual"))
    p = b.build()
    rows = [
        _row(ra, "retirement.{p}.retirementAge", i_pos=0.09, i_fna=0.05, i_content=0.09, i_extract=0.01, pos_base=0.7, pos_prior=0.65),
        _row(bonus, "income.{p}.annualBonus", i_pos=0.0, i_fna=0.0, i_content=0.0, i_extract=0.002, pos_base=0.70, pos_prior=0.65),
    ]
    items = {it.fact_id: it for it in prioritise(p, rows, catalog=CATALOG)}
    assert items[ra].priority == pytest.approx(1.0 * 0.09 + 2.0 * 0.01) and items[ra].forced == 0
    assert items[ra].why == "moves success odds by ~9 points"
    assert items[bonus].priority == pytest.approx(0.004)
    assert items[bonus].why == "if misheard, success odds could be off by ~5 points"
    w = {it.fact_id: it for it in prioritise(p, rows, catalog=CATALOG, w_content=2.0, w_extract=0.0)}
    assert w[ra].priority == pytest.approx(0.18)


def test_forced_rules():
    b = ProfileBuilder()
    flip = b.add("income.{p}.grossMonthly", "client", qty(Triangular(low=40000, mode=50000, high=60000), "ZAR", "monthly"))
    hard = b.add("retirement.{p}.retirementAge", "spouse", point(45, "age"))
    contra = b.add("retirement.{p}.planningAge", "client", [(point(90, "age"), 0.5), (point(95, "age"), 0.5)], status="contradicted")
    sugg_a = b.add("retirement.{p}.retirementAge", "client", point(65, "age"), attribution="advisor_suggested")
    sugg_b = b.add("{p}.smoker", "client", point(False), attribution="advisor_suggested")  # tier B: not forced
    p = b.build()
    p = annotate(
        p,
        hard,
        verification={"constraint": "fail", "constraint_messages": ["retirement age 45 for the spouse is below their current age (50)"]},
    )
    rows = [
        _row(flip, "income.{p}.grossMonthly", status_flip=True, flips=["life:client partial→shortfall"], i_fna=0.2, i_content=0.2),
        _row(hard, "retirement.{p}.retirementAge"),
        _row(contra, "retirement.{p}.planningAge"),
        _row(sugg_a, "retirement.{p}.retirementAge"),
        _row(sugg_b, "{p}.smoker"),
    ]
    items = {it.fact_id: it for it in prioritise(p, rows, catalog=CATALOG)}
    assert items[flip].forced == 1.0 and items[flip].priority == pytest.approx(1.2)
    assert items[flip].why.startswith("flips the client's life cover from partial to shortfall")
    assert items[hard].forced == 1.0 and "fails a check: retirement age 45" in items[hard].why
    assert items[contra].forced == 1.0 and "incompatible" in items[contra].why
    assert items[sugg_a].forced == 1.0 and "adviser proposed" in items[sugg_a].why
    assert items[sugg_b].forced == 0.0 and items[sugg_b].why == "little effect on the plan"
    # a virtual (missing) fact's flip is not forced: the missing-field flag covers it
    v = prioritise(
        p,
        [_row("retirement.{p}.targetPercent#client", "retirement.{p}.targetPercent", virtual=True, status_flip=True, flips=["x a→b"])],
        catalog=CATALOG,
    )[0]
    assert v.forced == 0.0 and v.virtual and "not discussed in the call" in v.why


def test_why_line_when_the_swing_exceeds_the_whole_need():
    b = ProfileBuilder()
    b.entity("liability.car", "liability", "Car finance")
    bal = b.add("liabilities[].balance", "liability.car", money(150000, "once"))
    p = b.build()
    row = _row(bal, "liabilities[].balance", i_fna=4.63, i_content=4.63, top_need="estate:spouse", top_need_change=2_800_000)
    item = prioritise(p, [row], catalog=CATALOG)[0]
    assert item.why == "moves the spouse's estate liquidity shortfall by R2.8m (more than the whole need)"


def test_collapse_annotation_sidecar_and_review_list():
    b = ProfileBuilder()
    ids = [b.add("income.{p}.annualBonus", "client", money(30000, "annual")), b.add("income.{p}.grossMonthly", "client", money(45000))]
    for i in range(20):
        b.entity(f"liability.l{i}", "liability", f"Loan {i}")
        ids.append(b.add("liabilities[].balance", f"liability.l{i}", money(1000 * (i + 1), "once")))
    p = b.build()
    p = annotate(p, ids[0], auto_accepted=True)
    rows = [_row(ids[0], "income.{p}.annualBonus", i_content=0.001)] + [
        _row(fid, "liabilities[].balance", i_content=0.01 * n, i_pos=0.01 * n) for n, fid in enumerate(ids[2:], start=1)
    ]
    items = prioritise(p, rows, catalog=CATALOG)
    assert [it.priority for it in items] == sorted((it.priority for it in items), reverse=True)
    bonus = next(it for it in items if it.fact_id == ids[0])
    assert bonus.collapsed  # auto-accepted and below half a PoS point
    top = review_list(items)
    assert len(top) == 15 and ids[0] not in top and top[0] == ids[-1]
    annotated = annotate_priorities(p, items)
    f = annotated.facts[ids[-1]]
    assert (
        f.review_priority == pytest.approx(0.2)
        and f.voi["why_text"] == "moves success odds by ~20 points"
        and f.voi["I_content"] == pytest.approx(0.2)
    )
    assert annotated.facts[ids[0]].voi["collapsed"] == 1.0
    side = binding.sidecar(annotated, catalog=CATALOG)
    assert any(v["why"] == "moves success odds by ~20 points" for v in side["fields"].values())
    assert check_profile(annotated) == []
    assert type(f).model_validate_json(f.model_dump_json()).voi["why_text"] == f.voi["why_text"]  # survives a JSON round trip


def _item(**kw):
    base = {
        "fact_id": "x#client",
        "slot_id": "retirement.{p}.retirementAge",
        "tier": "A",
        "status": "stated",
        "p_correct": 0.8,
        "i_content": 0.0,
        "i_extract": 0.0,
        "i_fna": 0.0,
        "i_pos": 0.0,
        "forced": 0.0,
        "priority": 0.0,
    }
    base.update(kw)
    return PriorityItem(**base)


def test_the_clarify_table():
    # extraction risk dominates and p_correct < 0.7 → review item
    d = should_clarify(_item(i_extract=0.05, i_content=0.01, p_correct=0.6, priority=0.11), has_prior=False)
    assert d.action == "review" and d.expected_pos_change == 0.05
    # extraction dominates but p_correct ≥ 0.7: still above threshold → review item
    assert should_clarify(_item(i_extract=0.05, i_content=0.01, p_correct=0.9, priority=0.11), has_prior=False).action == "review"
    # content spread dominates on a stated fact → client follow-up
    d = should_clarify(_item(i_content=0.08, i_extract=0.01, priority=0.1), has_prior=False)
    assert d.action == "followup" and d.expected_pos_change == 0.08
    # not discussed / explicitly unknown on tier A → follow-up, even with no measurable impact
    assert should_clarify(_item(status="explicitly_unknown"), has_prior=True).action == "followup"
    assert should_clarify(_item(status="not_discussed"), has_prior=False).action == "followup"
    # not discussed, but the prior FNA value stands in: a follow-up only when it matters
    assert should_clarify(_item(status="not_discussed"), has_prior=True).action == "none"
    assert should_clarify(_item(status="not_discussed", priority=0.02, i_content=0.02), has_prior=True).action == "followup"
    # tier B not discussed below the threshold → nothing
    assert should_clarify(_item(status="not_discussed", tier="B"), has_prior=False).action == "none"
    # priority below threshold → no action
    assert should_clarify(_item(i_content=0.001, priority=0.001), has_prior=False).action == "none"
    # forced by a contradiction → review, not a client question
    d = should_clarify(
        _item(i_content=0.3, priority=1.3, forced=1.0, forced_reasons=["the call holds two incompatible statements"]), has_prior=False
    )
    assert d.action == "review" and d.rule.startswith("forced")
    # a status flip with content spread → the client can settle it
    d = should_clarify(
        _item(i_content=0.3, priority=1.3, forced=1.0, forced_reasons=["flips the life cover from partial to shortfall"]), has_prior=False
    )
    assert d.action == "followup"


def test_clarify_all_uses_prior_values():
    b = ProfileBuilder()
    seeded = b.seed("{p}.employmentType", "client", point("salaried"))
    p = b.build()
    decisions = clarify_all(
        p,
        [
            _item(fact_id=seeded, slot_id="{p}.employmentType", status="not_discussed"),
            _item(fact_id="retirement.{p}.retirementAge#spouse", status="not_discussed", virtual=True),
        ],
    )
    assert decisions[seeded].action == "none" and decisions["retirement.{p}.retirementAge#spouse"].action == "followup"
