"""The meeting note rendered from reviewed facts, validated sentence by sentence (spec §11.6)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic_ai import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from advisor_intel.extract.reducer import apply_state_ops, make_ops
from advisor_intel.models.dists import Point, Quantity
from advisor_intel.models.facts import Hypothesis
from advisor_intel.models.runs import VersionPins
from advisor_intel.note import NoteSentence, apply_note, changed_facts, check_sentence, generate_note, note_facts, update_note, validate

from .factories import GROSS, RET_AGE, sample_document, seeded_profile, with_call_facts

PINS = VersionPins()


def _profile():
    p = with_call_facts(seeded_profile())
    # The client's objectives in their own words: an engagement text fact.
    from advisor_intel.models.facts import Provenance

    from .factories import evidence

    specs = [
        (
            "ADD",
            "engagement.clientObjectives",
            "s0004",
            {
                "slot_id": "engagement.clientObjectives",
                "fna_path": "engagement.clientObjectives",
                "status": "stated",
                "hypotheses": [Hypothesis(value=Quantity(dist=Point(value="Retire comfortably and pay for Neo's studies"), unit="none"), weight=1.0, rationale="quoted")],
                "evidence": [evidence("s0004", "we want to retire comfortably and pay for Neo's studies", t=40.0)],
                "provenance": Provenance(run_id=p.run_id, schema_version=p.schema_version),
            },
            "extractor",
        )
    ]
    r = apply_state_ops(p, make_ops(p, specs))
    assert not r.rejected, r.rejected
    return r.profile


def _neo(p) -> str:
    return next(e for e, ent in p.entities.items() if ent.canonical_name == "Neo Mokoena")


def _model(sentences: list[dict], seen: list[str] | None = None) -> FunctionModel:
    def fn(messages, info: AgentInfo) -> ModelResponse:
        if seen is not None:
            seen.append(json.dumps([m.model_dump(mode="json") if hasattr(m, "model_dump") else str(m) for m in messages], default=str))
        return ModelResponse(parts=[TextPart(json.dumps({"response": sentences}))])

    return FunctionModel(fn)


def test_validation_keeps_grounded_sentences_and_removes_the_rest():
    p = _profile()
    neo = _neo(p)
    neo_fact = next(fid for fid, f in p.facts.items() if f.instance_key == neo and f.slot_id == "dependants[].tertiaryStartAge")
    rejected = apply_state_ops(p, make_ops(p, [("RETRACT", "{p}.occupation#client", None, {"reason": "misheard"}, "reviewer")])).profile
    cases = [
        (NoteSentence(section="objectives", text="Thabo wants to retire between 60 and 65, most likely at 62.", fact_ids=[RET_AGE]), None),
        (NoteSentence(section="summary", text="The client now earns R85,000 a month, down from R100,000.", fact_ids=[GROSS]), None),
        (NoteSentence(section="summary", text="That is about R1 020 000 a year before tax.", fact_ids=[GROSS]), None),
        (NoteSentence(section="summary", text="The client earns R95,000 a month.", fact_ids=[GROSS]), "number_mismatch"),
        (NoteSentence(section="summary", text="The client plans to travel.", fact_ids=[]), "no_facts"),
        (NoteSentence(section="summary", text="The client works in mining.", fact_ids=["no.such#fact"]), "unknown_fact"),
        (NoteSentence(section="summary", text="Neo wants to retire at 62.", fact_ids=[RET_AGE]), "entity_mismatch"),
        (NoteSentence(section="summary", text="Neo will start tertiary studies.", fact_ids=[neo_fact]), None),
        (NoteSentence(section="summary", text="One of the client's goals is a comfortable retirement at 62.", fact_ids=[RET_AGE]), None),
    ]
    for sentence, expected in cases:
        why = check_sentence(sentence, p)
        assert (why[0] if why else None) == expected, (sentence.text, why)
    assert check_sentence(NoteSentence(section="summary", text="The client is an engineer.", fact_ids=["{p}.occupation#client"]), rejected)[0] == "rejected_fact"
    kept, removed = validate([s for s, _ in cases], p)
    assert len(kept) == 5 and {r.reason for r in removed} == {"number_mismatch", "no_facts", "unknown_fact", "entity_mismatch"}


async def test_generate_with_a_model_validates_its_sentences():
    p = _profile()
    model = _model(
        [
            {"section": "reason_for_advice", "text": "Thabo came in to plan for retirement.", "fact_ids": [RET_AGE]},
            {"section": "objectives", "text": "Retire comfortably and pay for Neo's studies.", "fact_ids": ["engagement.clientObjectives"]},
            {"section": "summary", "text": "He hopes to retire at 62, between 60 and 65.", "fact_ids": [RET_AGE]},
            {"section": "summary", "text": "He earns R90,000 a month.", "fact_ids": [GROSS]},
            {"section": "concerns", "text": "He worries about the markets.", "fact_ids": []},
        ]
    )
    note = await generate_note(p, pins=PINS, model=model)
    assert [s.text for s in note.sentences] == [
        "Thabo came in to plan for retirement.",
        "Retire comfortably and pay for Neo's studies.",
        "He hopes to retire at 62, between 60 and 65.",
    ]
    assert sorted(r.reason for r in note.removed) == ["no_facts", "number_mismatch"]
    fields = note.narrative_fields()
    assert fields["engagement.clientObjectives"] == "Retire comfortably and pay for Neo's studies."
    assert fields["advice.summary"] == "He hopes to retire at 62, between 60 and 65." and "engagement.clientConcerns" not in fields
    assert set(note.fact_hashes) == {RET_AGE, "engagement.clientObjectives"}


async def test_offline_template_note_is_fully_grounded():
    p = _profile()
    note = await generate_note(p, pins=PINS)  # "test" pins: the deterministic template
    assert note.model == "template" and not note.removed
    cited = {fid for s in note.sentences for fid in s.fact_ids}
    assert cited == {f.fact_id for f in note_facts(p)}
    assert note.narrative_fields()["engagement.clientObjectives"].startswith("Retire comfortably")


async def test_editing_a_fact_regenerates_only_the_sentences_citing_it():
    p = _profile()
    first = await generate_note(
        p,
        pins=PINS,
        model=_model(
            [
                {"section": "summary", "text": "Thabo plans to retire at 62.", "fact_ids": [RET_AGE]},
                {"section": "summary", "text": "He earns R85,000 a month.", "fact_ids": [GROSS]},
                {"section": "objectives", "text": "Retire comfortably and pay for Neo's studies.", "fact_ids": ["engagement.clientObjectives"]},
            ]
        ),
    )
    # The reviewer edits the income to R90,000 a month.
    edit = {
        "replace_hypotheses": [Hypothesis(value=Quantity(dist=Point(value=90000), unit="ZAR", period="monthly", basis="gross"), weight=1.0, rationale="adviser")],
        "review": {"status": "edited", "reviewer_id": "adv-1", "reviewed_at": datetime.now(timezone.utc).isoformat()},
    }
    p2 = apply_state_ops(p, make_ops(p, [("UPDATE", GROSS, None, edit, "reviewer")])).profile
    assert changed_facts(first, p2) == {GROSS}
    prompts: list[str] = []
    updated = await update_note(first, p2, pins=PINS, model=_model([{"section": "summary", "text": "He earns R90,000 a month.", "fact_ids": [GROSS]}], prompts))
    assert [s.text for s in updated.sentences] == ["Thabo plans to retire at 62.", "He earns R90,000 a month.", "Retire comfortably and pay for Neo's studies."]
    assert updated.profile_version == p2.version
    # Only the edited fact went to the model.
    assert GROSS in prompts[0] and RET_AGE not in prompts[0]
    # Nothing edited: nothing regenerated, no model call.
    again = await update_note(updated, p2, pins=PINS, model=_model([], prompts))
    assert again.sentences == updated.sentences and len(prompts) == 1


async def test_note_writes_the_narrative_fields_into_the_document():
    p = _profile()
    note = await generate_note(p, pins=PINS)
    doc = apply_note(sample_document(), note)
    assert doc.engagement.clientObjectives.startswith("Retire comfortably")
    assert doc.advice.summary and "62" in doc.advice.summary
    assert doc.engagement.reasonForAdvice == sample_document().engagement.reasonForAdvice  # no such sentences: left as is
