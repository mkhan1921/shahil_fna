"""load_prior and materialise connect the profile to the FnaDocument (§3.6)."""

from __future__ import annotations

from datetime import date

import numpy as np

from advisor_intel import binding
from advisor_intel.extract.reducer import apply_state_ops, check_profile, make_ops
from advisor_intel.models import fna as F
from advisor_intel.models.crm import CrmPerson, CrmProduct, CrmRecord
from advisor_intel.models.dists import Bernoulli, Point, Quantity, Triangular
from advisor_intel.models.facts import Evidence, Hypothesis, Provenance

ANCHOR = date(2026, 10, 15)


def _load(doc, crm=None):
    return binding.load_prior(doc, crm, call_id="c1", run_id="wf-1", anchor_date=ANCHOR)


def _strip(d):
    d = dict(d)
    d.pop("updatedAt", None)
    return d


def test_round_trip_with_base_is_exact(sample_fna_json):
    doc = F.FnaDocument.model_validate(sample_fna_json)
    prof = _load(doc)
    assert check_profile(prof) == []
    assert all(f.source == "prior_fna" and f.status == "not_discussed" for f in prof.facts.values())
    out = binding.materialise(prof, "median", base=doc)
    assert _strip(out.model_dump(mode="json")) == _strip(doc.model_dump(mode="json"))


def test_round_trip_without_base_rebuilds_slot_fields(sample_fna_json):
    doc = F.FnaDocument.model_validate(sample_fna_json)
    prof = _load(doc)
    out = binding.materialise(prof, "median").model_dump(mode="json")
    src = doc.model_dump(mode="json")
    for f in prof.facts.values():
        assert binding.get_path(out, f.fna_path) == binding.get_path(src, f.fna_path), f.fna_path
    assert [r["id"] for r in out["dependants"]] == [r["id"] for r in src["dependants"]]


def test_registry_seeded_from_rows_and_crm(sample_fna_json):
    doc = F.FnaDocument.model_validate(sample_fna_json)
    first_policy = doc.policies[0]
    crm = CrmRecord(
        client_id="crm-1",
        household=[CrmPerson(crm_id="p1", role="client_primary", name=doc.client.firstName)],
        products=[
            CrmProduct(crm_id="x1", kind="policy", provider=first_policy.insurer, product=first_policy.type, fna_type=first_policy.type),
            CrmProduct(crm_id="x2", kind="investment", provider="Satrix", product="TFSA ETF", fna_type="tfsa", last_value=120000),
        ],
    )
    prof = _load(doc, crm)
    ents = prof.entities
    assert ents["person.client_primary"].crm_match == [("p1", 1.0)]
    assert {"person.client_primary", "person.advisor"} <= set(ents)
    rows = [e for e in ents.values() if e.fna_list == "policies"]
    assert len(rows) == len(doc.policies)
    assert any(e.crm_match and e.crm_match[0][0] == "x1" for e in rows)
    assert any(e.entity_id.endswith("_crm") and e.crm_match[0][0] == "x2" for e in ents.values())


def test_paths():
    d = F.FnaDocument().model_dump(mode="json")
    binding.set_path(d, "expenses.items[groceries]", 7000)
    binding.set_path(d, "dependants[abc].tertiaryStartAge", 18)
    binding.set_path(d, "riskProfile.answers[horizon]", 4)
    binding.set_path(d, "income.client.grossMonthly", 45000)
    assert binding.get_path(d, "expenses.items[groceries]") == 7000
    assert d["dependants"][0]["id"] == "abc" and d["dependants"][0]["tertiaryCostAnnual"] == 150000
    assert binding.get_path(d, "riskProfile.answers[horizon]") == 4
    assert binding.parse_path("dependants[abc].tertiaryStartAge") == binding.PathParts("dependants", "abc", "tertiaryStartAge")
    assert F.FnaDocument.model_validate(d).income["client"].grossMonthly == 45000


PROV = Provenance(run_id="wf-1")


def _ev(seg="s0001"):
    return Evidence(segment_id=seg, char_start=0, char_end=5, quote="hello", speaker_role="client_primary", t_start=1, t_end=2, asr_mean_conf=0.9, role="supports")


def _add(prof, slot_id, instance, path, dist, unit="age", period="none", p_correct=0.5):
    fid = f"{slot_id}#{instance}" if instance else slot_id
    payload = {"slot_id": slot_id, "instance_key": instance, "fna_path": path, "hypotheses": [Hypothesis(value=Quantity(dist=dist, unit=unit, period=period), weight=1, rationale="")],
               "evidence": [_ev()], "provenance": PROV}
    prof = apply_state_ops(prof, make_ops(prof, [("ADD", fid, "s0001", payload, "extractor")])).profile
    f = prof.facts[fid].model_copy(update={"p_correct": p_correct})
    return prof.model_copy(update={"facts": {**prof.facts, fid: f}}), fid


def test_median_and_draw_modes():
    prof = _load(F.FnaDocument())
    prof, _ = _add(prof, "retirement.{p}.retirementAge", "client", "retirement.client.retirementAge", Triangular(low=60, mode=62, high=65))
    prof, _ = _add(prof, "client.dateOfBirth".replace("client", "{p}"), "client", "client.dateOfBirth", Point(value="1980-03-04"), unit="date")
    med = binding.materialise(prof, "median")
    assert med.retirement["client"].retirementAge == 62
    assert med.client.dateOfBirth == "1980-03-04"
    rng = np.random.default_rng(0)
    draws = [binding.materialise(prof, "draw", rng).retirement["client"].retirementAge for _ in range(400)]
    assert 55 <= min(draws) and max(draws) <= 75
    # p_correct 0.5: about half the draws come from the population prior (60–70), widening the spread.
    assert np.std(draws) > 1.0


def test_text_slot_with_two_readings_keeps_the_likelier():
    prof = _load(F.FnaDocument())
    hyps = [Hypothesis(value=Quantity(dist=Point(value="teacher"), unit="none"), weight=0.7, rationale=""),
            Hypothesis(value=Quantity(dist=Point(value="lecturer"), unit="none"), weight=0.3, rationale="")]
    payload = {"slot_id": "{p}.occupation", "instance_key": "client", "fna_path": "client.occupation", "hypotheses": hyps, "evidence": [_ev()], "provenance": PROV}
    prof = apply_state_ops(prof, make_ops(prof, [("ADD", "{p}.occupation#client", "s0001", payload, "extractor")])).profile
    assert binding.materialise(prof, "median").client.occupation == "teacher"
    assert check_profile(prof) == []


def test_probabilistic_event_and_new_rows():
    prof = _load(F.FnaDocument())
    prof = prof.model_copy(update={"entities": {**prof.entities, "person.thandi_1": binding.Entity(entity_id="person.thandi_1", type="person", canonical_name="Thandi", attributes={"relation": "child"})}})
    rid = binding.row_id_for(prof, "person.thandi_1")
    prof, _ = _add(prof, "dependants[].tertiary", "person.thandi_1", f"dependants[{rid}].tertiary", Bernoulli(p=0.4), unit="none")
    prof, _ = _add(prof, "dependants[].relationship", "person.thandi_1", f"dependants[{rid}].relationship", Point(value="child"), unit="none")
    med = binding.materialise(prof, "median")
    assert len(med.dependants) == 1 and med.dependants[0].id == rid
    assert med.dependants[0].tertiary is False  # p = 0.4 < 0.5
    assert binding.row_id_for(prof, "person.thandi_1") == rid  # stable across calls


def test_risk_answers_and_counts(sample_fna_json):
    doc = F.FnaDocument.model_validate(sample_fna_json)
    doc.riskProfile.answers = {"horizon": 4, "objective": 2}
    doc.income["client"].medicalSchemeMembers = 3
    prof = _load(doc)
    hz = prof.facts["riskProfile.answers[horizon]"]
    assert hz.value.dist == Point(value="More than 10 years")
    assert prof.facts["income.{p}.medicalSchemeMembers#client"].value.dist == Point(value="3")
    out = binding.materialise(prof, "median")
    assert out.riskProfile.answers == {"horizon": 4, "objective": 2}
    assert out.income["client"].medicalSchemeMembers == 3


def test_reviewer_removal_drops_the_row(sample_fna_json):
    doc = F.FnaDocument.model_validate(sample_fna_json)
    prof = _load(doc)
    pol = doc.policies[0]
    fid = next(f.fact_id for f in prof.facts.values() if f.fna_path == f"policies[{pol.id}].type")
    prof = apply_state_ops(prof, make_ops(prof, [("RETRACT", fid, None, {"reason": "removed"}, "reviewer")])).profile
    out = binding.materialise(prof, "median", base=doc)
    assert pol.id not in {p.id for p in out.policies}


def test_sidecar_and_export(sample_fna_json):
    doc = F.FnaDocument.model_validate(sample_fna_json)
    prof = _load(doc)
    exp, side = binding.export_bundle(prof, base=doc)
    assert exp.format == "shahil-fna" and exp.document.schemaVersion == 2
    assert side["format"] == "advisor-intel-sidecar"
    f = side["fields"]["income.client.grossMonthly"]
    assert f["status"] == "not_discussed" and f["change"] == "unchanged_not_discussed" and f["value"]["point"] == doc.income["client"].grossMonthly


def test_describe_value():
    q = Quantity(dist=Triangular(low=60, mode=62, high=65), unit="age")
    assert binding.describe_value(q).startswith("60.") or "most likely" in binding.describe_value(q)
    assert binding.describe_value(Quantity(dist=Point(value=42000), unit="ZAR", period="monthly")) == "R42,000 a month"
    assert binding.describe_value(Quantity(dist=Bernoulli(p=0.4), unit="none")) == "40% likely"
