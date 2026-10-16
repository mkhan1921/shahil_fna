"""Synthetic calls (§15.1): determinism, gold consistency, coverage of the household space, and the synthetic guard."""

from __future__ import annotations

import json
import sys
from collections import Counter

import numpy as np
import pytest
from pydantic_ai import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from advisor_intel import stats
from advisor_intel.binding import get_path
from advisor_intel.evals import synthetic as syn
from advisor_intel.evals.datasets import LocalStore, fetch_for_calibration, sync_synthetic, synthetic_record
from advisor_intel.evals.gold import GoldCall
from advisor_intel.evals.spoken import read_amount, say_amount, words_af
from advisor_intel.models.runs import VersionPins
from advisor_intel.models.transcript import Segment, Word


@pytest.fixture(scope="module")
def calls():
    return syn.generate_calls(len(syn.ARCHETYPES) * 3, seed=21)


def test_generation_is_deterministic():
    a = syn.generate_call(4, seed=5)
    b = syn.generate_call(4, seed=5)
    assert a.model_dump_json() == b.model_dump_json()
    assert syn.generate_call(4, seed=6).model_dump_json() != a.model_dump_json()
    # a call does not depend on how many were generated before it
    assert syn.generate_calls(6, seed=5)[4].model_dump_json() == a.model_dump_json()


def test_every_gold_fact_is_grounded_in_a_script_turn(calls):
    for c in calls:
        segs = c.transcript().index()
        for f in c.gold.facts:
            if f.status == "not_discussed":
                assert not f.spans
                continue
            assert f.spans, f"{c.call_id}: {f.fact_id} ({f.status}) has no span"
            for s in f.spans:
                assert segs[s.segment_id].text[s.char_start : s.char_end] == s.quote, (c.call_id, f.fact_id)
                assert s.t_start is not None and s.t_end is not None and s.t_end >= s.t_start
            assert (f.value is not None) == (f.status in ("stated", "implied", "contradicted")), (c.call_id, f.fact_id)


def test_gold_document_holds_what_the_call_established(calls):
    for c in calls:
        doc = c.document.model_dump(mode="json")
        for f in c.gold.facts:
            if f.status not in ("stated", "implied") or not f.fna_path or f.value is None or f.value.kind != "numeric":
                continue
            if f.slot_id.endswith("dateOfBirth") or f.slot_id == "retirement.{p}.retirementAge":
                continue  # implied ages and dates keep the household's exact value, the gold p50 is the spoken reading
            raw = get_path(doc, f.fna_path)
            assert raw == pytest.approx(f.value.p50), (c.call_id, f.fact_id)


def test_households_cover_the_spec_perturbations(calls):
    docs = [c.household for c in calls]
    regimes = {d.household.maritalRegime for d in docs}
    assert {"icop", "anc-accrual", "na"} <= regimes
    assert any(d.client.employmentType == "retired" for d in docs)
    assert any(not d.household.includeSpouse for d in docs)
    rels = Counter(dep.relationship for d in docs for dep in d.dependants)
    assert rels["child"] and rels["parent"] and rels["sibling"]
    for lst in ("dependants", "retirementFunds", "assets", "liabilities", "policies", "goals"):
        assert any(getattr(d, lst) for d in docs), lst
    assert any(d.expenses.custom for d in docs)
    assert any(f.type == "living-annuity" for d in docs for f in d.retirementFunds)
    for d in docs:  # valid SA ID numbers agree with the date of birth
        assert d.client.idNumber[:6] == d.client.dateOfBirth[2:4] + d.client.dateOfBirth[5:7] + d.client.dateOfBirth[8:10]
        assert syn._luhn(d.client.idNumber[:-1]) == int(d.client.idNumber[-1])  # noqa: SLF001


def test_scripts_carry_the_phenomena_extraction_must_handle(calls):
    seen = Counter(p for c in calls for p in c.phenomena)
    for p in (
        "approx",
        "range",
        "hedge",
        "self_correction",
        "spouse_correction",
        "spouse_contradiction",
        "advisor_suggested",
        "advisor_confirmed",
        "implied",
        "explicitly_unknown",
        "slang",
        "mixed_language",
        "off_topic",
    ):
        assert seen[p], p
    statuses = Counter(f.status for c in calls for f in c.gold.facts)
    assert statuses["contradicted"] and statuses["implied"] and statuses["explicitly_unknown"] and statuses["not_discussed"]
    attributions = Counter(f.attribution for c in calls for f in c.gold.facts if f.status != "not_discussed")
    assert attributions["advisor_suggested"] and attributions["advisor_confirmed_by_client"] and attributions["mixed"]
    text = " ".join(t.text for c in calls for t in c.script)
    assert all(any(w in text for w in group) for group in ((" bar", "a bar"), (" mil",), (" grand", "k ", "k.")))
    assert "duisend" in text


def test_spoken_amounts_from_the_spec():
    assert say_amount(1000, "bar") == "a bar"
    assert say_amount(2_500_000, "mil") == "two point five mil"
    assert words_af(200_000) == "twee-honderd duisend"
    for v in (1000, 500, 85_000, 2_500_000, 200_000, 37_500):
        for style in ("digits", "words", "short_k", "bar", "grand", "mil", "afrikaans"):
            assert read_amount(say_amount(v, style)) == v


def test_entity_map_points_prior_rows_at_registry_ids(calls):
    with_prior = [c for c in calls if c.prior_document is not None and c.gold.entity_map]
    assert with_prior
    c = with_prior[0]
    from advisor_intel.binding import load_prior

    prof = load_prior(c.prior_document, call_id=c.call_id, run_id="x", anchor_date=c.anchor_date)
    for gid, rid in c.gold.entity_map.items():
        ge = next(e for e in c.gold.entities if e.entity_id == gid)
        assert prof.entities[rid].fna_id == ge.fna_id


def test_synthetic_guard(tmp_path, calls):
    c = calls[0]
    assert syn.is_synthetic(c) and syn.is_synthetic(c.gold) and syn.is_synthetic(synthetic_record(c))
    assert syn.is_synthetic({"input": {"call_id": "syn-1-0001"}})
    real = GoldCall(call_id="real-1", anchor_date=c.anchor_date)
    assert not syn.is_synthetic(real)
    syn.assert_not_synthetic([real])
    with pytest.raises(syn.SyntheticDataError, match="calibration"):
        syn.assert_not_synthetic([real, c.gold])

    @syn.no_synthetic("conformal threshold")
    def fit(rows, alpha=0.1):
        return len(rows)

    assert fit([real]) == 1
    with pytest.raises(syn.SyntheticDataError, match="conformal"):
        fit([c.gold])
    store = LocalStore(tmp_path)
    sync_synthetic(calls[:2], store=store)
    with pytest.raises(ValueError):
        fetch_for_calibration(store, "synthetic-calls")
    store.insert("gold-calls", [synthetic_record(calls[0]).model_copy(update={"id": "leaked"})])
    with pytest.raises(syn.SyntheticDataError):
        fetch_for_calibration(store, "gold-calls")


def test_llm_writer_keeps_every_keyed_phrase():
    rng = np.random.default_rng([3, 1])
    from advisor_intel.schema.slots import load_catalog

    plan = syn._Planner(syn.sample_household(rng, syn.DEFAULT_ANCHOR, "family", ns="t"), rng, syn.DEFAULT_ANCHOR, load_catalog())  # noqa: SLF001
    plan.run()
    attempts = []

    def model(messages, info: AgentInfo):
        attempts.append(len(messages))
        turns = []
        for b in plan.beats:
            for bt in b.turns:
                text = bt.text if bt.speaker != "advisor" else "Okay. " + bt.text
                turns.append({"speaker": bt.speaker if bt.speaker != "other" else "advisor", "text": text})
        if len(attempts) == 1:  # the first draft paraphrases one keyed phrase; the validator sends it back
            first = next(
                i
                for i, t in enumerate(turns)
                if any(p.keys for b in plan.beats for bt in b.turns for p in bt.pieces if p.text in t["text"])
            )
            turns[first]["text"] = "Something else entirely."
        return ModelResponse(parts=[TextPart(content=json.dumps({"turns": turns}))])

    agent = syn.script_writer_agent(VersionPins())  # test pins: never a provider
    with agent.override(model=FunctionModel(model)):
        script = syn.write_script_llm(plan.beats, agent=agent, brief="Household test")
    assert len(attempts) == 2
    spans, missing = syn.locate_phrases(script, plan.beats)
    assert not missing
    assert set(spans) == {k for b in plan.beats for bt in b.turns for p in bt.pieces for k in p.keys}
    assert any(t.text.startswith("Okay. ") for t in script)


def test_transcripts_come_from_ingest_with_the_scripts_roles(monkeypatch):
    """Stage 1's back half builds the transcript (scripted ASR words, gold roles, its own segmentation), and the gold
    spans are re-found on its segments; without the ingest package the one-segment-per-turn fallback is used."""
    c = syn.generate_call(2, seed=9)
    tr = c.transcript()
    assert tr.asr_provider != "script" and tr.numeric_spans and tr.call_id == c.call_id
    assert " ".join(s.text for s in tr.segments) == " ".join(t.text for t in c.script)  # segmentation may merge a speaker's turns
    assert {s.speaker_role for s in tr.segments} == {t.speaker for t in c.script}
    assert c.transcript() == tr  # deterministic: generation, datasets and the cache see the same transcript
    idx = tr.index()
    found = 0
    for f in c.gold.facts:
        for s in f.spans:
            assert idx[s.segment_id].text[s.char_start : s.char_end] == s.quote
            found += 1
    assert found > 20
    monkeypatch.setitem(sys.modules, "advisor_intel.ingest", None)  # not installed
    syn._ingested_json.cache_clear()
    try:
        fb = syn.script_transcript(c.script, c.anchor_date, c.call_id)
    finally:
        syn._ingested_json.cache_clear()
    assert fb.asr_provider == "script" and [s.text for s in fb.segments] == [t.text for t in c.script]
    assert isinstance(fb.segments[0], Segment) and isinstance(fb.segments[0].words[0], Word)


def test_render_audio_with_a_tts_provider():
    class FakeTTS:
        sample_rate = 8000

        def synthesize(self, text, *, voice, language="en-ZA"):
            n = 80 * len(text.split())
            return (np.sin(np.linspace(0, 50, n)) * 8000).astype("<i2").tobytes()

    turns = [syn.ScriptTurn("advisor", "Good morning"), syn.ScriptTurn("client_primary", "Hi there, thanks")]
    clean = syn.render_audio(turns, FakeTTS(), snr_db=None, gap_s=0.1)
    noisy = syn.render_audio(turns, FakeTTS(), snr_db=10, gap_s=0.1, seed=1)
    assert clean.sample_rate == 8000 and len(clean.turn_times) == 2
    assert clean.turn_times[0] == (0.0, 0.02) and clean.turn_times[1][0] == pytest.approx(0.12)
    a, b = np.frombuffer(clean.pcm, "<i2"), np.frombuffer(noisy.pcm, "<i2")
    assert len(a) == len(b) and not np.array_equal(a, b)


def test_implied_values_follow_the_derivation_rules(calls):
    for c in calls:
        for f in c.gold.facts:
            if f.slot_id == "{p}.dateOfBirth" and f.status == "implied":
                dob = getattr(c.household, f.instance_key).dateOfBirth
                assert f.value.p50 == pytest.approx(stats.iso_to_year(dob))
                assert f.value.p90 - f.value.p10 == pytest.approx(1.0)
