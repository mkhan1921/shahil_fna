"""load_prior and materialise connect the profile to the FnaDocument (§3.6)."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

import numpy as np

from advisor_intel import binding
from advisor_intel.extract.reducer import apply_state_ops, check_profile, make_ops
from advisor_intel.models import fna as F
from advisor_intel.models.crm import CrmPerson, CrmProduct, CrmRecord
from advisor_intel.models.dists import Bernoulli, Categorical, Discrete, LogNormal, Point, Quantity, Triangular
from advisor_intel.models.entities import Entity
from advisor_intel.models.facts import Evidence, Hypothesis, Provenance
from advisor_intel.models.profile import Profile
from advisor_intel.schema.slots import load_catalog

from .conftest import load_fixture
from .forecast_support import add_fact, prior_profile, reject, sample_document
from .test_forecast_consistency import _profile_with_uncertain_facts

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
    return Evidence(
        segment_id=seg,
        char_start=0,
        char_end=5,
        quote="hello",
        speaker_role="client_primary",
        t_start=1,
        t_end=2,
        asr_mean_conf=0.9,
        role="supports",
    )


def _add(prof, slot_id, instance, path, dist, unit="age", period="none", p_correct=0.5):
    fid = f"{slot_id}#{instance}" if instance else slot_id
    payload = {
        "slot_id": slot_id,
        "instance_key": instance,
        "fna_path": path,
        "hypotheses": [Hypothesis(value=Quantity(dist=dist, unit=unit, period=period), weight=1, rationale="")],
        "evidence": [_ev()],
        "provenance": PROV,
    }
    prof = apply_state_ops(prof, make_ops(prof, [("ADD", fid, "s0001", payload, "extractor")])).profile
    f = prof.facts[fid].model_copy(update={"p_correct": p_correct})
    return prof.model_copy(update={"facts": {**prof.facts, fid: f}}), fid


def test_median_and_draw_modes():
    prof = _load(F.FnaDocument())
    prof, _ = _add(prof, "retirement.{p}.retirementAge", "client", "retirement.client.retirementAge", Triangular(low=60, mode=62, high=65))
    prof, _ = _add(
        prof, "client.dateOfBirth".replace("client", "{p}"), "client", "client.dateOfBirth", Point(value="1980-03-04"), unit="date"
    )
    med = binding.materialise(prof, "median")
    assert med.retirement["client"].retirementAge == 62
    assert med.client.dateOfBirth == "1980-03-04"
    rng = np.random.default_rng(0)
    draws = [binding.materialise(prof, "draw", rng).retirement["client"].retirementAge for _ in range(400)]
    assert min(draws) >= 55 and max(draws) <= 75
    # p_correct 0.5: about half the draws come from the population prior (60–70), widening the spread.
    assert np.std(draws) > 1.0


def test_values_are_written_at_the_fields_grain():
    # Whole ages and years, cents, six decimals of a rate: for spreads and for certain values alike (VOI's
    # sensitivity ends depend on whole ages), so a fractional prior age does not survive a round trip.
    doc = F.FnaDocument(dependants=[F.Dependant(dependencyEndAge=25.25, tertiaryStartAge=18)])
    prof = _load(doc)
    assert binding.materialise(prof, "median").dependants[0].dependencyEndAge == 25
    prof, _ = _add(prof, "retirement.{p}.retirementAge", "client", "retirement.client.retirementAge", Point(value=62.4))
    prof, _ = _add(prof, "retirement.{p}.planningAge", "client", "retirement.client.planningAge", Triangular(low=88, mode=92, high=97))
    prof, _ = _add(
        prof, "income.{p}.grossMonthly", "client", "income.client.grossMonthly", Point(value=51234.5678), unit="ZAR", period="monthly"
    )
    med = binding.materialise(prof, "median", base=doc)
    assert med.retirement["client"].retirementAge == 62 and med.retirement["client"].planningAge == 92
    assert med.income["client"].grossMonthly == 51234.57


def test_new_documents_and_rows_are_dated_by_the_call_not_the_clock():
    # A run without a prior file must give the same document whenever it runs (§1.5: the meeting date is call metadata).
    prof = _load(None)
    assert binding.materialise(prof, "median").engagement.meetingDate == ANCHOR.isoformat()
    prof = prof.model_copy(
        update={
            "entities": {
                **prof.entities,
                "goal.trip_1": binding.Entity(entity_id="goal.trip_1", type="goal", canonical_name="Overseas trip"),
            }
        }
    )
    rid = binding.row_id_for(prof, "goal.trip_1")
    prof, _ = _add(prof, "goals[].name", "goal.trip_1", f"goals[{rid}].name", Point(value="Overseas trip"), unit="none")
    med = binding.materialise(prof, "median")
    assert med.goals[0].name == "Overseas trip" and med.goals[0].targetYear == ANCHOR.year + 5
    base = F.FnaDocument(engagement=F.Engagement(meetingDate="2025-01-01"))
    assert binding.materialise(prof, "median", base=base).engagement.meetingDate == "2025-01-01"  # a prior file keeps its own date


def test_text_slot_with_two_readings_keeps_the_likelier():
    prof = _load(F.FnaDocument())
    hyps = [
        Hypothesis(value=Quantity(dist=Point(value="teacher"), unit="none"), weight=0.7, rationale=""),
        Hypothesis(value=Quantity(dist=Point(value="lecturer"), unit="none"), weight=0.3, rationale=""),
    ]
    payload = {
        "slot_id": "{p}.occupation",
        "instance_key": "client",
        "fna_path": "client.occupation",
        "hypotheses": hyps,
        "evidence": [_ev()],
        "provenance": PROV,
    }
    prof = apply_state_ops(prof, make_ops(prof, [("ADD", "{p}.occupation#client", "s0001", payload, "extractor")])).profile
    assert binding.materialise(prof, "median").client.occupation == "teacher"
    assert check_profile(prof) == []


def test_probabilistic_event_and_new_rows():
    prof = _load(F.FnaDocument())
    prof = prof.model_copy(
        update={
            "entities": {
                **prof.entities,
                "person.thandi_1": binding.Entity(
                    entity_id="person.thandi_1", type="person", canonical_name="Thandi", attributes={"relation": "child"}
                ),
            }
        }
    )
    rid = binding.row_id_for(prof, "person.thandi_1")
    prof, _ = _add(prof, "dependants[].tertiary", "person.thandi_1", f"dependants[{rid}].tertiary", Bernoulli(p=0.4), unit="none")
    prof, _ = _add(
        prof, "dependants[].relationship", "person.thandi_1", f"dependants[{rid}].relationship", Point(value="child"), unit="none"
    )
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
    assert (
        f["status"] == "not_discussed"
        and f["change"] == "unchanged_not_discussed"
        and f["value"]["point"] == doc.income["client"].grossMonthly
    )


def test_describe_value():
    q = Quantity(dist=Triangular(low=60, mode=62, high=65), unit="age")
    assert binding.describe_value(q).startswith("60.") or "most likely" in binding.describe_value(q)
    assert binding.describe_value(Quantity(dist=Point(value=42000), unit="ZAR", period="monthly")) == "R42,000 a month"
    assert binding.describe_value(Quantity(dist=Bernoulli(p=0.4), unit="none")) == "40% likely"


# --------------------------------------------------------------------------- draws


def reference_draws(profile: Profile, k: int, seed: int, *, base: F.FnaDocument | None = None) -> list[F.FnaDocument]:
    """``materialise_draws`` as first written: the per-fact loop run K times, every path, slot and sample worked
    out again on each draw. ``materialise_draws`` now prepares that work once per batch; this loop is kept as the
    reference its documents must equal, draw for draw, from the same seed."""
    catalog = load_catalog(profile.schema_version)
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(k):
        doc = base.model_dump(mode="json") if base is not None else binding.empty_document(profile.anchor_date)
        binding._ensure_rows(doc, profile, catalog)
        removed: set[tuple[str, str]] = set()
        for f in sorted(profile.facts.values(), key=lambda x: x.fact_id):
            slot = catalog.slot(f.slot_id)
            if not slot.exported:
                continue
            pp = binding.parse_path(f.fna_path)
            identity = pp.container in binding.IDENTITY_FIELD and pp.field == binding.IDENTITY_FIELD[pp.container]
            if f.change == "removed" and identity and pp.key:
                removed.add((pp.container, pp.key))
                continue
            if f.review.status == "rejected" or f.value is None:
                continue
            dist = f.value.dist
            trusted = f.review.status in ("accepted", "edited") or f.auto_accepted or f.source in ("prior_fna", "reviewer")
            if slot.population_prior is not None and rng.random() >= (1.0 if trusted else f.p_correct):
                dist = slot.population_prior
            value = binding.to_fna_value(slot, dist, "draw", rng)
            if value is None:
                continue
            binding.set_path(doc, binding._row_path(profile, f), value, anchor=profile.anchor_date)
        for container, key in removed:
            rows = binding._container(doc, container)
            rows[:] = [r for r in rows if r.get("id") != key]
        out.append(F.FnaDocument.model_validate(doc))
    return out


def dumps(docs: list[F.FnaDocument]) -> list[dict[str, Any]]:
    """The documents as JSON, without the id and timestamps a document made from nothing takes from the clock."""
    out = []
    for d in docs:
        x = d.model_dump(mode="json")
        for key in ("id", "createdAt", "updatedAt"):
            x.pop(key, None)
        out.append(x)
    return out


def _sha(x: Any) -> str:
    return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _draw_profile() -> tuple[Profile, F.FnaDocument]:
    """A profile that takes every draw path: each distribution kind, trusted and untrusted facts, a bimodal
    mixture, text with two readings, a risk answer, a count, an expense item, a row the call added, a row a
    reviewer removed and a fact a reviewer rejected, over the sample household's prior."""
    doc = sample_document()
    prof = prior_profile(doc)

    def add(slot_id: str, path: str, dist: Any, **kw: Any) -> None:
        nonlocal prof
        prof, _ = add_fact(prof, slot_id, path, dist, **kw)

    add(
        "retirement.{p}.retirementAge",
        "retirement.client.retirementAge",
        Triangular(low=61, mode=64, high=67),
        instance="client",
        unit="age",
        p_correct=0.6,
    )
    add(
        "retirement.{p}.retirementAge",
        "retirement.spouse.retirementAge",
        [(0.6, Point(value=60)), (0.4, Point(value=65))],
        instance="spouse",
        unit="age",
        p_correct=0.7,
    )
    add(
        "income.{p}.grossMonthly",
        "income.spouse.grossMonthly",
        LogNormal(median=58_000, p10=50_000, p90=70_000),
        instance="spouse",
        period="monthly",
        p_correct=0.8,
    )
    add(
        "{p}.dateOfBirth",
        "spouse.dateOfBirth",
        Triangular(low=1984.2, mode=1984.5, high=1984.9),
        instance="spouse",
        unit="date",
        p_correct=0.5,
    )
    add(
        "{p}.employmentType",
        "client.employmentType",
        Categorical(probs={"salaried": 0.7, "self-employed": 0.3}),
        instance="client",
        unit="none",
        p_correct=0.75,
    )
    add("{p}.smoker", "spouse.smoker", Bernoulli(p=0.3), instance="spouse", unit="none", p_correct=0.9)
    add(
        "income.{p}.medicalSchemeMembers",
        "income.client.medicalSchemeMembers",
        Categorical(probs={"3": 0.5, "4": 0.5}),
        instance="client",
        unit="count",
        p_correct=0.6,
    )
    add(
        "riskProfile.answers[horizon]",
        "riskProfile.answers[horizon]",
        Categorical(probs={"5 – 10 years": 0.4, "More than 10 years": 0.6}),
        unit="none",
        p_correct=0.55,
    )
    add(
        "expenses.items[]",
        "expenses.items[groceries]",
        Discrete(support=[7000, 8000, 9000], weights=[0.25, 0.5, 0.25]),
        instance="groceries",
        period="monthly",
        p_correct=0.8,
    )
    add(
        "{p}.occupation",
        "client.occupation",
        [(0.7, Point(value="teacher")), (0.3, Point(value="lecturer"))],
        instance="client",
        unit="none",
    )
    # reviewed or auto-accepted: trusted whatever p_correct says
    add(
        "retirement.{p}.planningAge",
        "retirement.client.planningAge",
        Triangular(low=88, mode=92, high=97),
        instance="client",
        unit="age",
        p_correct=0.3,
        review="accepted",
    )
    add(
        "income.{p}.annualBonus",
        "income.client.annualBonus",
        Triangular(low=40_000, mode=60_000, high=90_000),
        instance="client",
        period="annual",
        p_correct=0.4,
        auto_accepted=True,
    )
    # a third child the call added, with an uncertain tertiary plan
    zola = Entity(entity_id="person.zola_1", type="person", canonical_name="Zola", attributes={"relation": "child"})
    prof = prof.model_copy(update={"entities": {**prof.entities, zola.entity_id: zola}})
    rid = binding.row_id_for(prof, zola.entity_id)
    refs = {"instance": zola.entity_id, "entity_refs": [zola.entity_id]}
    add("dependants[].relationship", f"dependants[{rid}].relationship", Point(value="child"), unit="none", **refs)
    add("dependants[].tertiary", f"dependants[{rid}].tertiary", Bernoulli(p=0.45), unit="none", p_correct=0.7, **refs)
    add(
        "dependants[].tertiaryCostAnnual",
        f"dependants[{rid}].tertiaryCostAnnual",
        LogNormal(median=120_000, p10=90_000, p90=180_000),
        period="annual",
        p_correct=0.5,
        **refs,
    )
    # a reviewer removed the first policy and rejected the first liability's balance
    pol_type = next(f.fact_id for f in prof.facts.values() if f.fna_path == f"policies[{doc.policies[0].id}].type")
    prof = apply_state_ops(prof, make_ops(prof, [("RETRACT", pol_type, None, {"reason": "removed"}, "reviewer")])).profile
    prof = reject(prof, next(f.fact_id for f in prof.facts.values() if f.fna_path == f"liabilities[{doc.liabilities[0].id}].balance"))
    return prof, doc


def test_draws_match_the_reference_loop_and_do_not_depend_on_k():
    prof, doc = _draw_profile()
    for base in (doc, None):
        docs = binding.materialise_draws(prof, 40, seed=3, base=base)
        assert dumps(docs) == dumps(reference_draws(prof, 40, 3, base=base))
        assert dumps(docs[:7]) == dumps(binding.materialise_draws(prof, 7, seed=3, base=base))  # draw k is the same whatever K
    # K single draws sharing a generator are the batch's K documents
    docs = binding.materialise_draws(prof, 40, seed=3, base=doc)
    rng = np.random.default_rng(3)
    assert dumps([binding.materialise(prof, "draw", rng, base=doc) for _ in range(5)]) == dumps(docs[:5])
    # the profile takes the paths it claims to
    assert all(doc.policies[0].id not in {p.id for p in d.policies} for d in docs)
    assert all(len(d.dependants) == len(doc.dependants) + 1 and d.dependants[-1].name == "" for d in docs)  # the added child
    assert {d.dependants[-1].tertiary for d in docs} == {True, False}
    assert {d.retirement["spouse"].retirementAge for d in docs} > {60}
    assert {3, 4} <= {d.riskProfile.answers["horizon"] for d in docs} <= {0, 1, 2, 3, 4}  # the two readings, or the prior
    assert all(d.client.occupation == "teacher" and d.liabilities[0].balance == doc.liabilities[0].balance for d in docs)


def test_draws_are_pinned_to_the_first_implementation():
    """The documents the first implementation wrote for the uncertain-facts profile at seed 7: the first draw in
    full and a digest of each of the 50. A deliberate change to the sample household, the catalog's priors or the
    draw rules means rewriting the fixture from ``materialise_draws(prof, 50, seed=7, base=doc)``."""
    pin = load_fixture("materialise_draws_pinned.json")
    prof, doc = _profile_with_uncertain_facts()
    docs = [d.model_dump(mode="json") for d in binding.materialise_draws(prof, pin["k"], seed=pin["seed"], base=doc)]
    assert docs[0] == pin["first_draw"]
    assert [_sha(d) for d in docs] == pin["sha256"]


def test_a_draw_without_a_generator_is_refused():
    prof, doc = _draw_profile()
    try:
        binding.materialise(prof, "draw", None, base=doc)
    except ValueError as e:
        assert "rng" in str(e)
    else:
        raise AssertionError("draw mode needs an rng")
