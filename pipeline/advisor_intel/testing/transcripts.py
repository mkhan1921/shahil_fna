"""Build transcripts from scripted turns for unit tests of the stages after ingestion.

Ingestion has its own scripted ASR with realistic noise (``advisor_intel.ingest``); this is the
minimal deterministic version: one segment per turn, words timed at 0.35 s each, confidence 0.95
unless a turn overrides it.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

from ..models.transcript import QualityReport, Segment, Transcript, Word

Turn = tuple[str, str] | tuple[str, str, float]  # (speaker_role, text[, confidence])

_LABEL = {"advisor": "spk_0", "client_primary": "spk_1", "client_secondary": "spk_2", "other": "spk_3"}


def transcript_from_turns(turns: Sequence[Turn], *, call_id: str = "call-test", anchor: date = date(2026, 10, 15), word_s: float = 0.35, gap_s: float = 0.6) -> Transcript:
    segs: list[Segment] = []
    t = 0.0
    for i, turn in enumerate(turns, start=1):
        role, text = turn[0], turn[1]
        conf = turn[2] if len(turn) > 2 else 0.95  # type: ignore[misc]
        words = []
        for w in text.split():
            words.append(Word(text=w, t_start=round(t, 2), t_end=round(t + word_s * 0.9, 2), conf=conf))
            t += word_s
        start = words[0].t_start if words else t
        segs.append(
            Segment(
                segment_id=f"s{i:04d}",
                speaker_label=_LABEL.get(role, "spk_9"),
                speaker_role=role,  # type: ignore[arg-type]
                role_conf=0.97,
                diarization_conf=0.95,
                t_start=start,
                t_end=round(t, 2),
                words=words,
                text=text,
                flags={"low_asr"} if conf < 0.6 else set(),
            )
        )
        t += gap_s
    roles = {s.speaker_label for s in segs}
    q = QualityReport(mean_word_conf=sum(s.mean_conf for s in segs) / max(1, len(segs)), low_asr_share=0.0, crosstalk_share=0.0, n_speakers=len(roles), role_conf_min=0.97)
    return Transcript(call_id=call_id, anchor_date=anchor, segments=segs, asr_provider="scripted", asr_model_version="scripted-1", quality=q)


async def ingested_transcript(turns: Sequence[Turn], *, call_id: str = "call-test", anchor: date = date(2026, 10, 15), seed: int = 0,
                              noise: object | None = None) -> Transcript:
    """The same turns through stage 1 (scripted ASR, segmentation, spans and grammar readings, quality), roles from the script."""
    from ..ingest import transcript_from_script_async
    from ..ingest.asr.fake import ScriptedCall

    script = ScriptedCall.parse("\n".join(f"{t[0]}: {t[1]}" for t in turns), roles={r: r for r in {t[0] for t in turns}})  # type: ignore[misc]
    return await transcript_from_script_async(script, anchor, call_id=call_id, seed=seed, noise=noise, roles="script")  # type: ignore[arg-type]
