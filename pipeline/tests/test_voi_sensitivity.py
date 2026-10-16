"""Sensitivity runs (§10.2): PoS and FNA views on stubs, and the FNA view against the real engine."""

from __future__ import annotations

import functools
from datetime import date, datetime

import pytest

from advisor_intel import binding, stats
from advisor_intel.extract.reducer import apply_state_ops, make_ops
from advisor_intel.fna_engine.analysis import analyse as fna_analyse
from advisor_intel.models import fna as F
from advisor_intel.models.dists import Bernoulli, Categorical, Triangular
from advisor_intel.models.facts import Hypothesis
from advisor_intel.voi.sensitivity import NeedPoint, fact_ends, fna_view, need_table, sensitivity, status_for

from .conftest import load_fixture
from .verify_factories import CATALOG, PROV, ProfileBuilder, ev, money, qty

SAMPLE_EXPORTED_AT = datetime.fromisoformat("2026-10-16T18:49:32.222+00:00")  # as in test_fna_parity
ANCHOR = date(2026, 10, 16)


# --------------------------------------------------------------------------- the real engine


def test_need_table_reads_the_engine_and_the_typescript_shapes_alike():
    doc = F.FnaDocument.model_validate(load_fixture("sample_fna.json"))
    model = fna_analyse(doc, today=SAMPLE_EXPORTED_AT)
    from_model = need_table(model)
    from_dict = need_table(model.model_dump(mode="json"))
    from_ts = need_table(load_fixture("sample_analysis.json"))  # exported by the TypeScript app
    assert (
        set(from_model)
        == set(from_ts)
        == {
            f"{a}:{p}"
            for a in ("life", "disability", "income-protection", "severe-illness", "funeral", "estate", "retirement")
            for p in ("client", "spouse")
        }
        | {"education", "emergency"}
    )
    for k in from_ts:
        assert from_model[k].status == from_dict[k].status == from_ts[k].status, k
        assert from_model[k].shortfall == pytest.approx(from_ts[k].shortfall, abs=1.0), k
        assert from_model[k].need == pytest.approx(from_ts[k].need, abs=1.0), k
    # the sample household: the client's life cover is partly funded, the education need is short
    assert from_ts["life:client"].status == "partial" and from_ts["education"].status == "shortfall"
    assert from_ts["estate:client"].need == pytest.approx(1421152.5) and from_ts["estate:client"].shortfall == pytest.approx(892152.5)


def _sample_profile(extra):
    doc = F.FnaDocument.model_validate(load_fixture("sample_fna.json"))
    prof = binding.load_prior(doc, None, call_id="c1", run_id="wf-1", anchor_date=ANCHOR)
    specs = []
    for slot_id, inst, path, q in extra:
        specs.append(
            (
                "ADD",
                f"{slot_id}#{inst}",
                "s0001",
                {
                    "slot_id": slot_id,
                    "instance_key": inst,
                    "fna_path": path,
                    "status": "stated",
                    "hypotheses": [Hypothesis(value=q, weight=1, rationale="said")],
                    "evidence": [ev("s0001", "x")],
                    "provenance": PROV,
                },
                "extractor",
            )
        )
    return apply_state_ops(prof, make_ops(prof, specs)).profile, doc


async def test_i_fna_and_status_flips_against_the_real_engine():
    income = qty(Triangular(low=60000, mode=100000, high=140000), "ZAR", "monthly")
    age = qty(Triangular(low=55, mode=60, high=68), "age")
    prof, doc = _sample_profile(
        [
            ("income.{p}.grossMonthly", "client", "income.client.grossMonthly", income),
            ("retirement.{p}.retirementAge", "client", "retirement.client.retirementAge", age),
        ]
    )
    analyse = functools.partial(fna_analyse, today=ANCHOR)
    rows = {r.fact_id: r for r in await sensitivity(prof, catalog=CATALOG, analyse=analyse, base_document=doc)}
    inc = rows["income.{p}.grossMonthly#client"]
    lo, hi = stats.quantile(income.dist, 0.1), stats.quantile(income.dist, 0.9)
    assert inc.ends == pytest.approx([lo, hi])
    # recompute I_fna by hand from three engine runs: the median document and the two ends
    median = binding.materialise(prof, "median", base=doc)
    base = need_table(analyse(median))
    ends = []
    for v in (lo, hi):
        d = median.model_dump(mode="json")
        binding.set_path(d, "income.client.grossMonthly", round(v, 2))
        ends.append(need_table(analyse(F.FnaDocument.model_validate(d))))
    by_hand = max(abs(ends[1][k].shortfall - ends[0][k].shortfall) / max(base[k].need, 1.0) for k in base)
    assert inc.i_fna == pytest.approx(by_hand) and inc.i_fna > 0.05
    flips = {
        k
        for k in base
        if (ends[0][k].status if ends[0][k].status != "na" else "covered")
        != (ends[1][k].status if ends[1][k].status != "na" else "covered")
    }
    assert inc.status_flip == bool(flips) and {f.split(" ")[0] for f in inc.flips} == flips
    assert inc.i_content == inc.i_fna and inc.top_need is not None
    ra = rows["retirement.{p}.retirementAge#client"]
    assert ra.i_fna > 0 and ra.top_need.startswith("retirement")
    # facts held at a single prior value have no spread to move across
    assert rows["{p}.dateOfBirth#client"].i_fna == 0 and rows["{p}.dateOfBirth#client"].note == "no spread to move across"


# --------------------------------------------------------------------------- stubs: the maths


def _stub_analyse(doc: F.FnaDocument) -> dict:
    """Retirement shortfall falls by R1m per year of retirement age; need R10m; status follows funded."""
    age = doc.retirement["client"].retirementAge
    shortfall = max(0.0, 10_000_000 - (age - 55) * 1_000_000)
    funded = 1 - shortfall / 10_000_000
    return {
        "persons": {
            "client": {"retirement": {"shortfall": shortfall, "requiredCapital": 10_000_000, "status": status_for(funded, 10_000_000)}}
        }
    }


async def test_voi_maths_on_stub_pos_runs_and_analyse():
    b = ProfileBuilder()
    ra = b.add("retirement.{p}.retirementAge", "client", qty(Triangular(low=55, mode=60, high=65), "age"))
    inc = b.add("income.{p}.grossMonthly", "client", money(45000))
    p = b.build()
    from advisor_intel.extract.reducer import annotate

    p = annotate(p, ra, p_correct=0.8)
    p = annotate(p, inc, p_correct=0.6)
    calls = {}

    def pos_runs(profile, fact_ids, *, n_paths, seed):
        calls.update(n_paths=n_paths, seed=seed, ids=list(fact_ids))
        return {
            ra: {"pos_base": 0.70, "pos_q10": 0.60, "pos_q90": 0.81, "pos_prior": 0.66},
            inc: {"pos_base": 0.70, "pos_q10": 0.70, "pos_q90": 0.70, "pos_prior": 0.55},
        }

    rows = {
        r.fact_id: r for r in await sensitivity(p, catalog=CATALOG, analyse=_stub_analyse, pos_runs=pos_runs, seed=7, include_missing=False)
    }
    assert calls["n_paths"] == 1000 and calls["seed"] == 7 and set(calls["ids"]) == {ra, inc}
    r = rows[ra]
    assert r.i_pos == pytest.approx(0.21) and r.i_extract == pytest.approx(0.2 * 0.04)
    lo, hi = stats.quantile(Triangular(low=55, mode=60, high=65), 0.1), stats.quantile(Triangular(low=55, mode=60, high=65), 0.9)
    # the document stores whole ages: the ends round to 57 and 63 → shortfalls 8m and 2m on a 10m need
    assert (round(lo), round(hi)) == (57, 63)
    assert r.i_fna == pytest.approx((8_000_000 - 2_000_000) / 10_000_000)
    assert r.status_flip and r.flips == ["retirement:client shortfall→partial"]
    assert r.i_content == pytest.approx(max(0.21, 0.6))
    i = rows[inc]
    assert i.i_pos == 0 and i.i_extract == pytest.approx(0.4 * 0.15) and i.i_fna == 0  # a point value: no content spread


async def test_async_pos_runs_and_failures_degrade_to_the_other_view():
    b = ProfileBuilder()
    ra = b.add("retirement.{p}.retirementAge", "client", qty(Triangular(low=55, mode=60, high=65), "age"))
    p = b.build()

    async def pos_runs(profile, fact_ids, *, n_paths, seed):
        return {ra: {"pos_base": 0.5, "pos_q10": 0.4, "pos_q90": 0.6, "pos_prior": 0.5}}

    rows = await sensitivity(p, catalog=CATALOG, analyse=None, pos_runs=pos_runs, include_missing=False)
    assert rows[0].i_pos == pytest.approx(0.2) and rows[0].i_fna == 0

    def broken(profile, fact_ids, **kw):
        raise RuntimeError("forecast down")

    rows = await sensitivity(p, catalog=CATALOG, analyse=_stub_analyse, pos_runs=broken, include_missing=False)
    assert rows[0].i_pos == 0 and rows[0].i_fna > 0


def test_ends_for_numeric_categorical_and_boolean_facts():
    b = ProfileBuilder()
    cat = b.add("{p}.employmentType", "client", qty(Categorical(probs={"salaried": 0.55, "self-employed": 0.4, "contract": 0.05}), "none"))
    sure = b.add("{p}.smoker", "client", qty(Bernoulli(p=0.97), "none"))
    maybe = b.add("dependants[].tertiary", "person.neo", qty(Bernoulli(p=0.6), "none"))
    p = b.build()
    assert fact_ends(p.facts[cat], CATALOG.slot("{p}.employmentType")) == ["salaried", "self-employed"]  # outcomes with ≥ 10%
    assert fact_ends(p.facts[sure], CATALOG.slot("{p}.smoker")) == []  # 97% either way: no spread
    assert fact_ends(p.facts[maybe], CATALOG.slot("dependants[].tertiary")) == [False, True]


def test_fna_view_picks_the_most_different_pair_and_flags_flips():
    base = {"life:client": NeedPoint(shortfall=1e6, need=4e6, status="partial")}
    a = {"life:client": NeedPoint(shortfall=0.5e6, need=4e6, status="partial")}
    b = {"life:client": NeedPoint(shortfall=2.5e6, need=4e6, status="shortfall")}
    c = {"life:client": NeedPoint(shortfall=0.0, need=0.0, status="na")}
    i_fna, flip, flips, top, delta = fna_view(base, [a, b])
    assert i_fna == pytest.approx(0.5) and flip and flips == ["life:client partial→shortfall"] and top == "life:client" and delta == 2e6
    covered = {"life:client": NeedPoint(shortfall=0.0, need=4e6, status="covered")}
    assert not fna_view(base, [covered, c])[1]  # covered and n/a both leave nothing to recommend


async def test_candidates_include_missing_tier_a_fields_and_forecast_only_slots():
    b = ProfileBuilder()
    b.entity("goal.study", "goal", "Study overseas")
    ev_id = b.add("event[].occurs", "goal.study", qty(Bernoulli(p=0.4), "none"))
    p = b.build()
    rows = {r.fact_id: r for r in await sensitivity(p, catalog=CATALOG, analyse=_stub_analyse)}
    assert rows[ev_id].note == "not in the FnaDocument: PoS view only"
    missing = rows["retirement.{p}.retirementAge#client"]  # a tier A field the call never filled
    assert missing.virtual and missing.reasons == ["tier A", "missing"] and missing.i_fna > 0  # moved across its population prior
    assert "{p}.occupation#client" not in rows  # text slots have no VOI
