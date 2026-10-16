"""Answer intake (spec §10.6).

When the advisor records a client's answer, it becomes a one-segment transcript attributed to
``client_primary`` (``asr_provider="followup"``, segment id ``fu-<followup id>``), which the same value
extraction reads through the injected ``extract`` callable — in production
``extract.followup.extract_followup_answer`` with the run's catalog, pins and run id bound. Its ops carry
``source="followup"``; they go through the reducer, the follow-up is marked answered, and the answer
segment is appended to the call transcript so its evidence quotes resolve in the review overlay. The
workflow then re-runs the forecast.
"""

from __future__ import annotations

import inspect
from datetime import date
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

from ..extract.reducer import RejectedOp, apply_state_ops
from ..models.profile import FollowUp, Profile, StateOp
from ..models.runs import FollowupAnswer
from ..models.transcript import Segment, Transcript, Word

SPEAKER_ROLE = "client_primary"
WORD_SECONDS = 0.35

# extract(profile, followup, one_segment_transcript) → ops, or (transcript, ops) as extract_followup_answer returns
ExtractAnswer = Callable[[Profile, FollowUp, Transcript], "Awaitable[Any] | Any"]


def answer_segment_id(followup_id: str) -> str:
    return f"fu-{followup_id}"


def answer_transcript(answer: FollowupAnswer, followup: FollowUp, *, call_id: str, anchor_date: date, t0: float = 0.0) -> Transcript:
    """The recorded answer as a one-segment transcript attributed to the primary client."""
    toks = answer.text.split()
    words = [Word(text=w, t_start=t0 + i * WORD_SECONDS, t_end=t0 + (i + 1) * WORD_SECONDS, conf=1.0) for i, w in enumerate(toks)]
    seg = Segment(
        segment_id=answer_segment_id(followup.followup_id),
        speaker_label="followup",
        speaker_role=SPEAKER_ROLE,
        role_conf=1.0,
        diarization_conf=1.0,
        t_start=t0,
        t_end=t0 + WORD_SECONDS * max(1, len(toks)),
        words=words,
        text=answer.text,
    )
    return Transcript(
        call_id=call_id,
        anchor_date=anchor_date,
        segments=[seg],
        asr_provider="followup",
        asr_model_version=answer.source,
        asr_alternatives=False,
    )


def append_answer(call: Transcript, answer: Transcript) -> Transcript:
    """The call transcript with the answer segment after the call's end (replacing an earlier answer to the same follow-up)."""
    offset = call.duration + 1.0
    new = []
    for seg in answer.segments:
        words = [w.model_copy(update={"t_start": w.t_start + offset, "t_end": w.t_end + offset}) for w in seg.words]
        new.append(seg.model_copy(update={"t_start": seg.t_start + offset, "t_end": seg.t_end + offset, "words": words}))
    ids = {s.segment_id for s in new}
    return call.model_copy(update={"segments": [s for s in call.segments if s.segment_id not in ids] + new})


class IntakeResult(BaseModel):
    profile: Profile
    transcript: Transcript  # the one-segment answer transcript
    followup: FollowUp
    ops: list[StateOp] = []  # applied
    rejected: list[RejectedOp] = []


def _renumber(profile: Profile, ops: list[StateOp]) -> list[StateOp]:
    seq = profile.next_seq()
    out = []
    for i, op in enumerate(ops):
        payload = dict(op.payload)
        if op.op in ("ADD", "UPDATE"):
            payload.setdefault("source", "followup")
        out.append(op.model_copy(update={"seq": seq + i, "payload": payload}))
    return out


async def intake_answer(
    profile: Profile, answer: FollowupAnswer, *, extract: ExtractAnswer, followup: FollowUp | None = None
) -> IntakeResult:
    """Apply one recorded answer: extract it, run the ops through the reducer, mark the follow-up answered."""
    fu = followup or next((q for q in profile.open_questions if q.followup_id == answer.followup_id), None)
    if fu is None:
        raise KeyError(f"no follow-up {answer.followup_id} on this profile")
    t = answer_transcript(answer, fu, call_id=profile.call_id, anchor_date=profile.anchor_date)
    out = extract(profile, fu, t)
    if inspect.isawaitable(out):
        out = await out
    ops = list(out[1] if isinstance(out, tuple) else out)
    r = apply_state_ops(profile, _renumber(profile, ops))
    answered = fu.model_copy(update={"status": "answered"})
    questions = [answered if q.followup_id == fu.followup_id else q for q in r.profile.open_questions]
    if not any(q.followup_id == fu.followup_id for q in questions):
        questions.append(answered)
    prof = r.profile.model_copy(update={"open_questions": questions})
    return IntakeResult(profile=prof, transcript=t, followup=answered, ops=r.applied, rejected=r.rejected)
