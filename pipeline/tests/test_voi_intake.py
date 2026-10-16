"""Answer intake (§10.6)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from advisor_intel.extract.reducer import check_profile, make_ops
from advisor_intel.models.dists import Triangular
from advisor_intel.models.facts import Hypothesis
from advisor_intel.models.profile import FollowUp
from advisor_intel.models.runs import FollowupAnswer
from advisor_intel.voi.intake import answer_segment_id, answer_transcript, append_answer, intake_answer

from .verify_factories import ProfileBuilder, ev, point, qty, segment, transcript

AT = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def _setup():
    b = ProfileBuilder()
    fid = b.add("retirement.{p}.retirementAge", "client", qty(Triangular(low=58, mode=62, high=66), "age"))
    fu = FollowUp(
        followup_id="fu_abc",
        fact_ids=[fid],
        question="Would you say 60, 63 or 65?",
        why="moves success odds by ~9 points",
        expected_pos_change=0.09,
        answer_type="AGE",
    )
    p = b.build()
    return p.model_copy(update={"open_questions": [fu]}), fid, fu


def test_answer_becomes_a_one_segment_client_transcript():
    p, _fid, fu = _setup()
    ans = FollowupAnswer(call_id="c1", followup_id="fu_abc", text="Probably sixty three", answered_at=AT)
    t = answer_transcript(ans, fu, call_id="c1", anchor_date=p.anchor_date)
    assert len(t.segments) == 1 and t.asr_provider == "followup" and t.asr_model_version == "followup"
    seg = t.segments[0]
    assert seg.segment_id == answer_segment_id("fu_abc") == "fu-fu_abc"
    assert seg.speaker_role == "client_primary" and seg.role_conf == 1.0 and seg.text == "Probably sixty three"
    assert [w.text for w in seg.words] == ["Probably", "sixty", "three"] and all(w.conf == 1.0 for w in seg.words)
    call = transcript([segment("s0001", "hello there", t0=0), segment("s0002", "bye now", t0=100)])
    merged = append_answer(call, t)
    assert merged.segments[-1].segment_id == "fu-fu_abc" and merged.segments[-1].t_start > call.duration
    assert len(append_answer(merged, t).segments) == 3  # answering again replaces the earlier answer segment


async def test_intake_applies_the_extracted_ops_and_marks_the_followup_answered():
    p, fid, _fu = _setup()
    seen = {}

    async def extract(profile, followup, t):
        seen["segment"] = t.segments[0].segment_id
        seen["followup"] = followup.followup_id
        payload = {
            "replace_hypotheses": [Hypothesis(value=point(63, "age"), weight=1, rationale="answered")],
            "status": "stated",
            "evidence": [ev(t.segments[0].segment_id, "sixty three", start=9)],
        }
        return t, make_ops(profile, [("UPDATE", fid, t.segments[0].segment_id, payload, "extractor")])  # as extract_followup_answer returns

    ans = FollowupAnswer(call_id="c1", followup_id="fu_abc", text="Probably sixty three", answered_at=AT)
    res = await intake_answer(p, ans, extract=extract)
    assert seen == {"segment": "fu-fu_abc", "followup": "fu_abc"}
    f = res.profile.facts[fid]
    assert f.value.dist.value == 63 and f.source == "followup"  # ops are sourced to the follow-up
    assert f.evidence[-1].segment_id == "fu-fu_abc" and f.evidence[-1].role == "revises"
    assert res.followup.status == "answered" and res.profile.open_questions[0].status == "answered"
    assert len(res.ops) == 1 and not res.rejected
    assert check_profile(res.profile) == []


async def test_answers_are_re_sourced_as_followup_whatever_the_extraction_wrote():
    p, fid, _fu = _setup()

    async def extract(profile, followup, t):
        payload = {
            "replace_hypotheses": [Hypothesis(value=point(63, "age"), weight=1, rationale="answered")],
            "status": "stated",
            "source": "call",  # §10.6: the answer's ops carry source="followup" regardless
            "evidence": [ev(t.segments[0].segment_id, "sixty three", start=9)],
        }
        return make_ops(profile, [("UPDATE", fid, t.segments[0].segment_id, payload, "extractor")])

    res = await intake_answer(p, FollowupAnswer(call_id="c1", followup_id="fu_abc", text="Probably sixty three", answered_at=AT), extract=extract)
    assert res.profile.facts[fid].source == "followup" and res.ops[0].payload["source"] == "followup"


async def test_unknown_followups_are_refused():
    p, _, _ = _setup()
    with pytest.raises(KeyError):
        await intake_answer(p, FollowupAnswer(call_id="c1", followup_id="nope", text="x", answered_at=AT), extract=lambda *a: [])
