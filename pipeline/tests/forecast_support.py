"""Shared helpers for the forecast tests: a deterministic stub tax model and profile builders.

The stub is not SARS tax: the pipeline's one tax implementation is the ported FNA engine (§12.7). It is a
simple progressive schedule with the same interface, so engine and mapper tests run without the engine.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from advisor_intel import binding
from advisor_intel.extract.reducer import apply_state_ops, make_ops
from advisor_intel.forecast.tax import PersonTax, gross_up_by_bisection
from advisor_intel.models import fna as F
from advisor_intel.models.dists import Dist, Point, Quantity
from advisor_intel.models.facts import Evidence, Hypothesis, Provenance, ReviewState
from advisor_intel.models.profile import Profile

FIXTURES = Path(__file__).parent / "fixtures"
ANCHOR = date(2026, 10, 15)
PROV = Provenance(run_id="wf-forecast")


class StubTaxModel:
    """A deterministic two-band tax: 20% on taxable income above R100,000 and 35% above R700,000; flat UIF."""

    version = "stub-tax-1"

    def __init__(self, threshold: float = 100_000.0, rate: float = 0.20, top_from: float = 700_000.0, top_rate: float = 0.35) -> None:
        self.threshold, self.rate, self.top_from, self.top_rate = threshold, rate, top_from, top_rate

    def income_tax(self, taxable_annual, age):
        x = np.asarray(taxable_annual, dtype=float)
        return self.rate * np.clip(x - self.threshold, 0, self.top_from - self.threshold) + self.top_rate * np.maximum(x - self.top_from, 0)

    def person_tax(
        self,
        *,
        age,
        gross_monthly,
        annual_bonus=0.0,
        other_taxable_monthly=0.0,
        retirement_contributions_annual=0.0,
        employer_retirement_annual=0.0,
        medical_scheme_members=0.0,
        uif_applies=True,
    ):
        gross = np.asarray(gross_monthly, dtype=float)
        cash = gross * 12 + np.asarray(annual_bonus, dtype=float) + np.asarray(other_taxable_monthly, dtype=float) * 12
        remuneration = cash + np.asarray(employer_retirement_annual, dtype=float)
        deduction = np.minimum(np.asarray(retirement_contributions_annual, dtype=float), 0.275 * remuneration)
        taxable = np.maximum(remuneration - deduction, 0.0)
        tax = self.income_tax(taxable, age)
        uif = np.where(np.asarray(uif_applies, dtype=bool), np.minimum(gross, 17_712.0) * 0.01, 0.0)
        shape = np.broadcast_shapes(np.shape(tax), np.shape(uif))
        return PersonTax(np.broadcast_to(tax, shape).astype(float), np.broadcast_to(uif, shape).astype(float))

    def gross_up_monthly(
        self,
        net_monthly,
        *,
        age,
        retirement_monthly=0.0,
        medical_members=0.0,
        medical_payroll_monthly=0.0,
        other_payroll_monthly=0.0,
        uif=True,
    ):
        return gross_up_by_bisection(
            self,
            net_monthly,
            age=age,
            retirement_monthly=retirement_monthly,
            medical_members=medical_members,
            medical_payroll_monthly=medical_payroll_monthly,
            other_payroll_monthly=other_payroll_monthly,
            uif=uif,
        )


class ZeroTaxModel(StubTaxModel):
    """No tax at all (hand-computed cash-flow tables)."""

    version = "zero-tax"

    def income_tax(self, taxable_annual, age):
        return np.zeros(np.shape(np.asarray(taxable_annual, dtype=float)))

    def person_tax(self, **kw):
        out = super().person_tax(**kw)
        return PersonTax(np.zeros_like(out.annual_tax), np.zeros_like(out.monthly_uif))


def sample_document() -> F.FnaDocument:
    return F.FnaDocument.model_validate(json.loads((FIXTURES / "sample_fna.json").read_text()))


def sample_analysis() -> dict[str, Any]:
    return json.loads((FIXTURES / "sample_analysis.json").read_text())


def prior_profile(doc: F.FnaDocument | None = None, *, call_id: str = "c-forecast") -> Profile:
    return binding.load_prior(doc, None, call_id=call_id, run_id="wf-forecast", anchor_date=ANCHOR)


def empty_profile(call_id: str = "c-empty") -> Profile:
    return Profile(call_id=call_id, run_id="wf-forecast", schema_version="profile_v1", anchor_date=ANCHOR)


def evidence(seg: str = "s0001") -> Evidence:
    return Evidence(
        segment_id=seg,
        char_start=0,
        char_end=4,
        quote="said",
        speaker_role="client_primary",
        t_start=1,
        t_end=2,
        asr_mean_conf=0.9,
        role="supports",
    )


def add_fact(
    profile: Profile,
    slot_id: str,
    path: str,
    dist: Dist | list[tuple[float, Dist]],
    *,
    instance: str | None = None,
    unit: str = "ZAR",
    period: str = "none",
    basis: str = "na",
    status: str = "stated",
    source: str = "call",
    p_correct: float = 0.9,
    review: str | None = None,
    auto_accepted: bool = False,
    entity_refs: list[str] | None = None,
) -> tuple[Profile, str]:
    """Add (or replace) a call fact through the reducer, then set its scoring annotations."""
    fid = f"{slot_id}#{instance}" if instance else slot_id
    hyps = dist if isinstance(dist, list) else [(1.0, dist)]
    payload = {
        "slot_id": slot_id,
        "instance_key": instance,
        "fna_path": path,
        "source": source,
        "status": status,
        "hypotheses": [Hypothesis(value=Quantity(dist=d, unit=unit, period=period, basis=basis), weight=w, rationale="") for w, d in hyps],
        "evidence": [evidence()] if status in ("stated", "implied", "contradicted") else [],
        "entity_refs": entity_refs or [],
        "provenance": PROV,
    }
    result = apply_state_ops(profile, make_ops(profile, [("ADD", fid, "s0001", payload, "extractor")]))
    assert not result.rejected, result.rejected
    profile = result.profile
    f = profile.facts[fid]
    update: dict[str, Any] = {"p_correct": p_correct, "auto_accepted": auto_accepted}
    if review:
        update["review"] = ReviewState(status=review)
    facts = dict(profile.facts)
    facts[fid] = f.model_copy(update=update)
    return profile.model_copy(update={"facts": facts}), fid


def mark_unknown(
    profile: Profile, fid: str, *, slot_id: str | None = None, path: str | None = None, instance: str | None = None
) -> Profile:
    payload: dict[str, Any] = {"reason": "client unsure"}
    if fid not in profile.facts:
        payload.update({"slot_id": slot_id, "instance_key": instance, "fna_path": path, "provenance": PROV})
    result = apply_state_ops(profile, make_ops(profile, [("MARK_UNKNOWN", fid, "s0002", payload, "extractor")]))
    assert not result.rejected, result.rejected
    return result.profile


def reject(profile: Profile, fid: str) -> Profile:
    result = apply_state_ops(profile, make_ops(profile, [("RETRACT", fid, None, {"reason": "misheard"}, "reviewer")]))
    assert not result.rejected, result.rejected
    return result.profile


def point(v: Any) -> Point:
    return Point(value=v)
