"""§15.3 metrics against hand-computed values."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from advisor_intel.evals import scorers as S
from advisor_intel.evals.gold import GoldCall, GoldEntity, GoldFact, GoldMention, GoldValue, Span
from advisor_intel.evals.records import EvalRecord, NoteLine, SystemCallOutput, SystemEntity, SystemFact, SystemFlag, ValueSummary
from advisor_intel.schema.slots import fact_id_for, load_catalog

CAT = load_catalog()
FIXTURES = Path(__file__).parent / "fixtures"


def num(p50, p10=None, p90=None):
    return ValueSummary(kind="numeric", p10=p50 if p10 is None else p10, p50=p50, p90=p50 if p90 is None else p90)


def gnum(p50, p10=None, p90=None, vague=False):
    return GoldValue(kind="numeric", p10=p10, p50=p50, p90=p90, vague=vague)


def span(seg, a, b, transcript="human", t=None):
    t0, t1 = t if t else (None, None)
    return Span(segment_id=seg, char_start=a, char_end=b, quote="x" * (b - a), t_start=t0, t_end=t1, transcript=transcript)


def sf(
    slot_id,
    inst=None,
    status="stated",
    value=None,
    spans=(),
    attribution="client",
    p=0.9,
    auto=False,
    source="call",
    features=None,
    form=None,
):
    slot = CAT.slot(slot_id)
    return SystemFact(
        fact_id=fact_id_for(slot_id, inst),
        slot_id=slot_id,
        instance_key=inst,
        status=status,
        value=value,
        spans=list(spans),
        attribution=attribution,
        p_correct=p,
        auto_accepted=auto,
        source=source,
        group=slot.group,
        tier=slot.sensitivity_tier,
        features=features or {},
        form_type=form,
    )


def gf(slot_id, inst=None, status="stated", value=None, spans=(), attribution="client"):
    return GoldFact(slot_id=slot_id, instance_key=inst, status=status, value=value, spans=list(spans), attribution=attribution)


RA, INC, SMOKER, MARITAL, WILL = (
    "retirement.{p}.retirementAge",
    "income.{p}.grossMonthly",
    "{p}.smoker",
    "household.maritalStatus",
    "estate.{p}.hasWill",
)


def small_case():
    gold = GoldCall(
        call_id="c1",
        anchor_date=date(2026, 10, 15),
        transcript="human",
        facts=[
            gf(RA, "client", value=gnum(65), spans=[span("s1", 10, 20)]),
            gf(INC, "client", value=gnum(50_000, 45_000, 55_000, vague=True), spans=[span("s2", 0, 12)]),
            gf(SMOKER, "client", value=GoldValue(kind="boolean", label="false"), spans=[span("s3", 0, 5)]),
            gf(MARITAL, None, status="not_discussed"),
        ],
    )
    system = SystemCallOutput(
        call_id="c1",
        transcript_kind="human",
        facts=[
            sf(RA, "client", value=num(62, 60, 64), spans=[span("s1", 15, 25)]),
            sf(
                INC,
                "client",
                value=num(50_000, 44_000, 56_000),
                spans=[span("s9", 0, 5)],
                attribution="advisor_suggested",
                form="AMOUNT_APPROX",
            ),
            sf(WILL, "client", value=ValueSummary(kind="categorical", label="yes", probs={"yes": 1.0}), spans=[span("s4", 0, 3)]),
        ],
    )
    return system, gold


def test_extraction_metrics_on_a_hand_made_call():
    system, gold = small_case()
    # keys: RA ✓, INC ✓, SMOKER (missed) ✗, MARITAL (both not discussed) ✓, WILL (invented) ✗
    st = S.status_accuracy(system, gold)
    assert (st.hits, st.total) == (3, 5) and st.value == pytest.approx(0.6)
    assert any("invented estate.client.hasWill" in m for m in st.misses) and any(
        "missed {p}.smoker".replace("{p}", "client") in m for m in st.misses
    )
    # tier A: RA, INC, MARITAL, WILL (smoker is tier B)
    assert S.status_accuracy(system, gold, tier="A").value == pytest.approx(3 / 4)
    rec = S.slot_recall(system, gold)
    assert (rec.hits, rec.total) == (2, 3)
    cov = S.value_coverage(system, gold)
    assert (cov.hits, cov.total) == (1, 2)  # 65 outside 60–64; 50k inside 44k–56k
    assert any("retirement.client.retirementAge" in m and "60–64" in m for m in cov.misses)
    gr = S.grounding(system, gold)
    assert (gr.hits, gr.total) == (1, 3)  # RA overlaps; INC on the wrong segment; WILL was never discussed
    att = S.attribution_accuracy(system, gold)
    assert (att.hits, att.total) == (1, 2) and "marked the client's statement as an advisor suggestion" in att.misses[0]
    f1 = S.span_f1(system, gold)
    # RA: [15,25) vs [10,20) → overlap 5 of 10+10; INC: no overlap
    assert f1.value == pytest.approx((0.5 + 0.0) / 2)


def test_value_coverage_rules():
    assert S.covers(num(60, 58, 63), gnum(62))
    assert not S.covers(num(60, 58, 61), gnum(62))
    assert S.covers(ValueSummary(kind="categorical", label="Salaried"), GoldValue(kind="categorical", label="salaried"))
    assert S.covers(ValueSummary(kind="categorical", label="3.0"), GoldValue(kind="categorical", label="3"))
    assert S.covers(ValueSummary(kind="boolean", p_true=0.7), GoldValue(kind="boolean", label="true"))
    assert not S.covers(ValueSummary(kind="boolean", p_true=0.4), GoldValue(kind="boolean", label="true"))
    assert S.covers(ValueSummary(kind="probability", p_true=0.4), gnum(0.4, 0.25, 0.55))
    assert not S.covers(ValueSummary(kind="probability", p_true=0.9), gnum(0.4, 0.25, 0.55))
    assert S.covers(
        ValueSummary(kind="text", label="worried the life cover is too low"),
        GoldValue(kind="text", label="Worried the life cover is too low"),
    )


def test_entities_align_by_row_id_then_by_name():
    gold = GoldCall(
        call_id="c",
        anchor_date=date(2026, 1, 1),
        facts=[
            gf("dependants[].tertiaryStartAge", "person.thandi", value=gnum(18)),
            gf("policies[].cover", "policy.life", value=gnum(2_000_000)),
        ],
        entities=[
            GoldEntity(entity_id="person.thandi", type="person", fna_list="dependants", names=["Thandi"]),
            GoldEntity(entity_id="policy.life", type="policy", fna_list="policies", fna_id="row-7", names=["Protea Life life cover"]),
        ],
    )
    system = SystemCallOutput(
        call_id="c",
        facts=[
            sf("dependants[].tertiaryStartAge", "person.daughter_1", value=num(18)),
            sf("policies[].cover", "policy.protea_life_life_1", value=num(2_000_000)),
            sf("dependants[].tertiaryStartAge", "person.ghost", value=num(19)),
        ],
        entities=[
            SystemEntity(entity_id="person.daughter_1", type="person", name="Thandi Mokoena", aliases=["my girl"]),
            SystemEntity(entity_id="policy.protea_life_life_1", type="policy", name="Protea Life", fna_list="policies", fna_id="row-7"),
            SystemEntity(entity_id="person.ghost", type="person", name="Sipho"),
        ],
    )
    al = S.align(system, gold)
    assert al.entity_map == {"person.daughter_1": "person.thandi", "policy.protea_life_life_1": "policy.life"}
    assert S.slot_recall(system, gold).value == 1.0
    st = S.status_accuracy(system, gold)
    assert st.total == 3 and st.hits == 2  # the unmatched dependant is an invented row


def test_span_overlap_by_time_across_transcripts():
    asr = [span("a7", 0, 4, transcript="asr", t=(10.0, 12.0))]
    human = [span("h3", 30, 40, transcript="human", t=(11.0, 13.0))]
    assert S.span_overlap_f1(asr, human) == pytest.approx(2 * 1.0 / 4.0)
    assert S._overlaps(asr[0], human[0])  # noqa: SLF001
    assert not S._overlaps(span("a7", 0, 4, transcript="asr", t=(1, 2)), human[0])  # noqa: SLF001


def test_ece_brier_and_cold_start():
    assert S.ece([0.2, 0.2, 0.8, 0.8], [0, 1, 1, 1]) == pytest.approx((0.2 + 0.8 + 0.2 + 0.2) / 4)
    # 30 rows in 15 equal-mass bins of 2: perfectly calibrated pairs give 0
    p = np.repeat(np.linspace(0.05, 0.95, 15), 2)
    y = np.array([0, 1] * 15)
    assert S.ece(p.tolist(), y.tolist()) == pytest.approx(float(np.mean(np.abs(np.linspace(0.05, 0.95, 15) - 0.5))))
    assert S.brier([0.9, 0.2], [1, 0]) == pytest.approx((0.01 + 0.04) / 2)
    assert S.ece([], []) is None and S.brier([], []) is None
    rows = [
        S.LabelRow(
            "c1", "income", "x", "A", 0.9, 1, {"entail_score": 1.0, "consistency": 1.0, "self_score": 1.0, "asr_mean_conf": 1.0}, False
        ),
        S.LabelRow(
            "c1", "income", "x", "A", 0.9, 0, {"entail_score": 0.0, "consistency": None, "self_score": 0.0, "asr_mean_conf": 0.0}, False
        ),
    ]
    s = S.cold_start_scores(rows)
    # the missing consistency takes the group mean (1.0)
    assert s.tolist() == pytest.approx([1.0, 0.3])


def _record(system, gold, kind="asr"):
    return EvalRecord(system=system, gold=gold, metadata={"transcript_kind": kind})


def test_conformal_and_forecast_summaries():
    system, gold = small_case()
    wrong_auto = system.model_copy(
        update={"facts": [f.model_copy(update={"auto_accepted": f.slot_id == RA}) for f in system.facts], "pos": 0.70}
    )
    right_auto = system.model_copy(
        update={"facts": [f.model_copy(update={"auto_accepted": f.slot_id == INC}) for f in system.facts], "pos": 0.80}
    )
    recs = [
        _record(wrong_auto, gold.model_copy(update={"pos": 0.75})),
        _record(right_auto, gold.model_copy(update={"call_id": "c2", "pos": 0.70})),
    ]
    w = S.wrong_auto_accept_share(recs)
    assert (w.hits, w.total) == (1, 2)
    assert S.auto_accept_rate(recs).value == pytest.approx(2 / 6)
    pe = S.pos_error(recs)
    assert pe["pos_error_median"] == pytest.approx(7.5) and pe["pos_error_p90"] == pytest.approx(5 + 0.9 * 5)
    # without a gold PoS, the gold profile is forecast on the system run's seed (common random numbers)
    seen = []

    def gold_pos(g, seed=None):
        seen.append((g.call_id, seed))
        return 0.60

    unpriced = [
        _record(right_auto.model_copy(update={"forecast_seed": 77}), gold),
        _record(system, gold.model_copy(update={"call_id": "c3"})),
    ]
    assert S.pos_error(unpriced, gold_pos=gold_pos)["pos_error_median"] == pytest.approx(20.0)
    assert seen == [("c1", 77)]  # a run without a forecast is not priced
    assert S.pos_error(unpriced, gold_pos=lambda g: 0.8)["pos_error_median"] == pytest.approx(0.0)  # a one-argument callable works too


def test_need_table_reads_the_app_analysis():
    analysis = json.loads((FIXTURES / "sample_analysis.json").read_text())
    t = S.need_table(analysis)
    assert t["life:client"]["status"] == "partial" and t["life:client"]["shortfall"] == pytest.approx(4415696.429240722)
    assert t["estate:client"] == {"need": pytest.approx(1421152.5), "shortfall": pytest.approx(892152.5), "status": "shortfall"}
    assert t["education"]["status"] == "shortfall" and t["emergency"]["need"] == 230472
    assert {"retirement:client", "retirement:spouse", "funeral:spouse", "income-protection:client"} <= set(t)


def test_fna_agreement_with_an_injected_engine(sample_fna_json):
    from advisor_intel.models.fna import FnaDocument

    analysis = json.loads((FIXTURES / "sample_analysis.json").read_text())
    worse = json.loads(json.dumps(analysis))
    worse["persons"]["client"]["death"]["status"] = "shortfall"
    worse["persons"]["client"]["death"]["shortfall"] += 1_236_334.6  # 10% of the need

    def analyse(doc):
        return worse if doc.notes == "system" else analysis

    doc = FnaDocument.model_validate(sample_fna_json)
    system = SystemCallOutput(call_id="c", document=doc.model_copy(update={"notes": "system"}).model_dump(mode="json"))
    gold = GoldCall(call_id="c", anchor_date=date(2026, 1, 1), document=doc)
    out = S.fna_agreement([_record(system, gold)], analyse=analyse)
    n = out["n_needs"]
    assert out["need_status_agreement"] == pytest.approx((n - 1) / n)
    assert out["shortfall_error_by_need"]["life"] == pytest.approx(0.05)  # median of {0.1, 0}
    if S.default_analyse() is None:  # the FNA engine port is not installed: the metric is skipped, not faked
        assert S.fna_agreement([_record(system, gold)]) == {}


def test_critical_misses_and_flag_recall():
    system, gold = small_case()
    misses = S.critical_misses(system, gold)
    assert {p.key for p in misses} == {(RA, "client"), (WILL, "client")}
    flagged = system.model_copy(update={"flags": [SystemFlag(kind="tier_a_missing", fact_id=fact_id_for(RA, "client"))]})
    out = S.summary_scores([_record(flagged, gold)])
    assert out["critical_miss_rate"] == 1.0 and out["flag_recall"] == pytest.approx(0.5)


def test_meeting_note_completeness_and_support():
    system, gold = small_case()
    ra_id, inc_id = fact_id_for(RA, "client"), fact_id_for(INC, "client")
    note = [
        NoteLine(text="Thabo wants to retire at about 62.", fact_ids=[ra_id]),
        NoteLine(text="He earns R50,000 a month.", fact_ids=[inc_id]),
        NoteLine(text="He earns R90,000 a month.", fact_ids=[inc_id]),  # a number no cited fact supports
        NoteLine(text="They seem happy.", fact_ids=[]),
    ]
    system = system.model_copy(update={"note": note})
    complete, supported = S.note_quality(system, gold)
    assert (complete.hits, complete.total) == (2, 2)  # the tier A stated gold facts: RA and income
    assert (supported.hits, supported.total) == (2, 4)
    assert S.unsupported_sentences(system) == ["He earns R90,000 a month.", "They seem happy."]


def test_discourse_metrics():
    gold = GoldCall(
        call_id="c",
        anchor_date=date(2026, 1, 1),
        entities=[GoldEntity(entity_id="person.thandi", fna_list="dependants", names=["Thandi"])],
        mentions=[
            GoldMention(segment_id="s1", char_start=0, char_end=3, surface="she", entity_id="person.thandi"),
            GoldMention(segment_id="s2", char_start=4, char_end=8, surface="that", entity_id=None),
        ],
        topic_labels={"s1": ["education", "dependants"], "s2": ["income"]},
    )
    system = SystemCallOutput(
        call_id="c",
        entities=[SystemEntity(entity_id="person.d1", type="person", name="Thandi")],
        mentions=[
            GoldMention(segment_id="s1", char_start=0, char_end=3, entity_id="person.d1"),
            GoldMention(segment_id="s2", char_start=5, char_end=7, entity_id="person.d1"),
        ],
        topic_index={"education": ["s1"], "income": ["s2", "s3"]},
    )
    m = S.mention_accuracy(system, gold)
    assert (m.hits, m.total) == (1, 2)
    t = S.topic_recall(system, gold)
    assert (t.hits, t.total) == (2, 3)


def test_asr_metrics_pass_through_from_ingest(monkeypatch):
    if importlib.util.find_spec("advisor_intel.ingest") is None:
        assert S.asr_metrics(None, None) == {}
    fake = types.ModuleType("advisor_intel.ingest.metrics")
    fake.numeric_wer = lambda ref, hyp: 0.06
    fake.der = lambda ref, hyp: 0.08
    fake.role_accuracy = lambda ref, hyp: 0.99
    pkg = types.ModuleType("advisor_intel.ingest")
    pkg.metrics = fake
    monkeypatch.setitem(sys.modules, "advisor_intel.ingest", pkg)
    monkeypatch.setitem(sys.modules, "advisor_intel.ingest.metrics", fake)
    assert S.asr_metrics("ref", "hyp") == {"numeric_wer": 0.06, "der": 0.08, "role_accuracy": 0.99}
    out = SystemCallOutput(call_id="c", transcript_metrics={"numeric_wer": 0.06, "der": 0.08, "role_accuracy": 0.99})
    m = S.item_metrics(out, GoldCall(call_id="c", anchor_date=date(2026, 1, 1)))
    assert m["numeric_word_accuracy"].value == pytest.approx(0.94) and m["diarization_accuracy"].value == pytest.approx(0.92)


def test_operations_metrics():
    r = S.review_minutes_per_hour([(300, 30), (600, 60), (1200, 60)])
    assert r.value == pytest.approx(10.0)  # 10, 10, 20 minutes per 60-minute call
    a = SystemCallOutput(call_id="a", usage={"cost_usd": 3.0}, call_minutes=60)
    b = SystemCallOutput(call_id="b", usage={"cost_usd": 1.0}, call_minutes=20)
    assert S.cost_per_call_minute([a, b]) == pytest.approx(4.0 / 80)
    assert S.voi_usefulness([4, 5, 3]) == pytest.approx(4.0)


def test_interval_coverage_and_asr_gap():
    system, gold = small_case()
    human = _record(system, gold, "human")
    asr_sys = system.model_copy(
        update={"facts": [f.model_copy(update={"value": num(40_000, 39_000, 41_000)}) if f.slot_id == INC else f for f in system.facts]}
    )
    asr = _record(asr_sys, gold.model_copy(update={"call_id": "c1-asr"}), "asr")
    out = S.summary_scores([human, asr])
    assert out["interval_coverage_vague"] == pytest.approx(0.5)  # income is vague: covered on human, not on ASR
    assert out["asr_human_gap"] == pytest.approx(0.5 - 0.0)
    assert out["by_group"]["income"]["value_coverage"] == pytest.approx(0.5)


def test_braintrust_adapters_return_named_scores():
    system, gold = small_case()
    out, exp = system.model_dump(mode="json"), gold.model_dump(mode="json")
    scores = S.status_accuracy_scorer(input={}, output=out, expected=exp)
    assert [s.name for s in scores] == ["status_accuracy", "status_accuracy_tier_a"]
    assert scores[0].score == pytest.approx(0.6) and scores[0].metadata["total"] == 5
    assert S.status_accuracy_scorer._name() == "status_accuracy"  # noqa: SLF001
    assert all(s.score is None for s in S.asr_scorer(input={}, output=out, expected=exp))
    assert all(s.score is None for s in S.slot_recall_scorer(input={}, output=None, expected=exp))
    assert len(set(S.ITEM_SCORE_NAMES)) == len(S.ITEM_SCORE_NAMES)
    assert S.TIER_A_METRICS == [m for m in S.ITEM_SCORE_NAMES if m.endswith("_tier_a")]
    recs = S.records_from_rows(
        [{"output": out, "expected": exp, "metadata": {"transcript_kind": "human"}}, {"output": None, "expected": exp}]
    )
    assert len(recs) == 1 and recs[0].transcript_kind == "human"
