"""Gold calls: annotation import, adjudication and inter-annotator agreement (§15.1–15.2)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from sklearn.metrics import cohen_kappa_score

from advisor_intel import stats
from advisor_intel.evals import gold as G
from advisor_intel.models.fna import FnaDocument
from advisor_intel.schema.slots import load_catalog

CAT = load_catalog()
ANCHOR = date(2026, 10, 15)


def ann(annotator, doc, facts, entities=(), role="annotator"):
    return G.AnnotationFile(
        call_id="c1",
        client_id="crm-1",
        annotator_id=annotator,
        role=role,
        anchor_date=ANCHOR,
        document=doc,
        facts=[G.AnnotatedFact(**f) for f in facts],
        entities=[G.AnnotatedEntity(**e) for e in entities],
    )


def test_gold_value_normalises_dates_and_fills_quantiles():
    v = G.GoldValue(kind="numeric", p50="1986-04-12")
    assert v.p50 == pytest.approx(stats.iso_to_year("1986-04-12")) and v.p10 == v.p50 == v.p90
    w = G.GoldValue(kind="numeric", p10=70, p50=60, p90=65)  # p10 above p50 is clamped
    assert (w.p10, w.p50, w.p90) == (60, 60, 65)
    with pytest.raises(ValueError):
        G.GoldValue(kind="categorical")
    assert G.GoldValue(kind="boolean", label="true").truth is True
    assert G.gold_value(CAT.slot("riskProfile.answers[horizon]"), value=4).label == "More than 10 years"
    assert G.gold_value(CAT.slot("event[].occurs"), p10=0.25, p50=0.4, p90=0.55).vague
    assert G.gold_value(CAT.slot("{p}.smoker"), value="yes").label == "true"


def test_paths_map_to_fact_keys():
    rows = {"row-1": "person.thandi"}
    assert G.key_for_path("retirement.spouse.retirementAge", CAT) == ("retirement.{p}.retirementAge", "spouse")
    assert G.key_for_path("client.dateOfBirth", CAT) == ("{p}.dateOfBirth", "client")
    assert G.key_for_path("household.maritalRegime", CAT) == ("household.maritalRegime", None)
    assert G.key_for_path("dependants[row-1].tertiaryStartAge", CAT, rows) == ("dependants[].tertiaryStartAge", "person.thandi")
    assert G.key_for_path("dependants[row-2].tertiary", CAT, rows) == ("dependants[].tertiary", "row:row-2")
    assert G.key_for_path("expenses.items[groceries]", CAT) == ("expenses.items[]", "groceries")
    assert G.key_for_path("riskProfile.answers[horizon]", CAT) == ("riskProfile.answers[horizon]", None)


def test_adjudication_averages_quantiles_and_settles_disagreements(sample_fna_json):
    doc = FnaDocument.model_validate(sample_fna_json)
    common = [
        {
            "fna_path": "retirement.client.retirementAge",
            "status": "stated",
            "p10": 60,
            "p50": 62,
            "p90": 65,
            "spans": [{"segment_id": "s0042", "char_start": 10, "char_end": 31, "quote": "somewhere around sixty"}],
        },
        {"fna_path": "income.client.grossMonthly", "status": "stated", "p50": 100_000},
    ]
    a = ann(
        "ann-1",
        doc,
        common
        + [
            {"fna_path": "client.smoker", "status": "stated", "value": False},
            {
                "fna_path": "dependants[new-a].tertiary",
                "status": "stated",
                "value": True,
                "spans": [{"segment_id": "s0050", "char_start": 0, "char_end": 9}],
            },
            {"fna_path": "estate.client.hasWill", "status": "stated", "value": "yes"},
        ],
        entities=[{"row_id": "new-a", "fna_list": "dependants", "names": ["Thandi"], "relation": "child"}],
    )
    b = ann(
        "ann-2",
        doc,
        [
            {**common[0], "p10": 62, "p50": 64, "p90": 67, "attribution": "client"},
            common[1],
            {"fna_path": "client.smoker", "status": "not_discussed"},
            {
                "fna_path": "dependants[new-b].tertiary",
                "status": "stated",
                "value": True,
                "spans": [{"segment_id": "s0051", "char_start": 0, "char_end": 4}],
            },
            {"fna_path": "estate.client.hasWill", "status": "stated", "value": "unsure"},
        ],
        entities=[{"row_id": "new-b", "fna_list": "dependants", "names": ["Thandi Mokoena"], "relation": "child"}],
    )
    g = G.adjudicate(a, b, catalog=CAT)
    idx = g.fact_index()
    ra = idx[("retirement.{p}.retirementAge", "client")]
    assert (ra.value.p10, ra.value.p50, ra.value.p90) == (61, 63, 66) and not ra.disputed
    assert g.document.retirement["client"].retirementAge == 63  # the averaged median goes into the gold document
    thandi = next(e for e in g.entities if e.names == ["Thandi"])
    tert = idx[("dependants[].tertiary", thandi.entity_id)]
    assert tert.value.label == "true" and len(tert.spans) == 2  # the two annotators' rows were matched by name; spans unioned
    assert idx[("{p}.smoker", "client")].disputed  # stated vs not discussed, no adjudicator
    assert idx[("estate.{p}.hasWill", "client")].disputed
    assert g.annotators == ["ann-1", "ann-2"] and g.adjudicator is None

    senior = ann(
        "senior",
        doc,
        [
            {"fna_path": "estate.client.hasWill", "status": "stated", "value": "unsure"},
            {"fna_path": "client.smoker", "status": "stated", "value": False},
        ],
        role="adjudicator",
    )
    g2 = G.adjudicate(a, b, adjudicator=senior, catalog=CAT)
    idx2 = g2.fact_index()
    assert idx2[("estate.{p}.hasWill", "client")].value.label == "unsure" and not idx2[("estate.{p}.hasWill", "client")].disputed
    assert idx2[("{p}.smoker", "client")].status == "stated" and g2.adjudicator == "senior"


def test_cohens_kappa_matches_sklearn():
    rng = np.random.default_rng(1)
    a = rng.choice(G.STATUSES, size=200, p=[0.4, 0.1, 0.1, 0.35, 0.05])
    b = np.where(rng.random(200) < 0.7, a, rng.choice(G.STATUSES, size=200))
    pairs = list(zip(a.tolist(), b.tolist()))
    assert G.cohens_kappa(pairs) == pytest.approx(cohen_kappa_score(a, b, labels=list(G.STATUSES)))
    assert G.cohens_kappa([("stated", "stated")] * 5) == 1.0
    assert np.isnan(G.cohens_kappa([]))


def test_interval_overlap():
    a = G.GoldValue(kind="numeric", p10=60, p50=62, p90=65)
    b = G.GoldValue(kind="numeric", p10=62, p50=64, p90=67)
    assert G.interval_overlap(a, b) == pytest.approx(3 / 7)
    assert G.interval_overlap(G.GoldValue(kind="numeric", p50=100), G.GoldValue(kind="numeric", p50=101)) == 1.0
    assert G.interval_overlap(G.GoldValue(kind="numeric", p50=100), G.GoldValue(kind="numeric", p50=110)) == 0.0
    assert G.interval_overlap(a, G.GoldValue(kind="boolean", label="true")) is None


def test_agreement_report_and_rewrite_list(sample_fna_json):
    doc = FnaDocument.model_validate(sample_fna_json)
    pairs = []
    for i in range(12):
        same = i % 2 == 0
        fa = [
            {"fna_path": "retirement.client.retirementAge", "status": "stated", "p10": 60, "p50": 62, "p90": 65},
            {"fna_path": "income.client.grossMonthly", "status": "stated", "p50": 90_000},
        ]
        fb = [
            {"fna_path": "retirement.client.retirementAge", "status": "stated" if same else "implied", "p10": 61, "p50": 63, "p90": 66},
            {"fna_path": "income.client.grossMonthly", "status": "stated", "p50": 90_000},
        ]
        if i < 6:
            fa.append({"fna_path": "client.smoker", "status": "stated", "value": False})
            fb.append({"fna_path": "client.smoker", "status": "stated", "value": False})
        pairs.append((ann("a", doc, fa), ann("b", doc, fb)))
    rep = G.agreement(pairs, catalog=CAT, min_items=10)
    assert rep.by_slot["income.{p}.grossMonthly"].kappa == 1.0
    ra = rep.by_slot["retirement.{p}.retirementAge"]
    assert ra.n == 12 and ra.kappa < 0.6
    assert [r.slot_id for r in rep.rewrite] == ["retirement.{p}.retirementAge"]
    assert rep.rewrite[0].question == CAT.slot("retirement.{p}.retirementAge").question
    assert rep.by_group["retirement"].value_overlap == pytest.approx(4 / 6)  # [60,65] vs [61,66]
    assert rep.by_group["people"].kappa == 1.0


def test_default_note_facts():
    g = G.GoldCall(
        call_id="c",
        anchor_date=ANCHOR,
        facts=[
            G.GoldFact(
                slot_id="retirement.{p}.retirementAge", instance_key="client", status="stated", value=G.GoldValue(kind="numeric", p50=65)
            ),
            G.GoldFact(slot_id="{p}.smoker", instance_key="client", status="stated", value=G.GoldValue(kind="boolean", label="false")),
            G.GoldFact(slot_id="household.maritalStatus", status="not_discussed"),
        ],
    )
    assert [f.slot_id for f in G.default_note_facts(g, CAT)] == ["retirement.{p}.retirementAge"]
    g2 = g.model_copy(update={"note_fact_ids": ["{p}.smoker#client"]})
    assert [f.slot_id for f in G.default_note_facts(g2, CAT)] == ["{p}.smoker"]


APP_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"  # the app's own exports (lib/review)
needs_app_fixture = pytest.mark.skipif(
    not (APP_FIXTURES / "gold_annotation.json").exists(), reason="the app's annotation fixture is absent"
)


def app_annotation():
    raw = json.loads((APP_FIXTURES / "gold_annotation.json").read_text())
    prior = FnaDocument.model_validate(json.loads((APP_FIXTURES / "review_package.json").read_text())["bundle"]["prior_document"])
    return raw, prior


@needs_app_fixture
def test_the_apps_annotation_export_reads_into_the_adjudication_format():
    raw, prior = app_annotation()
    a = G.load_annotation(json.dumps(raw), prior_document=prior)
    assert (a.call_id, a.annotator_id, a.transcript, a.seconds_spent) == ("call-2026-10-15-mokoena", "annotator-07", "asr", 4500.0)
    facts = {(f.slot_id, f.instance_key): f for f in a.facts}
    ra = facts[("retirement.{p}.retirementAge", "client")]
    assert (ra.p10, ra.p50, ra.p90) == (60, 62, 65) and ra.spans and ra.spans[0].quote
    cost = facts[("dependants[].tertiaryCostAnnual", "person.kagiso_mokoena_2")]
    assert (cost.p10, cost.p50, cost.p90) == (150_000, 150_000, 450_000)  # options 60% R150k / 40% R450k as quantiles
    assert facts[("event[].occurs", "event.annotator_kagiso_studies_overseas")].p50 == pytest.approx(0.4)  # P(yes)
    assert facts[("policies[].cover", "policy.annotator_policies_1")].status == "explicitly_unknown"
    # the annotator's document is the prior file with the annotated values written in
    assert a.document.income["client"].grossMonthly == 118_000 and a.document.retirement["client"].retirementAge == 62
    kagiso = next(d for d in a.document.dependants if d.id.startswith("b4db1d21"))
    assert kagiso.tertiaryCostAnnual == 150_000 and len(a.document.dependants) == len(prior.dependants)
    ents = {e.entity_id: e for e in a.entities}
    assert ents["person.kagiso_mokoena_2"].names == ["Kagiso Mokoena"]
    assert ents["policy.annotator_policies_1"].names[0] == "Group disability through work"
    # two annotators' exports adjudicate into a gold call: quantiles averaged, prior rows matched by entity id
    raw_b = json.loads(json.dumps(raw))
    for f in raw_b["facts"]:
        if f["slot_id"] == "retirement.{p}.retirementAge":
            f["value"]["value"].update(p10=58, p50=60, p90=63)
    b = G.load_annotation(dict(raw_b, annotator_id="annotator-08"), prior_document=prior)
    gold = G.adjudicate(a, b)
    by_key = {(f.slot_id, f.instance_key): f for f in gold.facts}
    v = by_key[("retirement.{p}.retirementAge", "client")].value
    assert (v.p10, v.p50, v.p90) == (59, 61, 64) and not by_key[("retirement.{p}.retirementAge", "client")].disputed
    assert gold.annotators == ["annotator-07", "annotator-08"] and gold.document.retirement["client"].retirementAge == 61
    assert {e.entity_id: e.type for e in gold.entities}["event.annotator_kagiso_studies_overseas"] == "goal"


@needs_app_fixture
def test_the_gold_sync_cli_takes_the_apps_files(tmp_path, monkeypatch):
    from advisor_intel.evals import datasets as D

    monkeypatch.delenv("BRAINTRUST_API_KEY", raising=False)
    monkeypatch.setenv("ADVISOR_INTEL_DATA_DIR", str(tmp_path))
    raw, prior = app_annotation()
    (tmp_path / "a.json").write_text(json.dumps(raw))
    (tmp_path / "b.json").write_text(json.dumps(dict(raw, annotator_id="annotator-08")))
    (tmp_path / "prior.json").write_text(prior.model_dump_json())
    assert D.main(["sync-gold", f"{tmp_path / 'a.json'},{tmp_path / 'b.json'}", "--prior", str(tmp_path / "prior.json"), "--heldout"]) == 0
    rows = D.LocalStore().fetch("gold-calls")
    assert sorted(r.id for r in rows) == ["call-2026-10-15-mokoena:asr", "call-2026-10-15-mokoena:human"]
    assert all(r.metadata["split"] == "heldout" for r in rows)
    gold = G.GoldCall.model_validate(rows[0].expected)
    assert gold.document.income["client"].grossMonthly == 118_000 and len(gold.facts) == 6
