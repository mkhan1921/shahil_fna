"""Consistency probes (§9.1b)."""

from __future__ import annotations

import pytest

from advisor_intel.models.dists import Categorical, Triangular
from advisor_intel.verify.consistency import ProbeResult, run_consistency

from .verify_factories import CATALOG, ProfileBuilder, money, point, qty


def _scripted(answers):
    """reextract(fact, variant) → answers[fact_id][variant] (an Exception is raised)."""
    calls = []

    async def reextract(fact, variant):
        calls.append((fact.fact_id, variant))
        a = answers[fact.fact_id][variant]
        if isinstance(a, Exception):
            raise a
        return a

    return reextract, calls


async def test_numeric_agreement_is_share_of_probe_medians_inside_p10_p90():
    b = ProfileBuilder()
    fid = b.add("retirement.{p}.retirementAge", "client", qty(Triangular(low=58, mode=62, high=66), "age"))
    p = b.build()
    answers = {
        fid: [
            ProbeResult(status="stated", value=point(62, "age")),
            ProbeResult(status="stated", value=qty(Triangular(low=59, mode=60, high=61), "age")),
            ProbeResult(status="stated", value=point(70, "age")),
        ]
    }
    reextract, calls = _scripted(answers)
    res = (await run_consistency(p, [fid], catalog=CATALOG, reextract=reextract))[fid]
    assert sorted(v for _, v in calls) == [0, 1, 2]
    assert res.kind == "numeric" and res.consistency == pytest.approx(2 / 3)
    assert res.status_agreement == 1.0 and [x["agrees"] for x in res.probes] == [True, True, False]


async def test_monthly_and_annual_probes_are_compared_in_one_unit():
    b = ProfileBuilder()
    fid = b.add("income.{p}.grossMonthly", "client", qty(Triangular(low=40000, mode=45000, high=50000), "ZAR", "monthly"))
    p = b.build()
    reextract, _ = _scripted(
        {
            fid: [
                ProbeResult(status="stated", value=money(540000, "annual")),
                ProbeResult(status="implied", value=money(45000)),
                ProbeResult(status="not_discussed"),
            ]
        }
    )
    res = (await run_consistency(p, [fid], catalog=CATALOG, reextract=reextract))[fid]
    assert res.consistency == pytest.approx(2 / 3)  # a probe without a value never agrees
    assert res.status_agreement == pytest.approx(1 / 3)


async def test_categorical_uses_argmax_and_status_facts_use_status():
    b = ProfileBuilder()
    emp = b.add("{p}.employmentType", "client", qty(Categorical(probs={"self-employed": 0.7, "contract": 0.3}), "none"))
    unk = b.add("income.{p}.annualBonus", "client", None, status="explicitly_unknown", evidence=[])
    p = b.build()
    reextract, _ = _scripted(
        {
            emp: [
                ProbeResult(status="stated", value=point("self-employed")),
                ProbeResult(status="stated", value=point("contract")),
                ProbeResult(status="stated", value=qty(Categorical(probs={"self-employed": 0.55, "salaried": 0.45}), "none")),
            ],
            unk: [ProbeResult(status="explicitly_unknown"), ProbeResult(status="explicitly_unknown"), ProbeResult(status="not_discussed")],
        }
    )
    res = await run_consistency(p, [emp, unk], catalog=CATALOG, reextract=reextract)
    assert res[emp].kind == "categorical" and res[emp].consistency == pytest.approx(2 / 3)
    assert res[unk].kind == "status" and res[unk].consistency == pytest.approx(2 / 3)


async def test_tier_c_is_nan_and_failed_probes_are_left_out():
    b = ProfileBuilder()
    occ = b.add("{p}.occupation", "client", point("engineer"))
    smoker = b.add("{p}.smoker", "client", point(False))  # tier B boolean
    p = b.build()
    reextract, calls = _scripted(
        {
            smoker: [
                ProbeResult(status="stated", value=point(False)),
                RuntimeError("timeout"),
                ProbeResult(status="stated", value=point(True)),
            ]
        }
    )
    res = await run_consistency(p, [occ, smoker], catalog=CATALOG, reextract=reextract)
    assert res[occ].consistency is None and res[occ].skipped == "tier C"
    assert all(fid != occ for fid, _ in calls)
    assert res[smoker].answered == 2 and res[smoker].consistency == pytest.approx(0.5)
    assert res[smoker].failures == 1 and res[smoker].error == "RuntimeError: timeout" and res[smoker].skipped is None
    all_fail, _ = _scripted({smoker: [RuntimeError("x")] * 3})
    dead = (await run_consistency(p, [smoker], catalog=CATALOG, reextract=all_fail))[smoker]
    assert dead.consistency is None and dead.failures == 3 and dead.skipped == "no probe answered (3 failed)"
    assert dead.verification()["consistency_detail"]["failures"] == 3  # the record on the fact says the probes failed, not "no spread"
