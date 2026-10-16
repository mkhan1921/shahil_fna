"""Conformal auto-accept (§9.5), spot checks (§11.3) and the wrong-auto-accept monitor (§14.4)."""

from __future__ import annotations

from advisor_intel.config import Settings
from advisor_intel.extract.reducer import annotate
from advisor_intel.verify.conformal import (
    CalibrationCall,
    ConformalThresholds,
    WrongAutoAcceptMonitor,
    auto_accept_decision,
    calibration_calls,
    call_score,
    conformal_quantile,
    fit_conformal,
    is_spot_check,
)
from advisor_intel.verify.labels import Label

from .verify_factories import CATALOG, ProfileBuilder, ev, money, point


def _label(call, p, correct, family="income_expenses", tier="B", eligible=True):
    return Label(
        call_id=call,
        run_id="r",
        fact_id=f"{call}-{p}-{correct}",
        slot_id="x",
        slot_group="income",
        family=family,
        tier=tier,
        correct=correct,
        action="accepted",
        reason="",
        p_raw=p,
        p_correct=p,
        auto_accepted=False,
        eligible=eligible,
    )


def test_call_score_is_the_most_confident_wrong_fact():
    c = CalibrationCall(call_id="c", facts=[(0.9, 1, "goals"), (0.7, 0, "goals"), (0.8, 0, "household"), (0.95, 1, "household")])
    assert call_score(c) == 0.8 and call_score(c, "goals") == 0.7 and call_score(c, "income_expenses") == 0.0
    assert call_score(CalibrationCall(call_id="ok", facts=[(0.99, 1, "goals")])) == 0.0


def test_tau_by_hand():
    scores = [i / 100 for i in range(1, 51)]  # 0.01 … 0.50, N = 50
    # α = 0.1: ⌈51 × 0.9⌉ = ⌈45.9⌉ = 46 → the 46th smallest score
    assert conformal_quantile(scores, 0.1) == 0.46
    # α/5 = 0.02: ⌈51 × 0.98⌉ = ⌈49.98⌉ = 50 → the largest
    assert conformal_quantile(scores, 0.02) == 0.50
    # with too few scores for the rank, there is no finite threshold
    assert conformal_quantile(scores[:40], 0.02) is None
    labels = [_label(f"call{i}", i / 100, 0) for i in range(1, 51)]
    th = fit_conformal(labels, alpha=0.1, min_calls=50, mondrian=False, settings=Settings())
    assert th.enabled and th.n_calls == 50 and th.tau_pooled == 0.46 and th.tau_for("goals") == 0.46


def test_fewer_than_50_calls_turns_auto_accept_off():
    labels = [_label(f"call{i}", 0.5, 0) for i in range(49)]
    th = fit_conformal(labels, settings=Settings())
    assert not th.enabled and "49 calibration calls" in th.reason and th.tau_for("income_expenses") is None


def test_only_the_last_n_calibration_calls_count():
    old = [_label(f"old{i}", 0.99, 0) for i in range(20)]  # an earlier pipeline's confident mistakes
    recent = [_label(f"call{i}", i / 100, 0) for i in range(1, 51)]
    th = fit_conformal(old + recent, alpha=0.1, min_calls=50, mondrian=False, last_n=50, settings=Settings())
    assert th.n_calls == 50 and th.tau_pooled == 0.46


def test_mondrian_per_family_at_alpha_over_5_with_pooled_fallback():
    labels = []
    for i in range(60):
        call = f"call{i}"
        labels.append(_label(call, 0.30 + i / 1000, 0, family="income_expenses"))  # every call has an income fact
        labels.append(_label(call, 0.90, 1, family="income_expenses"))
        if i < 20:  # only 20 calls have a goals fact: too few for its own τ
            labels.append(_label(call, 0.80, 0, family="goals"))
    th = fit_conformal(labels, alpha=0.1, min_calls=50, settings=Settings())
    assert th.enabled and th.mondrian and th.family_alpha == 0.02
    income_scores = sorted(0.30 + i / 1000 for i in range(60))
    k = 60  # ⌈61 × 0.98⌉ = ⌈59.78⌉ = 60
    assert th.source_by_family["income_expenses"] == "family" and th.tau_by_family["income_expenses"] == income_scores[k - 1]
    assert th.n_by_family["goals"] == 20 and th.source_by_family["goals"] == "pooled"
    assert th.tau_by_family["goals"] == th.tau_pooled_family == 0.80  # the pooled score is the call's max over families
    assert th.tau_for("household") == th.tau_pooled_family  # no calibration facts at all: pooled


def test_ineligible_and_tier_a_facts_stay_out_of_the_scores():
    labels = [_label("c1", 0.99, 0, eligible=False), _label("c1", 0.4, 0), _label("c1", 0.97, 0, tier="A")]
    assert calibration_calls(labels)[0].facts == [(0.4, 0, "income_expenses")]
    with_a = calibration_calls(labels, auto_accept_tier_a=True)[0].facts
    assert (0.97, 0, "income_expenses") in with_a


def _fact(slot, inst, value, p, **kw):
    b = ProfileBuilder()
    fid = b.add(slot, inst, value, **kw)
    prof = b.build()
    return annotate(prof, fid, p_correct=p), fid


def test_the_auto_accept_predicate():
    th = ConformalThresholds(
        enabled=True, reason="test", mondrian=True, tau_by_family={"income_expenses": 0.8, "household": 0.8}, tau_pooled_family=0.8
    )
    off = Settings()
    on = Settings(auto_accept_tier_a=True)
    prof, fid = _fact("income.{p}.annualBonus", "client", money(60000, "annual"), 0.85)  # tier B
    assert auto_accept_decision(prof.facts[fid], th, catalog=CATALOG, settings=off)[0]
    low, lfid = _fact("income.{p}.annualBonus", "client", money(60000, "annual"), 0.8)
    assert not auto_accept_decision(low.facts[lfid], th, catalog=CATALOG, settings=off)[0]  # strictly above τ
    a, afid = _fact("income.{p}.grossMonthly", "client", money(45000), 0.99)  # tier A: off by default
    ok, why = auto_accept_decision(a.facts[afid], th, catalog=CATALOG, settings=off)
    assert not ok and "tier A" in why
    assert auto_accept_decision(a.facts[afid], th, catalog=CATALOG, settings=on)[0]
    implied, ifid = _fact("income.{p}.annualBonus", "client", money(60000, "annual"), 0.99, status="implied")
    assert not auto_accept_decision(implied.facts[ifid], th, catalog=CATALOG, settings=off)[0]
    hard = annotate(prof, fid, verification={"constraint": "fail"})
    assert "hard constraint" in auto_accept_decision(hard.facts[fid], th, catalog=CATALOG, settings=off)[1]
    contra, cfid = _fact(
        "income.{p}.annualBonus", "client", money(60000, "annual"), 0.99, evidence=[ev(), ev("s9", "no it's less", role="contradicts")]
    )
    assert "contradicting" in auto_accept_decision(contra.facts[cfid], th, catalog=CATALOG, settings=off)[1]
    flagged = annotate(prof, fid, verification={"contradicted": True})
    assert not auto_accept_decision(flagged.facts[fid], th, catalog=CATALOG, settings=off)[0]
    assert not auto_accept_decision(prof.facts[fid], th.disabled("monitor alert"), catalog=CATALOG, settings=off)[0]
    assert not auto_accept_decision(prof.facts[fid], None, catalog=CATALOG, settings=off)[0]
    smoker, sfid = _fact("{p}.smoker", "client", point(False), 0.9)  # household family τ
    assert auto_accept_decision(smoker.facts[sfid], th, catalog=CATALOG, settings=off)[0]


def test_spot_checks_are_one_in_ten_and_stable_per_call():
    picks = [is_spot_check(f"call-{i}", 0.1) for i in range(5000)]
    assert 0.085 < sum(picks) / len(picks) < 0.115
    assert is_spot_check("call-7", 0.1) == is_spot_check("call-7", 0.1)
    assert not any(is_spot_check(f"c{i}", 0.0) for i in range(100)) and all(is_spot_check(f"c{i}", 1.0) for i in range(100))


def test_wrong_auto_accept_monitor_alerts_above_alpha_over_100_calls():
    m = WrongAutoAcceptMonitor(alpha=0.1)
    for i in range(99):
        m = m.record(f"c{i}", 5, 1 if i < 15 else 0)
    assert m.rate() == 15 / 99 and not m.alert()  # not yet a full window
    m = m.record("c99", 4, 0)
    assert len(m.outcomes) == 100 and m.alert()
    th = ConformalThresholds(enabled=True, reason="fitted")
    assert not m.apply(th).enabled and "exceeds" in m.apply(th).reason
    calm = WrongAutoAcceptMonitor(alpha=0.1)
    for i in range(120):
        calm = calm.record(f"c{i}", 3, 1 if i % 20 == 0 else 0)
    assert len(calm.outcomes) == 100 and calm.rate() == 0.05 and not calm.alert() and calm.apply(th).enabled
    labelled = WrongAutoAcceptMonitor().record_labels(
        "cx",
        [
            Label(
                call_id="cx",
                run_id="r",
                fact_id="f",
                slot_id="x",
                slot_group="g",
                family="goals",
                tier="B",
                correct=0,
                action="edited",
                reason="edited_outside",
                p_raw=0.9,
                p_correct=0.9,
                auto_accepted=True,
                eligible=True,
            )
        ],
    )
    assert labelled.outcomes[0].n_auto_accepted == 1 and labelled.outcomes[0].n_wrong == 1
