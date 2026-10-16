"""Stage 2: registry, references, rewrites, revisions and the topic index (§6)."""

from __future__ import annotations

from datetime import date

from advisor_intel import llm
from advisor_intel.discourse import registry as R
from advisor_intel.discourse.revisions import pattern_revisions
from advisor_intel.discourse.rewrite import check_rewrite
from advisor_intel.discourse.run import run_discourse
from advisor_intel.discourse.text import numbers_in, render_segment
from advisor_intel.embeddings import HashedEmbedder
from advisor_intel.discourse.topics import embedding_scores, index_from_scores, segment_vectors
from advisor_intel.models import Entity
from advisor_intel.models.runs import VersionPins
from advisor_intel.testing.calls import SAMPLE_CALL
from advisor_intel.testing.fakes import scripted_model
from advisor_intel.testing.transcripts import transcript_from_turns

T = transcript_from_turns(SAMPLE_CALL)


def seg_with(text_part: str):
    return next(s for s in T.segments if text_part in s.text)


def test_numbers_in_digits_and_words():
    assert {42000.0} <= numbers_in("about forty-two thousand a month")
    assert {1_200_000.0} <= numbers_in("one point two million") or {1.0, 2.0} <= numbers_in("one point two million")
    assert {250.0, 2.0} <= numbers_in("two fifty")
    assert {45000.0} <= numbers_in("R45 000") and {1500.0} <= numbers_in("1.5k")


def test_pattern_revisions_find_self_correction():
    revs = pattern_revisions(T)
    fix = seg_with("Actually no, sorry")
    prior = seg_with("I take home about forty-two")
    assert any(r.segment_id == fix.segment_id and r.revises_segment_id == prior.segment_id for r in revs)


def test_speaker_relative_spouse():
    ents = {"person.spouse": Entity(entity_id="person.spouse", type="person", canonical_name="Lerato")}
    from advisor_intel.testing.transcripts import transcript_from_turns as tf

    t = tf([("client_primary", "My wife smokes, I don't."), ("client_secondary", "My husband is the one who smokes!")])
    m1 = R.speaker_relative_mentions(t.segments[0], ents)
    m2 = R.speaker_relative_mentions(t.segments[1], ents)
    assert m1[0].entity_id == "person.spouse" and m2[0].entity_id == "person.client_primary"


def test_rewrite_checks():
    seg = seg_with("Thandi wants to do medicine")
    ok = check_rewrite(seg, "[Lerato's daughter] Thandi wants to do medicine. [Thandi] might go overseas to study, maybe [in] the UK.", [])
    assert ok.ok, ok.reason
    assert not check_rewrite(seg, "Thandi wants medicine and might go overseas, maybe to the UK, for 6 years.", []).ok
    assert "hedges" in check_rewrite(seg, "Thandi wants to do medicine and will study overseas in the UK.", []).reason
    assert not check_rewrite(seg, seg.text * 4, []).ok


def test_topic_embedding_pass_tags_obvious_segments():
    emb = HashedEmbedder()
    vecs = segment_vectors(T, {}, emb)
    scores = embedding_scores(vecs, ["income", "dependants", "liabilities", "retirement"], emb)
    idx = index_from_scores(scores, [s.segment_id for s in T.segments])
    assert seg_with("I take home about forty-two").segment_id in idx.get("income", [])
    assert seg_with("The bond, we owe").segment_id in idx.get("liabilities", [])
    assert seg_with("I'd like to retire at sixty").segment_id in idx.get("retirement", [])


async def test_run_discourse_offline_is_deterministic():
    pins = VersionPins()
    a = await run_discourse(T, pins=pins)
    b = await run_discourse(T, pins=pins)
    assert a.model_dump() == b.model_dump()
    assert a.topic_index and a.segment_vectors and a.revisions
    assert "person.client_primary" in a.entities and "person.advisor" in a.entities


async def test_run_discourse_with_scripted_models():
    thandi_seg = seg_with("Thandi is fourteen")

    def frontier(prompt: str, schema: dict, info):
        if "Candidate pairs" in prompt:
            return {"decisions": []}
        if thandi_seg.segment_id in prompt:
            start = thandi_seg.text.index("Thandi")
            return {
                "new_entities": [{"ref": "new:thandi", "type": "person", "canonical_name": "Thandi", "relation": "daughter", "attributes": [{"key": "age", "value": "14"}], "first_segment_id": thandi_seg.segment_id}],
                "mentions": [{"segment_id": thandi_seg.segment_id, "surface": "Thandi", "char_start": start, "char_end": start + 6, "entity_id": "new:thandi", "confidence": 0.95},
                             {"segment_id": thandi_seg.segment_id, "surface": "nonexistent words", "char_start": 0, "char_end": 3, "entity_id": "person.ghost", "confidence": 0.9}],
                "merges": [],
            }
        return {"new_entities": [], "mentions": [], "merges": []}

    she = seg_with("She might go overseas")

    def small(prompt: str, schema: dict, info):
        props = schema.get("properties", {})
        if "rewrites" in props:
            return {"rewrites": [{"segment_id": she.segment_id, "rewritten": "Thandi wants to do medicine. [Thandi] might go overseas to study, maybe the UK.", "source_segment_ids": []}]}
        if "revisions" in props:
            return {"revisions": []}
        return {"segments": [{"segment_id": she.segment_id, "groups": [{"group": "events", "relevance": 0.8}, {"group": "education", "relevance": 0.9}]}]}

    pins = VersionPins(frontier_model=llm.register_local_model("disc-frontier", scripted_model(frontier)), small_model=llm.register_local_model("disc-small", scripted_model(small)))
    out = await run_discourse(T, pins=pins)
    thandi = [e for e in out.entities.values() if e.canonical_name == "Thandi"]
    assert len(thandi) == 1 and thandi[0].attributes["relation"] == "daughter"
    assert any(m.entity_id == thandi[0].entity_id for m in out.mentions)
    assert all(m.entity_id != "person.ghost" for m in out.mentions)
    assert out.rewrites.get(she.segment_id, "").startswith("Thandi")
    assert she.segment_id in out.topic_index["events"] and she.segment_id in out.topic_index["education"]
    assert out.usage["requests"] >= 3


def test_render_segment_marks_low_confidence():
    t = transcript_from_turns([("client_primary", "about two fifty a month", 0.4)])
    assert "{two?0.40}" in render_segment(t.segments[0])
