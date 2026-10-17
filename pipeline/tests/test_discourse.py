"""Stage 2: registry, references, rewrites, revisions and the topic index (§6)."""

from __future__ import annotations

from advisor_intel import llm
from advisor_intel.discourse import registry as R
from advisor_intel.discourse.revisions import pattern_revisions, restates
from advisor_intel.discourse.rewrite import check_rewrite
from advisor_intel.discourse.run import run_discourse, with_rewrites
from advisor_intel.discourse.text import hedges_in, numbers_in, render_segment
from advisor_intel.discourse.topics import embedding_scores, index_from_scores, segment_vectors
from advisor_intel.embeddings import HashedEmbedder
from advisor_intel.lexicon.loader import load_lexicon
from advisor_intel.models import DiscourseOutput, Entity, Revision
from advisor_intel.models.runs import VersionPins
from advisor_intel.schema.slots import load_catalog
from advisor_intel.testing.calls import SAMPLE_CALL, SAMPLE_GOLD, SAMPLE_OBJECTS
from advisor_intel.testing.fakes import scripted_model
from advisor_intel.testing.transcripts import transcript_from_turns

T = transcript_from_turns(SAMPLE_CALL)


def seg_with(text_part: str):
    return next(s for s in T.segments if text_part in s.text)


def turn(role: str, text: str):
    return transcript_from_turns([(role, text)]).segments[0]


def test_numbers_in_digits_and_words():
    assert {42000.0} <= numbers_in("about forty-two thousand a month")
    assert {1_200_000.0, 1.0, 2.0} <= numbers_in("one point two million")
    assert 2_000_000.0 not in numbers_in("one point two million")  # "point two" is a decimal, not another two million
    assert {250.0, 2.0} <= numbers_in("two fifty")
    assert {45000.0} <= numbers_in("R45 000") and {1500.0} <= numbers_in("1.5k")


def test_numbers_in_scale_words_fractions_and_articles():
    assert {2.5, 2_500_000.0} <= numbers_in("R2.5 million")
    assert 250_000.0 in numbers_in("250 thousand")
    assert 2_500_000.0 in numbers_in("two and a half million") and 2.5 in numbers_in("two and a half")
    assert 100_000.0 in numbers_in("a hundred thousand") and 150_000.0 in numbers_in("a hundred and fifty thousand")
    assert 1000.0 in numbers_in("a thousand a month")
    assert numbers_in("3 kids") == {3.0}  # "k" of "kids" is not a scale suffix
    assert numbers_in("") == set() and numbers_in("no numbers here") == set()


def test_hedges_in_uses_letter_boundaries():
    assert "maybe" in hedges_in("sixty-five, maybe?")
    assert "ish" in hedges_in("retire at 65ish") and "ish" in hedges_in("retire at 65-ish")
    assert "i think" in hedges_in("I  think so")
    assert not hedges_in("Spanish roundabout finish")
    assert "a bit" not in hedges_in("a bitter pill")
    assert "my hedge" in hedges_in("well, my hedge is this", extra=("my hedge",))


def test_rewrite_number_check_reads_scaled_digits():
    seg = turn("client_primary", "The pension is worth about one point two million.")
    assert check_rewrite(seg, "[Sipho's company pension] is worth about R1.2 million.", []).ok
    assert not check_rewrite(seg, "[Sipho's company pension] is worth about R1.2 billion.", []).ok
    # The digits match but the scale word was never said: a new number.
    seg2 = turn("client_primary", "Two hundred and fifty a month for the gym.")
    assert not check_rewrite(seg2, "[Sipho] pays 250 thousand a month for the gym.", []).ok
    assert check_rewrite(seg2, "[Sipho] pays R250 a month for the gym.", []).ok


def test_rewrite_check_uses_the_lexicon_hedges():
    lex = load_lexicon()
    seg = turn("client_primary", "I reckon the flat is worth two million.")
    dropped = check_rewrite(seg, "[The Sea Point flat] is worth two million.", [], lexicon=lex)
    assert not dropped.ok and "i reckon" in dropped.reason
    assert check_rewrite(seg, "I reckon [the Sea Point flat] is worth two million.", [], lexicon=lex).ok
    assert check_rewrite(seg, "[The Sea Point flat] is worth two million.", []).ok  # the built-in list alone misses it


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


def test_pattern_revisions_repeated_number_and_no_false_cues():
    t = transcript_from_turns(
        [
            ("client_primary", "I'd rather retire at sixty than sixty-five."),  # "rather" is not a correction
            ("client_primary", "The bond is about fifteen thousand a month."),
            ("client_secondary", "Groceries are eight thousand a month."),
            ("client_secondary", "Petrol is about three thousand a month."),  # a list of items, not a revision
            ("client_primary", "The bond is fourteen thousand a month, I checked."),  # the same subject, a different number
            ("client_primary", "Forty, sorry, I mean forty-five."),
        ]
    )
    revs = {(r.segment_id, r.revises_segment_id): r.trigger for r in pattern_revisions(t)}
    assert ("s0001", "s0001") not in revs and not any(k[0] == "s0001" for k in revs)
    assert not any(k[0] == "s0004" for k in revs)
    assert revs[("s0005", "s0002")] == "changed number"
    assert revs[("s0006", "s0005")].lower() == "sorry, i mean"
    assert restates(t.segments[1], t.segments[4]) and not restates(t.segments[2], t.segments[3])
    # A self-correction in the very first segment revises itself.
    first = pattern_revisions(transcript_from_turns([("client_primary", "Forty, sorry, I mean forty-five.")]))
    assert [(r.segment_id, r.revises_segment_id) for r in first] == [("s0001", "s0001")]


async def test_primary_client_mentioning_a_spouse_adds_one():
    t = transcript_from_turns(
        [("advisor", "Who else is in the household?"), ("client_primary", "My wife, but my partner at the firm is not.")]
    )
    reg = R.seed_registry(None, None)
    assert "person.spouse" not in reg.entities
    reg = await R.discover(reg, t, VersionPins(), use_llm=False)
    assert reg.entities["person.spouse"].first_mention == "s0002"
    assert "My wife" in [m.surface for m in reg.mentions if m.entity_id == "person.spouse"]
    assert "s0002" not in reg.ambiguous  # the reference is not ambiguous once the spouse exists
    # "my partner" alone may be a business partner: it resolves (pre-existing rule) but never creates the spouse.
    reg2 = await R.discover(
        R.seed_registry(None, None),
        transcript_from_turns([("client_primary", "My partner and I run the firm.")]),
        VersionPins(),
        use_llm=False,
    )
    assert "person.spouse" not in reg2.entities


def test_merge_candidates_ignore_pronoun_aliases():
    reg = R.Registry(
        entities={
            "person.thandi_1": Entity(
                entity_id="person.thandi_1",
                type="person",
                canonical_name="Thandi",
                aliases=["she", "her"],
                attributes={"relation": "daughter"},
            ),
            "person.mother_1": Entity(
                entity_id="person.mother_1",
                type="person",
                canonical_name="Mother",
                aliases=["she", "my mom"],
                attributes={"relation": "mother"},
            ),
            "policy.discovery_1": Entity(
                entity_id="policy.discovery_1", type="policy", canonical_name="Discovery life cover", aliases=["that policy"]
            ),
            "policy.discovery_life_2": Entity(entity_id="policy.discovery_life_2", type="policy", canonical_name="Discovery life"),
        }
    )
    assert R.merge_candidates(reg) == [("policy.discovery_1", "policy.discovery_life_2")]
    assert "she" not in reg.summary() and "my mom" in reg.summary()


def test_registry_merge_repoints_duplicates_and_low_confidence_window_merges_are_questions():
    reg = R.Registry(
        entities={
            "policy.a": Entity(entity_id="policy.a", type="policy", canonical_name="Discovery life", fna_list="policies", fna_id="row-1"),
            "policy.b": Entity(entity_id="policy.b", type="policy", canonical_name="Discovery cover"),
            "policy.c": Entity(entity_id="policy.c", type="policy", canonical_name="Sanlam life"),
        }
    )
    window = T.segments[:3]
    R.apply_window(
        reg, window, R.WindowOut(new_entities=[], mentions=[], merges=[R.MergeOut(keep_id="policy.a", merge_id="policy.b", confidence=0.5)])
    )
    assert set(reg.entities) == {"policy.a", "policy.b", "policy.c"} and reg.possible_duplicates == [("policy.a", "policy.b")]
    reg.possible_duplicates.append(("policy.b", "policy.c"))
    R.apply_window(
        reg, window, R.WindowOut(new_entities=[], mentions=[], merges=[R.MergeOut(keep_id="policy.a", merge_id="policy.b", confidence=0.9)])
    )
    assert "policy.b" not in reg.entities and reg.entities["policy.a"].fna_id == "row-1"
    assert reg.possible_duplicates == [("policy.a", "policy.c")]  # the settled pair is gone, the open one follows the kept id


def test_with_rewrites_sets_rewrites_and_self_correction_flags():
    fix = seg_with("Actually no, sorry")
    d = DiscourseOutput(
        entities={},
        rewrites={fix.segment_id: "[Sipho's take-home pay] is more like forty-five thousand now."},
        revisions=[Revision(segment_id=fix.segment_id, revises_segment_id="s0007", scope="income")],
    )
    out = with_rewrites(T, d)
    seg = out.segment(fix.segment_id)
    assert seg.text == fix.text and seg.rewritten_text.startswith("[Sipho") and "self_correction" in seg.flags
    assert all("self_correction" not in s.flags for s in out.segments if s.segment_id != fix.segment_id)
    assert all(s.flags == o.flags for s, o in zip(T.segments, T.segments))  # the input transcript is untouched
    assert with_rewrites(T, None) is T


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


def test_topic_embedding_pass_recall_on_the_sample_gold():
    """The embedding pass alone, with the hashed embedder, against the sample call's gold labels. The §6.5 target
    (recall ≥ 0.95) is for both passes with the production embedder on gold calls; this is the offline floor."""
    cat = load_catalog()
    gold: dict[str, set[str]] = {}
    for key, g in SAMPLE_GOLD.items():
        group = cat.slot(key.split("#")[0]).group
        for s in T.segments:
            if g["said"] in s.text:
                gold.setdefault(s.segment_id, set()).add(group)
    for o in SAMPLE_OBJECTS:
        for q in o["quotes"]:
            for s in T.segments:
                if q in s.text:
                    gold.setdefault(s.segment_id, set()).add(o["group"])
    emb = HashedEmbedder()
    idx = index_from_scores(embedding_scores(segment_vectors(T, {}, emb), cat.group_names(), emb), [s.segment_id for s in T.segments])
    labels = [(sid, g) for sid, gs in gold.items() for g in gs]
    hits = sum(sid in idx.get(g, []) for sid, g in labels)
    assert len(labels) >= 20
    assert hits / len(labels) >= 0.8, [(sid, g) for sid, g in labels if sid not in idx.get(g, [])]


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
                "new_entities": [
                    {
                        "ref": "new:thandi",
                        "type": "person",
                        "canonical_name": "Thandi",
                        "relation": "daughter",
                        "attributes": [{"key": "age", "value": "14"}],
                        "first_segment_id": thandi_seg.segment_id,
                    }
                ],
                "mentions": [
                    {
                        "segment_id": thandi_seg.segment_id,
                        "surface": "Thandi",
                        "char_start": start,
                        "char_end": start + 6,
                        "entity_id": "new:thandi",
                        "confidence": 0.95,
                    },
                    {
                        "segment_id": thandi_seg.segment_id,
                        "surface": "nonexistent words",
                        "char_start": 0,
                        "char_end": 3,
                        "entity_id": "person.ghost",
                        "confidence": 0.9,
                    },
                ],
                "merges": [],
            }
        return {"new_entities": [], "mentions": [], "merges": []}

    she = seg_with("She might go overseas")

    def small(prompt: str, schema: dict, info):
        props = schema.get("properties", {})
        if "rewrites" in props:
            return {
                "rewrites": [
                    {
                        "segment_id": she.segment_id,
                        "rewritten": "Thandi wants to do medicine. [Thandi] might go overseas to study, maybe the UK.",
                        "source_segment_ids": [],
                    }
                ]
            }
        if "revisions" in props:
            return {"revisions": []}
        return {
            "segments": [
                {"segment_id": she.segment_id, "groups": [{"group": "events", "relevance": 0.8}, {"group": "education", "relevance": 0.9}]}
            ]
        }

    pins = VersionPins(
        frontier_model=llm.register_local_model("disc-frontier", scripted_model(frontier)),
        small_model=llm.register_local_model("disc-small", scripted_model(small)),
    )
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
