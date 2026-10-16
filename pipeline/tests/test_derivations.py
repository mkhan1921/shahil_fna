"""Implied facts from derivation rules (§7.6)."""

from __future__ import annotations

from datetime import date

from advisor_intel import stats
from advisor_intel.extract.reducer import apply_state_ops, check_profile, make_ops
from advisor_intel.models import Entity, Evidence, Hypothesis, Point, Profile, Quantity, Triangular
from advisor_intel.models.dists import Categorical
from advisor_intel.models.facts import Provenance
from advisor_intel.schema import load_catalog
from advisor_intel.schema.derivations import derive_all

PROV = Provenance(run_id="wf-d")


def ev(seg, quote):
    return Evidence(segment_id=seg, char_start=0, char_end=len(quote), quote=quote, speaker_role="client_primary", t_start=1, t_end=2, asr_mean_conf=0.9, role="supports")


def add(profile, slot_id, instance, path, dist, unit, said="said", seg="s0001"):
    fid = f"{slot_id}#{instance}" if instance else slot_id
    payload = {"slot_id": slot_id, "instance_key": instance, "fna_path": path, "hypotheses": [Hypothesis(value=Quantity(dist=dist, unit=unit), weight=1, rationale="")],
               "evidence": [ev(seg, said)], "said": said, "provenance": PROV}
    return apply_state_ops(profile, make_ops(profile, [("ADD", fid, seg, payload, "extractor")])).profile


def base_profile():
    return Profile(call_id="c", run_id="wf-d", schema_version="profile_v1", anchor_date=date(2026, 10, 15),
                   entities={"person.client_primary": Entity(entity_id="person.client_primary", type="person", canonical_name="Sipho")})


def test_horizon_and_income_stability():
    p = base_profile()
    p = add(p, "{p}.dateOfBirth", "client", "client.dateOfBirth", Point(value=1982.2), "date")
    p = add(p, "retirement.{p}.retirementAge", "client", "retirement.client.retirementAge", Triangular(low=60, mode=60, high=62), "age")
    p = add(p, "{p}.employmentType", "client", "client.employmentType", Categorical(probs={"salaried": 0.8, "commission": 0.2}), "none")
    out = derive_all(p)
    hz = out.facts["riskProfile.answers[horizon]"]
    assert hz.status == "implied" and hz.derivation == "horizon_from_retirement" and hz.source == "derived"
    assert stats.mode_label(hz.value.dist) == "More than 10 years"
    stab = out.facts["riskProfile.answers[income-stability]"]
    probs = stats.categorical_probs(stab.value.dist)
    assert abs(probs["Secure"] - 0.8) < 1e-9 and abs(probs["Somewhat insecure (commission, contract)"] - 0.2) < 1e-9
    assert stab.evidence and check_profile(out) == []


def test_derivations_never_override_what_was_said():
    p = base_profile()
    p = add(p, "{p}.employmentType", "client", "client.employmentType", Point(value="salaried"), "none")
    p = add(p, "riskProfile.answers[income-stability]", None, "riskProfile.answers[income-stability]", Point(value="Very secure, with other income sources"), "none")
    out = derive_all(p)
    assert out.facts["riskProfile.answers[income-stability]"].derivation is None


def test_tertiary_after_matric_and_linked_asset():
    p = base_profile()
    p = p.model_copy(update={"entities": {**p.entities,
                                          "person.thandi_1": Entity(entity_id="person.thandi_1", type="person", canonical_name="Thandi"),
                                          "liability.bond_1": Entity(entity_id="liability.bond_1", type="liability", canonical_name="Bond"),
                                          "property.house_1": Entity(entity_id="property.house_1", type="property", canonical_name="House")}})
    p = add(p, "dependants[].tertiaryStartAge", "person.thandi_1", "dependants[d1].tertiaryStartAge", Point(value=18), "age", said="straight after matric")
    p = add(p, "liabilities[].type", "liability.bond_1", "liabilities[l1].type", Point(value="home-loan"), "none", said="the bond")
    p = add(p, "assets[].type", "property.house_1", "assets[a1].type", Point(value="primary-residence"), "none", said="our house")
    out = derive_all(p)
    ts = out.facts["dependants[].tertiaryStartAge#person.thandi_1"]
    assert isinstance(ts.value.dist, Triangular) and ts.status == "implied"
    link = out.facts["liabilities[].linkedAssetId#liability.bond_1"]
    assert link.fna_path == "liabilities[l1].linkedAssetId" and link.derivation == "linked_asset_from_registry"
    assert check_profile(out) == []


def test_gross_from_net_uses_the_tax_module():
    from advisor_intel.fna_engine import tables, tax

    p = base_profile()
    p = add(p, "{p}.dateOfBirth", "client", "client.dateOfBirth", Point(value=1982.2), "date")
    fid = "income.{p}.grossMonthly#client"
    payload = {"slot_id": "income.{p}.grossMonthly", "instance_key": "client", "fna_path": "income.client.grossMonthly",
               "hypotheses": [Hypothesis(value=Quantity(dist=Point(value=42000), unit="ZAR", period="monthly", basis="net"), weight=1, rationale="")],
               "evidence": [ev("s0007", "I take home about forty-two thousand a month")], "provenance": PROV}
    p = apply_state_ops(p, make_ops(p, [("ADD", fid, "s0007", payload, "extractor")])).profile
    out = derive_all(p)
    f = out.facts[fid]
    assert f.status == "implied" and f.value.basis == "gross"
    med = stats.median(f.value.dist)
    expected = tax.gross_up_monthly(42000.0, age=44.6, table=tables.get_table())
    assert abs(med - expected) / expected < 0.02 and med > 42000
    assert stats.quantile(f.value.dist, 0.9) > stats.quantile(f.value.dist, 0.1)  # widened for unknown deductions
    assert check_profile(out) == []


def test_dob_from_prior_id_number(sample_fna_json):
    from advisor_intel.models import fna as F

    doc = F.FnaDocument.model_validate(sample_fna_json)
    doc.client.dateOfBirth = ""
    p = base_profile()
    out = derive_all(p, base=doc)
    f = out.facts["{p}.dateOfBirth#client"]
    assert f.value.dist == Point(value="1986-04-12") and f.status == "not_discussed" and f.derivation == "dob_from_id_number"


def test_a_spouse_with_facts_from_the_call_is_a_partner_in_the_plan():
    from advisor_intel.models import Entity
    from advisor_intel.schema.derivations import derive_all

    cat = load_catalog()
    p = Profile(call_id="c", run_id="r", schema_version=cat.version, anchor_date=date(2026, 10, 15),
                entities={"person.spouse": Entity(entity_id="person.spouse", type="person", canonical_name="Lerato")})
    assert "household.includeSpouse" not in derive_all(p).facts  # no spouse facts: nothing to infer
    seg = "s0010"
    ev = Evidence(segment_id=seg, char_start=0, char_end=6, quote="I earn", speaker_role="client_secondary", t_start=0, t_end=1, asr_mean_conf=0.95, role="supports")
    spec = ("ADD", "income.{p}.grossMonthly#spouse", seg, {"slot_id": "income.{p}.grossMonthly", "instance_key": "spouse", "fna_path": "income.spouse.grossMonthly",
            "hypotheses": [Hypothesis(value=Quantity(dist=Point(value=35000), unit="ZAR", period="monthly"), weight=1, rationale="")],
            "evidence": [ev], "provenance": Provenance(run_id="r")}, "extractor")
    p = apply_state_ops(p, make_ops(p, [spec])).profile
    d = derive_all(p)
    f = d.facts["household.includeSpouse"]
    assert f.status == "implied" and f.derivation == "partner_on_call" and f.value.dist.p > 0.9 and f.evidence[0].segment_id == seg
    ms = d.facts["household.maritalStatus"]
    assert ms.status == "implied" and stats.mode_label(ms.value.dist) == "married" and set(ms.value.dist.probs) == {"married", "life-partner"}
