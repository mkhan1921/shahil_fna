"""Builders for realistic test data: a profile seeded from the FNA sample household plus call facts."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from advisor_intel import binding
from advisor_intel.extract.reducer import apply_state_ops, make_ops
from advisor_intel.models import fna as F
from advisor_intel.models.dists import Point, Quantity, Triangular
from advisor_intel.models.facts import Evidence, Hypothesis, Provenance
from advisor_intel.models.profile import BlockingFlag, FollowUp, Profile, StateOp
from advisor_intel.models.transcript import QualityReport, Segment, Transcript, Word

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ANCHOR = date(2026, 10, 15)
RET_AGE = "retirement.{p}.retirementAge#client"
GROSS = "income.{p}.grossMonthly#client"


def sample_document() -> F.FnaDocument:
    return F.FnaDocument.model_validate(json.loads((FIXTURES / "sample_fna.json").read_text()))


def evidence(seg: str = "s0002", quote: str = "I'd like to retire at sixty", role: str = "supports", t: float = 12.0) -> Evidence:
    return Evidence(
        segment_id=seg,
        char_start=0,
        char_end=len(quote),
        quote=quote,
        speaker_role="client_primary",
        t_start=t,
        t_end=t + 3,
        asr_mean_conf=0.93,
        role=role,  # type: ignore[arg-type]
    )


def seeded_profile(run_id: str = "call-1-run-a", call_id: str = "call-1", doc: F.FnaDocument | None = None) -> Profile:
    return binding.load_prior(doc or sample_document(), None, call_id=call_id, run_id=run_id, anchor_date=ANCHOR)


def with_call_facts(profile: Profile) -> Profile:
    """The client states a retirement age range and a gross income during the call."""
    prov = Provenance(run_id=profile.run_id, schema_version=profile.schema_version, lexicon_version="1.3", extractor_model="test")
    specs: list[tuple[str, str, str | None, dict[str, Any], str]] = [
        (
            "ADD",
            RET_AGE,
            "s0002",
            {
                "slot_id": "retirement.{p}.retirementAge",
                "instance_key": "client",
                "fna_path": "retirement.client.retirementAge",
                "status": "stated",
                "hypotheses": [
                    Hypothesis(
                        value=Quantity(dist=Triangular(low=60, mode=62, high=65), unit="age"), weight=1.0, rationale="between 60 and 65"
                    )
                ],
                "evidence": [evidence()],
                "entity_refs": ["person.client_primary"],
                "interpretation": "retire between 60 and 65, most likely 62",
                "provenance": prov,
            },
            "extractor",
        ),
        (
            "ADD",
            GROSS,
            "s0003",
            {
                "slot_id": "income.{p}.grossMonthly",
                "instance_key": "client",
                "fna_path": "income.client.grossMonthly",
                "status": "stated",
                "hypotheses": [
                    Hypothesis(
                        value=Quantity(dist=Point(value=85000), unit="ZAR", period="monthly", basis="gross"), weight=1.0, rationale="said"
                    )
                ],
                "evidence": [evidence("s0003", "I earn eighty-five thousand a month before tax", t=20.0)],
                "entity_refs": ["person.client_primary"],
                "provenance": prov,
            },
            "extractor",
        ),
    ]
    result = apply_state_ops(profile, make_ops(profile, specs))
    assert not result.rejected, result.rejected
    return result.profile


def flagged(profile: Profile, *, fact_id: str = RET_AGE) -> Profile:
    flag = BlockingFlag(
        flag_id="flag.tier_a.retirement_age",
        kind="tier_a_contradicted",
        fact_id=fact_id,
        message="retirement age contradicts the prior FNA",
    )
    question = FollowUp(
        followup_id="fu.retirement_age",
        fact_ids=[fact_id],
        question="Would you say 60, 63 or 65 is most realistic for retiring?",
        why="moves success odds by ~9 points",
        expected_pos_change=0.09,
        answer_type="AGE",
    )
    return profile.model_copy(update={"blocking_flags": [flag], "open_questions": [question]})


def make_transcript(call_id: str = "call-1", asr_model_version: str = "fake-1") -> Transcript:
    lines = [
        ("advisor", "Thanks for coming in, let's talk about retirement."),
        ("client_primary", "I'd like to retire at sixty, maybe sixty-five."),
        ("client_primary", "I earn eighty-five thousand a month before tax."),
    ]
    segs = []
    t = 0.0
    for i, (role, text) in enumerate(lines, start=1):
        words = [Word(text=w, t_start=t + j * 0.4, t_end=t + j * 0.4 + 0.3, conf=0.92) for j, w in enumerate(text.split())]
        segs.append(
            Segment(
                segment_id=f"s{i:04d}",
                speaker_label=f"spk_{0 if role == 'advisor' else 1}",
                speaker_role=role,  # type: ignore[arg-type]
                role_conf=0.95,
                t_start=t,
                t_end=words[-1].t_end,
                words=words,
                text=text,
            )
        )
        t = words[-1].t_end + 0.5
    quality = QualityReport(mean_word_conf=0.92, low_asr_share=0.0, crosstalk_share=0.0, n_speakers=2, role_conf_min=0.95)
    return Transcript(
        call_id=call_id, anchor_date=ANCHOR, segments=segs, asr_provider="fake", asr_model_version=asr_model_version, quality=quality
    )


def reviewer_accept(profile: Profile, fact_id: str, reviewer: str = "adv-1") -> list[StateOp]:
    return make_ops(
        profile,
        [
            (
                "CONFIRM",
                fact_id,
                None,
                {"review": {"status": "accepted", "reviewer_id": reviewer, "reviewed_at": datetime.now(UTC).isoformat()}},
                "reviewer",
            )
        ],
    )
