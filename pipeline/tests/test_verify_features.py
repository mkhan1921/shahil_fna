"""The calibration feature vector (§9.2): completeness, sources and NaN rules."""

from __future__ import annotations

import math

import pytest

from advisor_intel.extract.reducer import annotate
from advisor_intel.models.entities import DiscourseOutput, Mention
from advisor_intel.models.facts import Hedge
from advisor_intel.models.transcript import QualityReport
from advisor_intel.verify.features import SPEC_FEATURES, feature_names, features, to_storable, vector

from .verify_factories import CATALOG, ProfileBuilder, ev, money, point, segment, span, transcript

# Every row of the §9.2 table (one-hot rows as prefixes).
SPEC_TABLE = ["self_score", "entail_score", "consistency", "n_evidence", "n_supporting_segments", "asr_mean_conf", "asr_min_conf", "low_asr_overlap", "diarization_conf", "role_conf", "speaker_is_client", "resolution_conf", "crm_link_score", "hedge_strength_max", "scale_ambiguous", "revised", "restated", "n_hypotheses", "top_weight", "lexicon_miss", "constraint_soft_violations", "derived", "grammar_readings", "grammar_top_margin", "second_pass_run", "second_pass_agreement", "distance_from_prior", "aligner_score", "reopened_by_coverage", "field_retries", "term_unresolved"]


def test_vector_is_complete_against_the_spec_table():
    names = feature_names(CATALOG)
    assert set(SPEC_TABLE) <= set(names) and list(SPEC_FEATURES) == SPEC_TABLE
    assert {"minicheck_score", "lexicon_near_miss", "call_quality_capped"} <= set(names)
    for s in ("stated", "implied", "explicitly_unknown", "not_discussed", "contradicted"):
        assert f"status_{s}" in names
    for p in ("categorical", "span_normalise", "open_list", "boolean", "text"):
        assert f"policy_{p}" in names
    assert {f"group_{g}" for g in CATALOG.group_names()} <= set(names)
    assert len(names) == len(set(names))


def _setup():
    b = ProfileBuilder()
    b.entity("account.ra", "account", "Allan Gray RA")
    ents = dict(b.profile.entities)
    ents["account.ra"] = ents["account.ra"].model_copy(update={"crm_match": [("crm-9", 0.82)]})
    b.profile = b.profile.model_copy(update={"entities": ents})
    quote = "two fifty thousand"
    fid = b.add(
        "retirementFunds[].value",
        "account.ra",
        [(money(250000, "once"), 0.7), (money(2500000, "once"), 0.3)],
        evidence=[ev("s0002", quote, start=12), ev("s0005", quote, start=0)],
        prior=money(240000, "once"),
        self_score=0.66,
        hedges=[Hedge(term="about", strength=0.2, lexicon_key="about")],
        normalization={
            "form": {"type": "AMOUNT_EXACT"},
            "lexicon": [{"section": "approx_words", "phrase": "ish", "match": "miss"}],
            "scale": {"scale_ambiguous": True},
        },
        field_retries=1,
    )
    p = b.build()
    t = transcript(
        [
            segment("s0001", "What is the RA worth?", "advisor"),
            segment("s0002", "It's around two fifty thousand now", confs=[0.95, 0.9, 0.4, 0.8, 0.9, 0.95], t0=3, diar=0.8, role_conf=0.9),
            segment("s0005", "two fifty thousand yes", confs=[0.9, 0.9, 0.9, 0.7], t0=9),
        ],
        spans=[span("sp1", "s0002", 2, 5, [(250000, 0.6), (2500000, 0.3), (250, 0.1)], second=250000.0, agree=True)],
        quality=QualityReport(
            mean_word_conf=0.78, low_asr_share=0.2, crosstalk_share=0, n_speakers=2, role_conf_min=0.9, cap_p_correct=True
        ),
    )
    return p, fid, t


def test_sources_of_each_feature():
    p, fid, t = _setup()
    p = annotate(p, fid, verification={"entail_score": 0.8, "minicheck_score": 0.9, "consistency": 2 / 3, "constraint_soft_violations": 2})
    disc = DiscourseOutput(
        entities=p.entities,
        mentions=[Mention(segment_id="s0002", char_start=0, char_end=4, surface="It's", entity_id="account.ra", resolution_conf=0.65)],
    )
    f = features(p.facts[fid], t, p, catalog=CATALOG, discourse=disc)
    assert f["self_score"] == 0.66 and f["entail_score"] == 0.8 and f["minicheck_score"] == 0.9
    assert f["consistency"] == pytest.approx(2 / 3)
    assert f["n_evidence"] == 2 and f["n_supporting_segments"] == 2
    # evidence words: "two fifty thousand" in both segments → confs 0.4, 0.8, 0.9 and 0.9, 0.9, 0.9
    assert f["asr_mean_conf"] == pytest.approx((0.4 + 0.8 + 0.9 + 0.9 + 0.9 + 0.9) / 6)
    assert f["asr_min_conf"] == pytest.approx(0.4) and f["low_asr_overlap"] == pytest.approx(1 / 6)
    assert f["diarization_conf"] == pytest.approx(0.8) and f["role_conf"] == pytest.approx(0.9)
    assert f["resolution_conf"] == pytest.approx(0.65)  # "It's" ties the amount to the RA, outside the quoted span
    assert f["crm_link_score"] == pytest.approx(0.82)
    assert f["hedge_strength_max"] == pytest.approx(0.2) and f["scale_ambiguous"] == 1.0 and f["lexicon_miss"] == 1.0
    assert f["n_hypotheses"] == 2 and f["top_weight"] == pytest.approx(0.7) and f["restated"] == 0.0
    assert f["grammar_readings"] == 3 and f["grammar_top_margin"] == pytest.approx(2.0)
    assert f["second_pass_run"] == 1.0 and f["second_pass_agreement"] == 1.0
    assert f["distance_from_prior"] == pytest.approx(abs(250000 - 240000) / 240000)
    assert f["aligner_score"] == pytest.approx(0.9) and f["field_retries"] == 1 and f["constraint_soft_violations"] == 2
    assert f["call_quality_capped"] == 1.0
    assert f["status_stated"] == 1.0 and f["policy_span_normalise"] == 1.0 and f["group_retirement_funds"] == 1.0
    assert sum(v for k, v in f.items() if k.startswith("group_")) == 1.0


def test_resolution_conf_reads_the_mentions_the_fact_relies_on():
    p, fid, t = _setup()
    unresolved = Mention(segment_id="s0002", char_start=0, char_end=4, surface="It's", entity_id=None, resolution_conf=0.4)
    other = Mention(segment_id="s0002", char_start=0, char_end=4, surface="It's", entity_id="person.client_primary", resolution_conf=0.2)
    elsewhere = Mention(segment_id="s0001", char_start=8, char_end=14, surface="the RA", entity_id="account.ra", resolution_conf=0.1)
    disc = DiscourseOutput(entities=p.entities, mentions=[unresolved, other, elsewhere])
    assert features(p.facts[fid], t, p, catalog=CATALOG, discourse=disc)["resolution_conf"] == pytest.approx(0.4)
    none = DiscourseOutput(entities=p.entities)
    assert features(p.facts[fid], t, p, catalog=CATALOG, discourse=none)["resolution_conf"] == 1.0
    assert math.isnan(features(p.facts[fid], t, p, catalog=CATALOG)["resolution_conf"])


def test_second_pass_disagreeing_with_the_chosen_reading():
    p, fid, t = _setup()
    t2 = t.model_copy(
        update={"numeric_spans": [span("sp1", "s0002", 2, 5, [(250000, 0.6), (2500000, 0.4)], second=2500000.0, agree=False)]}
    )
    f = features(p.facts[fid], t2, p, catalog=CATALOG)
    assert f["second_pass_run"] == 1.0 and f["second_pass_agreement"] == 0.0


def test_nan_rules():
    b = ProfileBuilder()
    occ = b.add("{p}.occupation", "client", point("engineer"), self_score=None, evidence=[ev("s0009", "engineer")])
    nd = b.add("income.{p}.annualBonus", "client", None, status="not_discussed", evidence=[], self_score=None)
    p = b.build()
    p = annotate(p, occ, verification={"consistency": 0.9})
    t = transcript([segment("s0001", "hello there", "advisor")])
    f = features(p.facts[occ], t, p, catalog=CATALOG)
    for name in (
        "self_score",
        "entail_score",
        "minicheck_score",
        "consistency",
        "grammar_readings",
        "grammar_top_margin",
        "second_pass_agreement",
        "distance_from_prior",
        "resolution_conf",
        "diarization_conf",
        "role_conf",
    ):
        assert math.isnan(f[name]), name
    assert f["consistency"] != f["consistency"]  # tier C is NaN even when a probe score exists
    assert f["asr_mean_conf"] == pytest.approx(0.93)  # no transcript words: the evidence's own mean
    assert f["second_pass_run"] == 0.0 and f["call_quality_capped"] == 0.0
    g = features(p.facts[nd], t, p, catalog=CATALOG)
    assert math.isnan(g["top_weight"]) and g["n_hypotheses"] == 0 and math.isnan(g["asr_mean_conf"])
    assert g["status_not_discussed"] == 1.0 and g["crm_link_score"] == 0.0  # the client entity has no CRM match


def test_reopened_by_coverage_reads_the_omission_check_record_for_an_updated_prior_field():
    """A prior field the omission check reopens is an UPDATE, which carries no ``reopened_by_coverage`` flag (§9.1d)."""
    b = ProfileBuilder()
    fid = b.seed("{p}.employer", "client", point("Eskom"))
    p = b.build()
    t = transcript([segment("s0001", "I work at Eskom")])
    assert features(p.facts[fid], t, p, catalog=CATALOG)["reopened_by_coverage"] == 0.0
    p = annotate(p, fid, verification={"coverage": {"reopened": True, "contradicted": False, "disagreements": 0, "segments": ["s0001"]}})
    assert not p.facts[fid].reopened_by_coverage and features(p.facts[fid], t, p, catalog=CATALOG)["reopened_by_coverage"] == 1.0


def test_storable_and_vector_round_trip():
    p, fid, t = _setup()
    f = features(p.facts[fid], t, p, catalog=CATALOG)
    stored = to_storable(f)
    assert all(v is None or isinstance(v, float) for v in stored.values())
    names = feature_names(CATALOG)
    v = vector(stored, names)
    assert v.shape == (len(names),) and math.isnan(v[names.index("resolution_conf")])
    annotated = annotate(p, fid, features=stored)
    assert annotated.facts[fid].model_validate_json(annotated.facts[fid].model_dump_json()).features == stored
