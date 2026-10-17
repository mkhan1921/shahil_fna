"""Weekly error analysis (§15.5): errors from review and gold, HDBSCAN clusters, small-model labels, the sample and the sheet."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest
from pydantic_ai import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from advisor_intel.evals import error_analysis as EA
from advisor_intel.evals.datasets import LocalStore, error_record, sync_error_dataset
from advisor_intel.evals.gold import GoldCall, GoldFact, GoldValue, Span
from advisor_intel.evals.records import EvalRecord, SystemCallOutput, SystemFact, ValueSummary
from advisor_intel.models.runs import VersionPins
from advisor_intel.schema.slots import fact_id_for, load_catalog

CAT = load_catalog()
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
RA, INC, SMOKER, WILL = "retirement.{p}.retirementAge", "income.{p}.grossMonthly", "{p}.smoker", "estate.{p}.hasWill"


def row(fact_id, action, reason, *, quotes=(), note=None, features=None, at=NOW, slot_id=None):
    """A row as the nightly activity builds it from the review_events table."""
    return {
        "run_id": "wf-1",
        "fact_id": fact_id,
        "slot_id": slot_id or fact_id.split("#")[0],
        "action": action,
        "reason": reason,
        "quotes": list(quotes),
        "value": None,
        "interpretation": None,
        "note": note,
        "reviewer_id": "adv-1",
        "at": at.isoformat(),
        "features": features or {},
    }


PRODUCTION = [
    row(f"{INC}#client", "reject", "misheard", quotes=["I earn fifteen thousand"], note="fifty, not fifteen"),
    row(f"{RA}#client", "reject", "advisor_said_it", quotes=["what about sixty-five"], note="that was my suggestion"),
    row(f"{INC}#spouse", "reject", "wrong_person", quotes=["she earns forty"], features={"diarization_conf": 0.4, "role_conf": 0.9}),
    row(
        f"{INC}#client",
        "reattribute",
        "wrong_person",
        quotes=["my brother earns forty"],
        features={"diarization_conf": 0.95, "role_conf": 0.95},
    ),
    row(f"{SMOKER}#client", "add", "missed_slot"),
    row(f"{RA}#spouse", "reject", "not_what_they_meant", quotes=["sometime in my sixties"], features={"hedge_strength_max": 0.7}),
    row(f"{RA}#client", "reject", "other", quotes=["in ten years"], features={"derived": 1.0}),
    row(f"{INC}#spouse", "reject", "other", quotes=["two point five"], features={"scale_ambiguous": 1.0}),
    row(f"{WILL}#client", "reject", "misheard", quotes=["old"], at=datetime(2026, 8, 1, tzinfo=UTC)),
]
EXPECTED_CAUSES = ["asr", "attribution", "diarization", "coreference", "extraction_miss", "lexicon", "derivation", "wrong_scale", "asr"]


def test_errors_from_production_rows_with_suggested_causes():
    records = [error_record(r) for r in PRODUCTION]
    errors = EA.errors_from_dataset(records)
    assert [EA.suggest_cause(e) for e in errors] == EXPECTED_CAUSES
    e = errors[0]
    assert (e.source, e.slot_id, e.group, e.tier, e.kind, e.reason) == ("production", INC, "income", "A", "rejected", "misheard")
    assert e.quotes == ["I earn fifteen thousand"] and e.note == "fifty, not fifteen"
    assert e.text == '"I earn fifteen thousand" · fifty, not fifteen'
    assert errors[4].kind == "missed" and errors[4].text == f"missed {SMOKER}"
    recent = EA.errors_from_dataset(records, since=datetime(2026, 10, 9, tzinfo=UTC))
    assert len(recent) == len(PRODUCTION) - 1  # the August rejection is outside the week


def span(seg, a, b):
    return Span(segment_id=seg, char_start=a, char_end=b, quote=f"{seg}:{a}-{b}", transcript="human")


def sf(slot_id, inst, value, *, status="stated", spans=(), attribution="client", features=None):
    slot = CAT.slot(slot_id)
    return SystemFact(
        fact_id=fact_id_for(slot_id, inst),
        slot_id=slot_id,
        instance_key=inst,
        status=status,
        value=value,
        spans=list(spans),
        attribution=attribution,
        p_correct=0.9,
        group=slot.group,
        tier=slot.sensitivity_tier,
        features=features or {},
    )


def num(x):
    return ValueSummary(kind="numeric", p10=x, p50=x, p90=x)


def gold_case():
    gold = GoldCall(
        call_id="g1",
        anchor_date=date(2026, 10, 15),
        transcript="human",
        facts=[
            GoldFact(
                slot_id=RA,
                instance_key="client",
                status="stated",
                value=GoldValue(kind="numeric", p50=65),
                spans=[span("s1", 0, 10)],
                attribution="advisor_suggested",
            ),
            GoldFact(
                slot_id=INC, instance_key="client", status="stated", value=GoldValue(kind="numeric", p50=50_000), spans=[span("s2", 0, 12)]
            ),
            GoldFact(
                slot_id=SMOKER,
                instance_key="client",
                status="stated",
                value=GoldValue(kind="boolean", label="false"),
                spans=[span("s3", 0, 5)],
            ),
            GoldFact(
                slot_id=RA, instance_key="spouse", status="implied", value=GoldValue(kind="numeric", p50=63), spans=[span("s4", 0, 9)]
            ),
        ],
    )
    system = SystemCallOutput(
        call_id="g1",
        transcript_kind="human",
        facts=[
            sf(RA, "client", num(65), spans=[span("s1", 2, 8)]),
            sf(INC, "client", num(50), spans=[span("s2", 0, 12)], features={"asr_mean_conf": 0.95}),
            sf(WILL, "client", ValueSummary(kind="categorical", label="yes", probs={"yes": 1.0}), spans=[span("s7", 0, 3)]),
            sf(RA, "spouse", num(63), spans=[span("s9", 0, 4)]),
        ],
    )
    return EvalRecord(system=system, gold=gold, metadata={"transcript_kind": "human"})


def test_errors_from_a_gold_experiment():
    errors = EA.errors_from_records([gold_case()])
    got = {(e.slot_id, e.fact_id.split("#")[-1], e.kind): e for e in errors}
    assert set(got) == {
        (RA, "client", "attribution"),
        (INC, "client", "wrong_value"),
        (SMOKER, "client", "missed"),
        (WILL, "client", "invented"),
        (RA, "spouse", "wrong_status"),
    }
    assert got[(RA, "client", "attribution")].reason == "advisor_as_client"
    scale = got[(INC, "client", "wrong_value")]
    assert scale.features["scale_mismatch"] == 1.0 and "extracted 50, gold 5e+04" in scale.detail
    assert got[(SMOKER, "client", "missed")].quotes == ["s3:0-5"]  # the gold span's words
    assert got[(RA, "spouse", "wrong_status")].reason == "stated_for_implied"
    assert {k[2]: EA.suggest_cause(e) for k, e in got.items()} == {
        "attribution": "attribution",
        "wrong_value": "wrong_scale",
        "missed": "extraction_miss",
        "invented": "extraction_miss",
        "wrong_status": "derivation",
    }
    assert all(e.source == "gold" and e.call_id == "g1" for e in errors)
    assert EA.scale_mismatch(num(2_500_000), GoldValue(kind="numeric", p50=2_500)) and not EA.scale_mismatch(
        num(40), GoldValue(kind="numeric", p50=50)
    )


def test_experiment_records_reads_root_spans():
    rec = gold_case()
    rows = [
        {
            "id": "r",
            "span_parents": None,
            "output": rec.system.model_dump(mode="json"),
            "expected": rec.gold.model_dump(mode="json"),
            "metadata": {},
        },
        {"id": "child", "span_parents": ["r"], "output": {"x": 1}, "expected": None},
    ]
    seen = {}

    class Exp:
        def fetch(self):
            return iter(rows)

    def init(**kw):
        seen.update(kw)
        return Exp()

    out = EA.experiment_records("gold-nightly", init=init)
    assert (
        len(out) == 1
        and out[0].gold.call_id == "g1"
        and seen == {"project": "advisor-extraction", "experiment": "gold-nightly", "open": True}
    )


PATTERNS = {
    "scale": [
        '"he earns about fifty a month"',
        '"fifty a month after tax"',
        '"she brings in about fifty a month"',
        '"roughly fifty a month gross"',
        '"about fifty a month, give or take"',
        '"I take home fifty a month"',
    ],
    "afrikaans": [
        '"twee-honderd duisend in die bank"',
        '"so twee-honderd duisend rand"',
        '"omtrent twee-honderd duisend"',
        '"twee-honderd duisend, ja"',
        '"ons het twee-honderd duisend gespaar"',
        '"twee-honderd duisend in die unit trust"',
    ],
    "advisor": [
        '"what if you retired at sixty-five" that was my suggestion',
        '"shall we say sixty-five for retirement" that was my suggestion',
        '"I would suggest retiring at sixty-five" my suggestion',
        '"maybe plan for sixty-five" that was my suggestion',
        '"let us pencil in sixty-five" that was my suggestion',
        '"typically people retire at sixty-five" my suggestion',
    ],
}


def pattern_errors(n_noise=3):
    out = []
    for k, texts in PATTERNS.items():
        for i, t in enumerate(texts):
            out.append(
                EA.ErrorItem(id=f"{k}{i}", source="production", slot_id=INC, group="income", tier="A", kind="rejected", reason=k, note=t)
            )
    for i, t in enumerate(['"we went to Durban in December"', '"my bond is with Absa"', '"the policy lapsed last year"'][:n_noise]):
        out.append(
            EA.ErrorItem(
                id=f"noise{i}", source="gold", slot_id=RA, group="retirement", tier="A", kind="missed", reason="missed_slot", note=t
            )
        )
    return out


def test_hdbscan_clusters_the_patterns():
    errors = pattern_errors()
    clusters = EA.cluster_errors(errors)
    by_reason = {max(c.reasons, key=c.reasons.get): c for c in clusters}
    assert set(by_reason) == set(PATTERNS) and len(clusters) == 3
    for k, c in by_reason.items():
        assert {f"{k}{i}" for i in range(6)} <= set(c.error_ids)
        assert c.size >= 6 and len(c.exemplars) == 3 and c.slots[INC] >= 6
        assert all(e.cluster == c.cluster_id for e in errors if e.id.startswith(k))
    assert EA.cluster_errors(errors[:4]) == [] and all(e.cluster is None for e in errors[:4])  # fewer than five: no clusters


def label_model(calls):
    def fn(messages, info: AgentInfo) -> ModelResponse:
        calls.append(messages)
        prompt = messages[0].parts[-1].content
        if len(calls) == 1:
            return ModelResponse(
                parts=[TextPart(content=json.dumps({"label": "two\nlines", "cause": "asr", "suggestion": "other", "proposal": "x"}))]
            )
        cause, suggestion = ("wrong_scale", "grammar_rule") if "fifty" in prompt else ("lexicon", "lexicon_entry")
        return ModelResponse(
            parts=[
                TextPart(
                    content=json.dumps(
                        {
                            "label": f"Pattern ({prompt.splitlines()[0]})",
                            "cause": cause,
                            "suggestion": suggestion,
                            "proposal": "Read 'fifty a month' as R50,000 when the income is a salary.",
                        }
                    )
                )
            ]
        )

    return FunctionModel(fn)


def test_clusters_get_a_one_line_label_from_the_small_model():
    errors = pattern_errors()
    clusters = EA.cluster_errors(errors)
    agent = EA.cluster_label_agent(VersionPins())
    calls = []
    with agent.override(model=label_model(calls)):
        labelled = EA.label_clusters(clusters, errors, agent=agent)
    assert all(c.label is not None for c in labelled) and len(calls) == len(clusters) + 1  # the two-line label was retried
    scale = next(c for c in labelled if "scale0" in c.error_ids)
    assert scale.label.cause == "wrong_scale" and scale.label.suggestion == "grammar_rule" and "\n" not in scale.label.label
    prompt = EA.cluster_prompt(scale, {e.id: e for e in errors})
    assert (
        prompt.startswith(f"Cluster of {scale.size} errors.")
        and f"{INC} ×" in prompt
        and "- [income.{p}.grossMonthly · rejected · scale]" in prompt
    )
    # only clusters of min_size or more are labelled; test pins run offline on TestModel
    big = EA.label_clusters(clusters, errors, pins=VersionPins(), min_size=7)
    assert [c.label is not None for c in big] == [c.size >= 7 for c in clusters]


def test_weekly_sample_is_stratified_by_group_and_bucket():
    errors = [
        EA.ErrorItem(id=f"p{i}", source="production", slot_id=INC, group="income", tier="A", kind="rejected", reason="misheard", cluster=0)
        for i in range(30)
    ]
    errors += [
        EA.ErrorItem(id=f"g{i}", source="gold", slot_id=INC, group="income", tier="A", kind="missed", reason="missed_slot")
        for i in range(10)
    ]
    errors += [
        EA.ErrorItem(id=f"r{i}", source="gold", slot_id=RA, group="retirement", tier="A", kind="missed", reason="missed_implied")
        for i in range(5)
    ]
    errors += [
        EA.ErrorItem(
            id=f"c{i}", source="production", slot_id="income.{p}.taxDirective", group="income", tier="C", kind="rejected", reason="other"
        )
        for i in range(5)
    ]
    errors += [
        EA.ErrorItem(id=f"k{i}", source="production", slot_id="risk.q1", group="risk_profile", tier="B", kind="rejected", reason="other")
        for i in range(5)
    ]
    sample = EA.weekly_sample(errors, seed="2026-W42")
    by_group = {}
    for e in sample:
        by_group.setdefault(e.group, []).append(e)
    assert set(by_group) == {"income", "retirement"} and len(by_group["income"]) == 20 and len(by_group["retirement"]) == 5
    assert sum(e.source == "gold" for e in by_group["income"]) == 10  # round-robin across (source, cluster) buckets
    assert all(e.suggested_cause for e in sample) and {e.suggested_cause for e in by_group["retirement"]} == {"derivation"}
    assert [e.id for e in EA.weekly_sample(errors, seed="2026-W42")] == [e.id for e in sample]
    assert [e.id for e in EA.weekly_sample(errors, seed="2026-W43")] != [e.id for e in sample]
    assert len(EA.weekly_sample(errors, per_group=3, groups=["income"], seed="x")) == 3


def test_sheet_round_trip_and_top_causes(tmp_path, capsys):
    sample = EA.weekly_sample(pattern_errors(), groups=["income", "retirement"], seed="w")
    clusters = [
        EA.ErrorCluster(
            cluster_id=0,
            size=6,
            error_ids=[],
            exemplars=[],
            slots={},
            reasons={},
            groups={},
            sources={},
            label=EA.ClusterLabel(label="fifty a month read as R50", cause="wrong_scale", suggestion="grammar_rule", proposal="x"),
        )
    ]
    sample[0].cluster = 0
    path = EA.write_sheet(sample, tmp_path / "sheet.jsonl", clusters=clusters)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["cluster_label"] == "fifty a month read as R50" and all(r["cause"] == "" for r in rows) and rows[0]["suggested_cause"]
    causes = ["asr", "Wrong scale", "asr", "lexicon", "asr", "lexicon", "", "derivation"]
    path.write_text("".join(json.dumps(dict(r, cause=causes[i % len(causes)])) + "\n" for i, r in enumerate(rows)))
    labelled = EA.read_sheet(path)
    assert EA.top_causes(labelled)[0][0] == "asr" and dict(EA.top_causes(labelled, k=8))["wrong_scale"] >= 1
    assert EA.main(["causes", str(path), "--k", "1"]) == 0 and capsys.readouterr().out.startswith("asr\t")
    path.write_text(json.dumps(dict(rows[0], cause="gremlins")) + "\n")
    with pytest.raises(ValueError, match="line 1"):
        EA.read_sheet(path)


def test_weekly_run_writes_the_sheet_and_the_clusters(tmp_path, monkeypatch):
    monkeypatch.setenv("ADVISOR_INTEL_DATA_DIR", str(tmp_path / "data"))
    store = LocalStore(tmp_path / "data" / "datasets")
    texts = [t for ts in PATTERNS.values() for t in ts]
    rows = [
        row(f"{INC}#client", "reject", "not_what_they_meant", quotes=[t.strip('"')], at=NOW.replace(hour=i % 10))
        for i, t in enumerate(texts)
    ]
    assert sync_error_dataset(PRODUCTION + rows, store=store) == len(PRODUCTION) + len(rows)
    calls = []
    agent = EA.cluster_label_agent(VersionPins())
    with agent.override(model=label_model(calls)):
        out = EA.run_weekly(store=store, records=[gold_case()], out_dir=tmp_path / "ea", agent=agent, now=NOW)
    assert out.week == "2026-W42" and out.n_production == len(PRODUCTION) - 1 + len(rows) and out.n_gold == 5
    assert out.clusters and all(c.label is not None for c in out.clusters)
    assert out.sample_by_group["income"] <= 20 and sum(out.sample_by_group.values()) == out.sample_size
    week_dir = tmp_path / "ea" / "2026-W42"
    assert (week_dir / "sample.jsonl").exists() and json.loads((week_dir / "clusters.json").read_text())["week"] == "2026-W42"
    assert "| cluster | errors | label |" in (week_dir / "summary.md").read_text()
    # the CLI on the same data, without labels: production errors only when there is no gold run
    assert EA.main(["weekly", "--out", str(tmp_path / "cli"), "--no-label", "--days", "0"]) == 0
    assert list((tmp_path / "cli").glob("*/sample.jsonl"))
