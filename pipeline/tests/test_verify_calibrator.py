"""The calibrator (§9.4) on synthetic labels with a known miscalibration."""

from __future__ import annotations

import math

import numpy as np
import pytest

from advisor_intel.models.runs import VersionPins
from advisor_intel.verify.calibrator import (
    COLD_WEIGHTS,
    Calibrator,
    LabelSet,
    brier,
    call_folds,
    choose_map,
    cold_scores,
    ece,
    fit_calibrator,
    plan,
    reliability,
    retrain,
    retrain_due,
    should_promote,
)
from advisor_intel.verify.features import feature_names
from advisor_intel.verify.labels import Label

from .verify_factories import CATALOG

NAMES = feature_names(CATALOG)


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def synth(
    n_calls: int, per_call: int, groups: list[str], *, seed: int, warm_signal: bool = False, call_prefix: str = "call"
) -> list[Label]:
    """Labels whose true P(correct) = s³, so the cold score s is overconfident; with ``warm_signal`` it is
    lower still and falls with two flags the cold score ignores (an ambiguous scale reading, a lexicon miss)."""
    rng = np.random.default_rng(seed)
    out = []
    for c in range(n_calls):
        for j in range(per_call):
            g = groups[(c * per_call + j) % len(groups)]
            entail, cons, self_s, asr = rng.uniform(0, 1), rng.uniform(0, 1), rng.uniform(0, 1), rng.uniform(0.5, 1)
            scale, miss = float(rng.random() < 0.3), float(rng.random() < 0.25)
            s = 0.4 * entail + 0.3 * cons + 0.2 * self_s + 0.1 * asr
            p = _sigmoid(-0.5 + 4 * (s - 0.5) - 3.0 * scale - 2.0 * miss) if warm_signal else s**3
            feats = {
                "entail_score": entail,
                "consistency": cons if rng.random() > 0.1 else None,
                "self_score": self_s,
                "asr_mean_conf": asr,
                "scale_ambiguous": scale,
                "lexicon_miss": miss,
                f"group_{g}": 1.0,
            }
            out.append(
                Label(
                    call_id=f"{call_prefix}{c}",
                    run_id="r",
                    fact_id=f"f{c}_{j}",
                    slot_id="x",
                    slot_group=g,
                    family=CATALOG.groups[g].family,
                    tier="A",
                    correct=int(rng.random() < p),
                    action="accepted",
                    reason="",
                    p_raw=s,
                    p_correct=s,
                    auto_accepted=False,
                    eligible=True,
                    features=feats,
                )
            )
    return out


def _raw_and_p(cal, labels):
    rows = [(lb.features, lb.slot_group, lb.family) for lb in labels]
    raw, p = cal.predict(rows)
    y = np.asarray([lb.correct for lb in labels], dtype=float)
    return raw, p, y


def test_metrics():
    p = np.array([0.1, 0.2, 0.8, 0.9])
    y = np.array([0, 0, 1, 1])
    assert brier(p, y) == pytest.approx((0.01 + 0.04 + 0.04 + 0.01) / 4)
    assert ece(np.full(15, 0.5), np.array([1] * 15)) == pytest.approx(0.5)
    rel = reliability(np.linspace(0, 1, 30), np.zeros(30))
    assert len(rel) == 15 and all(b["count"] == 2 for b in rel)  # equal-mass bins
    assert ece(np.array([]), np.array([])) != ece(np.array([]), np.array([]))  # NaN


def test_identity_before_any_labels_uses_the_cold_score():
    cal = Calibrator.identity(CATALOG)
    feats = {"entail_score": 1.0, "consistency": None, "self_score": 0.5, "asr_mean_conf": 0.9}
    raw, p = cal.predict_one(feats, "income", "income_expenses")
    assert raw == pytest.approx(0.4 * 1.0 + 0.3 * 0.5 + 0.2 * 0.5 + 0.1 * 0.9)  # NaN → 0.5 with no labels yet
    assert p == pytest.approx(raw)
    assert set(COLD_WEIGHTS) == {"entail_score", "consistency", "self_score", "asr_mean_conf"}


def test_cold_start_isotonic_fixes_a_known_miscalibration():
    train = synth(40, 6, ["income", "retirement", "estate"], seed=1)
    test = synth(100, 6, ["income", "retirement", "estate"], seed=2, call_prefix="t")
    cal = fit_calibrator(train, catalog=CATALOG)
    assert cal.mode == "cold" and cal.n_labels == 240
    assert cal.routing.group_units == {"income", "retirement", "estate"}  # each has ≥ 50 labels
    raw, p, y = _raw_and_p(cal, test)
    assert ece(raw, y) > 0.2  # s overstates P(correct) = s³
    assert ece(p, y) < 0.1 and ece(p, y) < ece(raw, y) / 3
    assert brier(p, y) < brier(raw, y)
    assert cal.metrics["heldout"] and cal.metrics["overall"]["ece"] < 0.15 and len(cal.metrics["overall"]["reliability"]) == 15


def test_cold_grouping_and_family_pooling_thresholds():
    labels = (
        synth(12, 5, ["income"], seed=3, call_prefix="a")  # 60 → its own group map
        + synth(6, 5, ["retirement"], seed=4, call_prefix="b")  # 30 ┐ family "goals": 55 ≥ 50 → a family map
        + synth(5, 5, ["goals"], seed=5, call_prefix="c")  # 25 ┘
        + synth(2, 5, ["estate"], seed=6, call_prefix="d")
    )  # 10, family policies_cover 10 < 50 → pooled
    cal = fit_calibrator(labels, catalog=CATALOG)
    assert cal.routing.key("income", "income_expenses") == "group:income"
    assert cal.routing.key("retirement", "goals") == "family:goals" and cal.routing.key("goals", "goals") == "family:goals"
    assert cal.routing.key("estate", "policies_cover") == "pooled"
    assert cal.units["family:goals"].n_labels == 55 and cal.units["pooled"].n_labels == len(labels)
    # an unseen group is served by the pooled map
    assert cal.unit_for("assets", "assets_liabilities") is cal.units["pooled"]


def test_warm_boosting_per_large_group_and_pooled_with_one_hots():
    train = synth(50, 8, ["income", "income", "retirement", "estate"], seed=7, warm_signal=True)
    test = synth(80, 8, ["income", "income", "retirement", "estate"], seed=8, warm_signal=True, call_prefix="t")
    cal = fit_calibrator(train, catalog=CATALOG)
    assert cal.mode == "warm" and cal.n_labels == 400 and cal.n_calls == 50
    assert cal.routing.group_units == {"income"}  # 200 labels; the other groups (100 each) share the pooled model
    for key in ("group:income", "pooled"):
        unit = cal.units[key]
        assert unit.mode == "warm" and len(unit.members) == 4
        for m in unit.members:  # each map is fit on calls its booster never saw
            assert m.cal_calls and not m.fit_calls & m.cal_calls
    raw_cold = cold_scores(LabelSet.from_labels(test, NAMES).X, NAMES, cal.global_fill)
    _, p, y = _raw_and_p(cal, test)
    assert ece(raw_cold, y) > 0.2  # the cold score overstates P(correct)
    assert ece(p, y) < 0.07 and ece(p, y) < ece(raw_cold, y) / 3
    from sklearn.metrics import roc_auc_score

    assert roc_auc_score(y, p) > roc_auc_score(y, raw_cold) + 0.05  # boosting uses the flags the cold score ignores


def test_venn_abers_is_chosen_only_when_brier_and_ece_are_both_better():
    assert choose_map({"brier": 0.20, "ece": 0.05}, {"brier": 0.19, "ece": 0.04}) == "venn_abers"
    assert choose_map({"brier": 0.20, "ece": 0.05}, {"brier": 0.19, "ece": 0.06}) == "isotonic"
    assert choose_map({"brier": 0.20, "ece": 0.05}, {"brier": 0.21, "ece": 0.04}) == "isotonic"
    assert choose_map({"brier": math.nan, "ece": 0.05}, {"brier": 0.1, "ece": 0.01}) == "isotonic"
    cal = fit_calibrator(synth(30, 6, ["income", "retirement"], seed=9), catalog=CATALOG)
    for unit in cal.units.values():
        if not unit.heldout:
            continue
        iso, va = unit.heldout["isotonic"], unit.heldout["venn_abers"]
        assert unit.chosen == ("venn_abers" if va["brier"] < iso["brier"] and va["ece"] < iso["ece"] else "isotonic")
        X = LabelSet.from_labels(synth(2, 5, ["income"], seed=10), NAMES).X
        raw, p = unit.predict(X, NAMES)
        assert np.allclose(p, unit.maps[unit.chosen].predict(raw))


def test_splits_are_by_call_never_by_fact():
    labels = synth(12, 7, ["income", "estate"], seed=11)
    calls = np.asarray([lb.call_id for lb in labels], dtype=object)
    folds = call_folds(calls, 5)
    assert len(folds) == 5
    for train, test in folds:
        assert not set(calls[train]) & set(calls[test])
    assert sorted(np.concatenate([t for _, t in folds]).tolist()) == list(range(len(labels)))
    assert call_folds(np.asarray(["only"] * 5, dtype=object)) == []


def test_plan_thresholds_warm():
    data = LabelSet.from_labels(synth(30, 10, ["income", "income", "estate"], seed=12), NAMES)
    assert plan(data, "warm").group_units == {"income"} and plan(data, "warm").family_units == set()


def test_promotion_rule_and_retrain_cycle():
    assert should_promote(0.045, 0.04)[0] and not should_promote(0.06, 0.04)[0] and should_promote(0.06, None)[0]
    pins = VersionPins(reasoning_model="anthropic:claude-opus-5-5", asr_provider="assemblyai")
    first = synth(40, 5, ["income", "retirement"], seed=13)
    rep = retrain(first, incumbent=None, pins=pins, catalog=CATALOG)
    assert rep.trigger == "no trained calibrator" and rep.promoted
    incumbent = rep.candidate
    assert retrain(first, incumbent=incumbent, pins=pins, catalog=CATALOG).reason == "not due"
    more = first + synth(45, 5, ["income", "retirement"], seed=14, call_prefix="n")
    rep2 = retrain(more, incumbent=incumbent, pins=pins, catalog=CATALOG)
    assert rep2.trigger == "225 new labels" and rep2.compared_on == 225  # scored on the labels the incumbent never saw
    assert rep2.promoted == (rep2.candidate_ece <= rep2.incumbent_ece + 0.01)


def test_retrain_triggers():
    pins = VersionPins(reasoning_model="anthropic:claude-opus-5-5", asr_provider="assemblyai", prompt_versions={"extract_values": "v3"})
    cal = fit_calibrator(synth(10, 5, ["income"], seed=15), catalog=CATALOG, pins=pins)
    assert retrain_due(cal, cal.n_labels + 10, pins) == (False, "not due")
    assert retrain_due(cal, cal.n_labels + 200, pins)[0]
    assert retrain_due(cal, cal.n_labels, pins.model_copy(update={"reasoning_model": "openai:gpt-5"})) == (True, "extraction model changed")
    assert retrain_due(cal, cal.n_labels, pins.model_copy(update={"prompt_versions": {"extract_values": "v4"}})) == (
        True,
        "prompt version changed",
    )
    assert retrain_due(cal, cal.n_labels, pins.model_copy(update={"asr_provider": "deepgram"})) == (True, "ASR changed")
    assert retrain_due(None, 0) == (False, "no labels yet")


def test_artifact_round_trip_with_version():
    labels = synth(20, 5, ["income", "estate"], seed=16)
    cal = fit_calibrator(labels, catalog=CATALOG)
    blob = cal.to_bytes()
    assert blob[:5] == b"AICAL" and blob[5] == 1
    back = Calibrator.from_bytes(blob)
    rows = [(lb.features, lb.slot_group, lb.family) for lb in labels]
    assert np.allclose(back.predict(rows)[1], cal.predict(rows)[1]) and back.version == cal.version
    with pytest.raises(ValueError):
        Calibrator.from_bytes(b"nope")
    with pytest.raises(ValueError):
        Calibrator.from_bytes(b"AICAL" + bytes([9]) + blob[6:])
    assert cal.experiment()["name"] == f"calibration/{cal.version}"
