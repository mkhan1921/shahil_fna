"""The §15.9 reward terms and the reward-hacking checks."""

from __future__ import annotations

import json
import math
from datetime import date

import pytest

from advisor_intel.evals import rl_reward as R
from advisor_intel.evals.gold import GoldFact, GoldValue
from advisor_intel.extract.reducer import apply_state_ops, make_ops
from advisor_intel.models.dists import Bernoulli, Categorical, Normal, Point, Quantity, Triangular
from advisor_intel.models.facts import Evidence, Hypothesis, Provenance
from advisor_intel.models.profile import Profile
from advisor_intel.schema.slots import load_catalog

CAT = load_catalog()
RA, INC, SMOKER, WILL = "retirement.{p}.retirementAge", "income.{p}.grossMonthly", "{p}.smoker", "estate.{p}.hasWill"
SEGMENTS = {"s1": "I want to retire at sixty-five", "s2": "I earn about R50,000 a month"}


def age(v):
    return Quantity(dist=Point(value=v), unit="age")


def zar(dist):
    return Quantity(dist=dist, unit="ZAR", period="monthly")


def test_scoring_rules():
    assert R.interval_score(60, 64, 65) == pytest.approx(4 + 10 * 1)  # width 4, miss 1 at α = 0.2
    assert R.interval_score(60, 64, 62) == 4
    assert R.crps(Point(value=60), 65) == 5
    assert R.crps(Normal(mean=0, sd=1), 0.0, n=20000) == pytest.approx((math.sqrt(2) - 1) / math.sqrt(math.pi), abs=0.01)
    slot = CAT.slot(INC)
    y = GoldValue(kind="numeric", p50=50_000)
    exact = R.value_score(slot, zar(Point(value=50_000)), y)
    narrow = R.value_score(slot, zar(Triangular(low=47_500, mode=50_000, high=52_500)), y, range_like=True)
    wide = R.value_score(slot, zar(Triangular(low=10_000, mode=50_000, high=90_000)), y, range_like=True)
    off = R.value_score(slot, zar(Point(value=40_000)), y)
    assert exact == 1.0 and 1.0 > narrow > wide >= 0.0 and wide < 0.2  # a very wide range cannot score well
    assert off == pytest.approx(1 - 10_000 / 25_000)
    cat = R.value_score(CAT.slot(WILL), Quantity(dist=Categorical(probs={"yes": 0.8, "no": 0.2}), unit="none"), GoldValue(kind="categorical", label="yes"))
    assert cat == pytest.approx(1 - 0.5 * (0.2**2 + 0.2**2))
    assert R.value_score(CAT.slot(SMOKER), Quantity(dist=Bernoulli(p=0.3), unit="none"), GoldValue(kind="boolean", label="false")) == pytest.approx(1 - 0.09)


def test_reward_terms_hand_computed():
    reviewed = [
        GoldFact(slot_id=RA, instance_key="client", status="stated", value=GoldValue(kind="numeric", p50=65)),
        GoldFact(slot_id=INC, instance_key="client", status="stated", attribution="client", value=GoldValue(kind="numeric", p50=50_000)),
        GoldFact(slot_id=SMOKER, instance_key="client", status="not_discussed"),
    ]
    rollout = [
        R.RolloutFact(slot_id=RA, instance_key="client", status="stated", value=age(65), quotes=[("s1", "retire at sixty-five")], self_score=0.9),
        R.RolloutFact(slot_id=INC, instance_key="client", status="stated", value=zar(Point(value=40_000)), quotes=[("s2", "R50,000"), ("s2", "R60,000")],
                      attribution="advisor_suggested", self_score=0.8),
        R.RolloutFact(slot_id=WILL, instance_key="client", status="stated", value=Quantity(dist=Point(value="yes"), unit="none"),
                      quotes=[("s1", "retire")], self_score=0.5),
    ]
    rb = R.reward(rollout, reviewed, SEGMENTS)
    assert rb.n_slots == 4  # RA, INC, SMOKER, WILL
    assert rb.status == 3  # RA, INC and SMOKER (both not discussed); WILL is invented
    # RA exact (1.0) + INC off by 10k on a 25k scale (0.6); WILL has no reviewed value
    assert rb.value == pytest.approx(1.0 + 0.6)
    assert rb.grounded == pytest.approx(0.5 + 0.5)  # RA and WILL quotes are found; INC cites a quote that is not in s2
    assert rb.unsupported == -2.0  # WILL
    assert rb.attribution == -1.0  # INC: the client's statement recorded as an advisor suggestion
    # self-scores: RA correct (0.9 → 0.01), INC wrong value (0.8 → 0.64), WILL wrong status (0.5 → 0.25)
    assert rb.calibration == pytest.approx(-(0.01 + 0.64 + 0.25) / 3)
    assert rb.total == pytest.approx(3 + 1.6 + 1.0 - 2 - 1 - 0.3)
    # restricting to the task's slots ignores the rest
    assert R.reward(rollout, reviewed, SEGMENTS, slots=[(RA, "client")]).n_slots == 1


def test_reviewed_facts_from_a_profile():
    prov = Provenance(run_id="r")
    ev = Evidence(segment_id="s1", char_start=0, char_end=5, quote="sixty", speaker_role="client_primary", t_start=0, t_end=1, asr_mean_conf=0.9, role="supports")
    p = Profile(call_id="c", run_id="r", schema_version="profile_v1", anchor_date=date(2026, 1, 1))
    add = lambda slot, inst, path, q: ("ADD", f"{slot}#{inst}", "s1", {"slot_id": slot, "instance_key": inst, "fna_path": path,  # noqa: E731
                                                                         "hypotheses": [Hypothesis(value=q, weight=1, rationale="")], "evidence": [ev],
                                                                         "provenance": prov}, "extractor")
    p = apply_state_ops(p, make_ops(p, [add(RA, "client", "retirement.client.retirementAge", age(60)),
                                        add(INC, "client", "income.client.grossMonthly", zar(Triangular(low=40_000, mode=45_000, high=50_000))),
                                        add(SMOKER, "client", "client.smoker", Quantity(dist=Point(value=False), unit="none"))])).profile
    p = apply_state_ops(p, make_ops(p, [("CONFIRM", f"{RA}#client", None, {}, "reviewer"), ("RETRACT", f"{INC}#client", None, {"reason": "misheard"}, "reviewer")])).profile
    facts = {f.key: f for f in R.reviewed_facts(p)}
    assert facts[(RA, "client")].value.p50 == 60 and facts[(INC, "client")].status == "not_discussed"
    assert (SMOKER, "client") not in facts  # never reviewed
    assert (SMOKER, "client") in {f.key for f in R.reviewed_facts(p, include_pending=True)}


def _out(pid, group, facts):
    return R.RolloutOutput(prompt_id=pid, group=group, facts=facts)


def test_not_discussed_rate_check():
    stated = R.RolloutFact(slot_id=RA, instance_key="client", status="stated", value=age(65))
    nd = R.RolloutFact(slot_id=RA, instance_key="spouse", status="not_discussed")
    ref = [_out(str(i), "retirement", [stated, nd]) for i in range(10)]  # 50%
    same = R.check_not_discussed(ref, ref)
    assert all(r.passed for r in same)
    dodging = [_out(str(i), "retirement", [nd, nd if i < 2 else stated]) for i in range(10)]  # 60%
    rows = R.check_not_discussed(dodging, ref)
    assert not rows[0].passed and rows[0].note == "dodging penalties"


def test_range_width_check():
    reviewed = {str(i): [GoldFact(slot_id=INC, instance_key="client", status="stated", value=GoldValue(kind="numeric", p50=50_000))] for i in range(5)}
    narrow = [_out(str(i), "income", [R.RolloutFact(slot_id=INC, instance_key="client", status="stated",
                                                    value=zar(Triangular(low=45_000, mode=50_000, high=55_000)))]) for i in range(5)]
    wide = [_out(str(i), "income", [R.RolloutFact(slot_id=INC, instance_key="client", status="stated",
                                                  value=zar(Triangular(low=20_000, mode=50_000, high=80_000)))]) for i in range(5)]
    rows = R.check_range_width(wide, narrow, reviewed)
    assert not rows[0].passed and "widened" in rows[0].note  # coverage was already 100%: wider ranges bought nothing
    missed = [_out(str(i), "income", [R.RolloutFact(slot_id=INC, instance_key="client", status="stated",
                                                    value=zar(Triangular(low=55_000, mode=60_000, high=65_000)))]) for i in range(5)]
    assert R.check_range_width(narrow, missed, reviewed)[0].passed  # wider but now covering: allowed


def test_samples_for_human_review(tmp_path):
    generic = R.RolloutFact(slot_id=RA, instance_key="client", status="stated", value=age(65), quotes=[("s1", "as discussed earlier on")],
                            interpretation="the client said so")
    outs = [_out(f"p{i}", "retirement", [generic]) for i in range(60)]
    samples = R.sample_for_review(outs, n=50, seed=1)
    assert len(samples) == 50 and len({s["prompt_id"] for s in samples}) == 50
    assert any("generic quote" in f for f in samples[0]["flags"]) and any("boilerplate" in f for f in samples[0]["flags"])
    path = R.write_review_sheet(samples, tmp_path / "review.jsonl")
    assert not R.review_signed_off(path)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    path.write_text("".join(json.dumps(dict(r, reviewer_verdict="ok")) + "\n" for r in rows))
    assert R.review_signed_off(path)
    rep = R.hacking_checks(outs, outs, {}, review_path=tmp_path / "r2.jsonl")
    assert rep.passed and rep.flagged_samples == 50 and rep.review_sheet.endswith("r2.jsonl")
