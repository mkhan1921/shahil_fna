"""Implied facts from derivation rules (§7.6)."""

from __future__ import annotations

from datetime import date

from advisor_intel import stats
from advisor_intel.extract.reducer import apply_state_ops, check_profile, make_ops
from advisor_intel.models import Entity, Evidence, Hypothesis, Point, Profile, Quantity, Triangular
from advisor_intel.models.dists import Categorical
from advisor_intel.models.facts import Provenance
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
