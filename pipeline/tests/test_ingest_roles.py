"""Role assignment (§5.2 step 6): heuristic, CRM names, the Pydantic AI agent with TestModel/FunctionModel, reconciliation."""

from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.profiles import ModelProfile

from advisor_intel.ingest import roles as R
from advisor_intel.ingest.run import transcript_from_script_async
from advisor_intel.ingest.types import CrmNames
from advisor_intel.models.runs import VersionPins

SCRIPT = """
advisor: Good morning Thabo, good morning Lerato, thanks for making the time. This meeting is recorded for compliance.
advisor: Today I'd like to update your needs analysis. Let's start with income. What do you earn each month?
thabo: I take home about forty two thousand a month.
advisor: Thanks Thabo. And Lerato, what about your salary?
lerato: I earn about twenty eight thousand, and my employer pays the medical aid.
advisor: Do you have any retirement annuities?
thabo: Yes, my RA is with Allan Gray, I put in two thousand a month.
advisor: And how much cover do you have on your life policy?
lerato: We have about two million on Thabo and one million on me.
advisor: When would you like to retire?
thabo: At sixty, maybe sixty two.
"""
ROLES = {"advisor": "advisor", "thabo": "client_primary", "lerato": "client_secondary"}
CRM = CrmNames(advisor=["Sipho Dlamini"], client_primary=["Thabo Mokoena"], client_secondary=["Lerato Mokoena"])


async def segments(crm: CrmNames | None = None):
    t = await transcript_from_script_async(SCRIPT, date(2026, 10, 16), roles="heuristic", crm_names=crm)
    return t.segments  # labels: advisor=spk_0, thabo=spk_1, lerato=spk_2


async def test_heuristic_finds_the_adviser_without_names():
    segs = await segments()
    h = R.heuristic_roles(segs)
    assert h.roles["spk_0"] == "advisor" and h.confidence["spk_0"] >= 0.8
    assert {h.roles["spk_1"], h.roles["spk_2"]} == {"client_primary", "client_secondary"}
    assert all(h.confidence[k] < 0.8 for k in ("spk_1", "spk_2"))  # without names the clients need a click to confirm
    assert R.role_review_needed(h)


async def test_crm_names_decide_primary_and_secondary():
    segs = await segments(CRM)
    feats = R.speaker_features(segs, CRM)
    assert feats["spk_1"].addressed_as.get("client_primary") and feats["spk_2"].addressed_as.get("client_secondary")
    h = R.heuristic_roles(segs, CRM)
    assert h.roles == {"spk_0": "advisor", "spk_1": "client_primary", "spk_2": "client_secondary"}
    assert min(h.confidence.values()) >= 0.8 and not R.role_review_needed(h)


async def test_test_model_pin_uses_the_heuristic_without_calling_a_model():
    segs = await segments(CRM)

    def boom(messages, info):  # pragma: no cover - must not be called
        raise AssertionError("the role agent ran on a test pin")

    with R.role_agent.override(model=FunctionModel(boom)):
        out = await R.assign_roles(segs, pins=VersionPins(), crm=CRM)
    assert out == R.heuristic_roles(segs, CRM)


async def test_agent_output_is_converted_and_reconciled():
    segs = await segments(CRM)
    captured = {}

    def model(messages, info: AgentInfo) -> ModelResponse:
        captured["prompt"] = "\n".join(str(p.content) for m in messages for p in m.parts if hasattr(p, "content"))
        assert info.model_request_parameters.output_mode == "native"
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {
                            "speakers": [
                                {
                                    "speaker_label": "spk_0",
                                    "role": "advisor",
                                    "confidence": 0.98,
                                    "evidence": "s0001 greets, compliance statement",
                                },
                                {
                                    "speaker_label": "spk_1",
                                    "role": "client_primary",
                                    "confidence": 0.95,
                                    "evidence": "addressed as Thabo in s0004",
                                },
                                {
                                    "speaker_label": "spk_2",
                                    "role": "client_primary",
                                    "confidence": 0.7,
                                    "evidence": "speaks about her salary",
                                },
                                {"speaker_label": "spk_9", "role": "other", "confidence": 0.9, "evidence": "not a label in this call"},
                            ],
                            "rationale": "The adviser greets and asks the profile questions; Thabo is the CRM primary client.",
                        }
                    )
                )
            ]
        )

    with R.role_agent.override(model=FunctionModel(model)):
        out = await R.assign_roles(segs, pins=VersionPins(), crm=CRM, use_agent=True)
    assert out.roles == {"spk_0": "advisor", "spk_1": "client_primary", "spk_2": "client_secondary"}  # the duplicate is demoted
    assert out.confidence["spk_2"] == pytest.approx(0.5) and out.confidence["spk_0"] == pytest.approx(0.98)
    assert "spk_9" not in out.roles and "s0001 greets" in out.rationale
    prompt = captured["prompt"]
    assert "Thabo Mokoena" in prompt and "Heuristic guess" in prompt and "[s0001 | spk_0 | 00:01]" in prompt


async def test_missing_labels_and_disagreement_with_a_confident_heuristic():
    segs = await segments(CRM)
    tm = TestModel(
        profile=ModelProfile(supports_json_schema_output=True),
        custom_output_text=json.dumps(
            {
                "speakers": [{"speaker_label": "spk_0", "role": "client_primary", "confidence": 0.9, "evidence": "talks a lot"}],
                "rationale": "only one speaker judged",
            }
        ),
    )
    with R.role_agent.override(model=tm):
        out = await R.assign_roles(segs, pins=VersionPins(), crm=CRM, use_agent=True)
    h = R.heuristic_roles(segs, CRM)
    # spk_0 disagrees with a confident heuristic: held below 0.8 for review
    assert out.roles["spk_0"] == "client_primary" and out.confidence["spk_0"] < R.MIN_ROLE_CONF
    # missing labels fall back to the heuristic at reduced confidence; the clash on client_primary is resolved
    assert out.confidence["spk_2"] == pytest.approx(0.7 * h.confidence["spk_2"], abs=1e-3)
    assert sorted(out.roles.values()).count("client_primary") == 1
    assert R.role_review_needed(out)


async def test_invalid_agent_output_falls_back_to_a_capped_heuristic():
    segs = await segments(CRM)

    def garbage(messages, info):
        return ModelResponse(parts=[TextPart("not json at all")])

    with R.role_agent.override(model=FunctionModel(garbage)):
        out = await R.assign_roles(segs, pins=VersionPins(), crm=CRM, use_agent=True)
    assert out.roles == R.heuristic_roles(segs, CRM).roles
    assert max(out.confidence.values()) <= 0.6 and "agent failed" in out.rationale


async def test_context_is_first_five_minutes_plus_a_sample_and_roles_apply():
    segs = await segments()
    shifted = [s.model_copy(update={"t_start": s.t_start + 290 * (i > 3), "t_end": s.t_end + 290 * (i > 3)}) for i, s in enumerate(segs)]
    many = shifted + [
        shifted[-1].model_copy(update={"segment_id": f"s{100 + k}", "t_start": 600.0 + 10 * k, "t_end": 605.0 + 10 * k}) for k in range(60)
    ]
    ctx = R.select_context(many)
    assert len([s for s in ctx if s.t_start < R.FIRST_WINDOW_S]) == len([s for s in many if s.t_start < 300])
    assert len([s for s in ctx if s.t_start >= R.FIRST_WINDOW_S]) == R.LATER_SAMPLE
    applied = R.apply_roles(segs, R.heuristic_roles(segs))
    assert applied[0].speaker_role == "advisor" and applied[0].role_conf > 0.8
    assert all(s.speaker_role != "unknown" for s in applied)


def test_output_schema_is_strict_friendly():
    schema = R.RoleAssignmentOutput.model_json_schema()
    item = schema["$defs"]["SpeakerRoleItem"]
    assert set(item["required"]) == set(item["properties"]) and set(schema["required"]) == set(schema["properties"])
    assert "additionalProperties" not in json.dumps(schema)  # no dict-valued fields
