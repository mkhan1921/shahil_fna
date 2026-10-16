"""The Stage 1 quality report and acceptance checks (§5.5).

| Check                              | Threshold      | Action if failed                                     |
| Mean word confidence               | ≥ 0.80         | continue, ``cap_p_correct``, banner                  |
| Low-ASR time                       | ≤ 15 % of speech | same                                               |
| Role confidence                    | ≥ 0.80 for every speaker | ``role_review_needed``: confirm roles before review |
| Speakers detected                  | 2–4            | ``failed``: route the run to manual                  |
| Numeric spans with scale ambiguity | logged         | resolved in Stage 4 (§7.4)                           |

A speaker counts as detected when they have at least ``MIN_SPEAKER_S`` of speech, so a stray diarization
label on a cough does not fail the run.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..models.transcript import NumericSpan, QualityReport, Segment, Transcript
from .segmentation import crosstalk_seconds

MIN_MEAN_CONF = 0.80
MAX_LOW_ASR_SHARE = 0.15
MIN_ROLE_CONF = 0.80
MIN_SPEAKERS, MAX_SPEAKERS = 2, 4
MIN_SPEAKER_S = 1.0


def speech_seconds(segments: Sequence[Segment]) -> float:
    return sum(max(0.0, s.t_end - s.t_start) for s in segments)


def detected_speakers(segments: Sequence[Segment], min_speech_s: float = MIN_SPEAKER_S) -> list[str]:
    totals: dict[str, float] = {}
    for s in segments:
        totals[s.speaker_label] = totals.get(s.speaker_label, 0.0) + max(0.0, s.t_end - s.t_start)
    return [spk for spk, t in totals.items() if t >= min_speech_s]


def quality_report(
    segments: Sequence[Segment],
    numeric_spans: Sequence[NumericSpan] = (),
    *,
    asr_alternatives: bool = True,
    role_conf: dict[str, float] | None = None,
) -> QualityReport:
    words = [w for s in segments for w in s.words]
    mean_conf = sum(w.conf for w in words) / len(words) if words else 0.0
    speech = speech_seconds(segments)
    low = sum(s.t_end - s.t_start for s in segments if "low_asr" in s.flags)
    low_share = low / speech if speech > 0 else 0.0
    cross_share = crosstalk_seconds(segments) / speech if speech > 0 else 0.0
    speakers = detected_speakers(segments)
    if role_conf is None:
        role_conf = {}
        for s in segments:
            role_conf[s.speaker_label] = min(role_conf.get(s.speaker_label, 1.0), s.role_conf)
    role_min = min((role_conf.get(spk, 0.0) for spk in speakers), default=0.0)
    ambiguous = sum(1 for n in numeric_spans if "scale_ambiguous" in n.flags)

    banners: list[str] = []
    cap = False
    if mean_conf < MIN_MEAN_CONF:
        cap = True
        banners.append(
            f"Low ASR confidence: mean word confidence {mean_conf:.2f} (target ≥ {MIN_MEAN_CONF:.2f}). Extraction confidence is capped."
        )
    if low_share > MAX_LOW_ASR_SHARE:
        cap = True
        banners.append(
            f"Poor audio: {low_share:.0%} of speech is low-confidence (target ≤ {MAX_LOW_ASR_SHARE:.0%}). Extraction confidence is capped."
        )
    role_review = role_min < MIN_ROLE_CONF
    if role_review:
        banners.append(f"Confirm speaker roles before review: lowest role confidence {role_min:.2f} (target ≥ {MIN_ROLE_CONF:.2f}).")
    failed = not (MIN_SPEAKERS <= len(speakers) <= MAX_SPEAKERS)
    if failed:
        banners.append(
            f"{len(speakers)} speaker(s) detected (expected {MIN_SPEAKERS}–{MAX_SPEAKERS}): this call is routed to manual processing."
        )
    if not asr_alternatives:
        banners.append("The ASR provider returned no word alternatives: amount readings rest on the 1-best transcript only.")
    unaccepted = [n for n in numeric_spans if not n.accepted]
    if unaccepted:
        rechecked = sum(1 for n in unaccepted if n.second_pass_method is not None)
        tail = (
            "were re-checked against the audio"
            if rechecked == len(unaccepted)
            else f"{rechecked} of them re-checked against the audio"
            if rechecked
            else "were not re-checked (no second pass configured)"
        )
        banners.append(f"{len(unaccepted)} number(s) could not be read cleanly; {tail}.")
    return QualityReport(
        mean_word_conf=round(mean_conf, 4),
        low_asr_share=round(low_share, 4),
        crosstalk_share=round(cross_share, 4),
        n_speakers=len(speakers),
        role_conf_min=round(role_min, 4),
        scale_ambiguous_spans=ambiguous,
        banners=banners,
        role_review_needed=role_review,
        failed=failed,
        cap_p_correct=cap,
    )


def report_for(transcript: Transcript, role_conf: dict[str, float] | None = None) -> QualityReport:
    return quality_report(transcript.segments, transcript.numeric_spans, asr_alternatives=transcript.asr_alternatives, role_conf=role_conf)
