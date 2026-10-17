"""Segmentation (§5.2 steps 5 and 7): speaker-labelled words → ``Segment``s s0001….

Each speaker's words are merged into runs, split when the pause exceeds 1.2 s, when another speaker
takes a turn in the gap (without overlapping), or when the run would pass 30 s. A long run is split at the
best break before 30 s: a long pause or a sentence end, never between two number words. A segment is
``crosstalk`` when another speaker's speech overlaps it by more than 300 ms (words on stereo, or
overlapping diarization turns within 0.5 s of it on mono), ``interrupted`` when another speaker started
over it and it stopped within 1.5 s of that without a sentence end, and ``low_asr`` when its mean word
confidence is below 0.6.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Sequence
from dataclasses import dataclass

from ..models.transcript import Segment, Word
from .diarization import overlap_seconds
from .grammars.numbers import number_vocabulary
from .grammars.tokens import normalize_token
from .types import SpeakerTurn

PAUSE_SPLIT_S = 1.2
MAX_SEGMENT_S = 30.0
CROSSTALK_MIN_S = 0.3
LOW_ASR_CONF = 0.6
TURN_OVERLAP_PAD_S = 0.5  # mono: overlapped speech often yields words for one speaker only, just outside the other's segment
INTERRUPTED_TAIL_S = 1.5  # a segment that stops within this long of another speaker starting over it was cut off
_SENTENCE_END = (".", "?", "!")

_NUMBERS = number_vocabulary()
_DIGIT = re.compile(r"\d")


@dataclass
class LabeledWord:
    word: Word
    speaker: str
    diarization_conf: float = 1.0
    channel: int | None = None


def _numeric(w: Word) -> bool:
    t = normalize_token(w.raw_spoken or w.text)
    return bool(_DIGIT.search(t)) or any(x in _NUMBERS for x in t.split())


def _union(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def _intersection(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
    i = j = 0
    total = 0.0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi > lo:
            total += hi - lo
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return total


def _best_break(run: list[LabeledWord], start: int, limit_end: float) -> int:
    """Index of the first word of the next segment: the best break point that keeps the current one within the limit."""
    best, best_score = None, None
    for k in range(start + 1, len(run)):
        prev, nxt = run[k - 1].word, run[k].word
        if prev.t_end > limit_end:
            break
        score = nxt.t_start - prev.t_end
        if prev.text.rstrip().endswith((".", "?", "!")):
            score += 0.5
        elif prev.text.rstrip().endswith((",", ";", ":")):
            score += 0.2
        if _numeric(prev) and _numeric(nxt):
            score -= 1.0
        score += 0.001 * k  # prefer later breaks on ties, so segments stay long
        if best_score is None or score > best_score:
            best, best_score = k, score
    return best if best is not None else max(start + 1, min(len(run) - 1, start + 1))


def _split_long(run: list[LabeledWord], max_s: float) -> list[list[LabeledWord]]:
    out, start = [], 0
    while start < len(run):
        if run[-1].word.t_end - run[start].word.t_start <= max_s:
            out.append(run[start:])
            break
        cut = _best_break(run, start, run[start].word.t_start + max_s)
        out.append(run[start:cut])
        start = cut
    return out


def _runs_for_speaker(
    words: list[LabeledWord], others_starts: list[float], others: list[LabeledWord], pause_s: float
) -> list[list[LabeledWord]]:
    runs: list[list[LabeledWord]] = []
    for lw in words:
        if runs:
            prev = runs[-1][-1].word
            gap_start, gap_end = prev.t_end, lw.word.t_start
            split = gap_end - gap_start > pause_s
            if not split and gap_end > gap_start:
                k = bisect.bisect_left(others_starts, gap_start - 1e-6)
                while k < len(others) and others[k].word.t_start < gap_end:
                    o = others[k].word
                    if o.t_start >= gap_start - 1e-6 and o.t_end <= gap_end + 0.05:
                        split = True
                        break
                    k += 1
            if not split:
                runs[-1].append(lw)
                continue
        runs.append([lw])
    return runs


def segment_words(
    words: Sequence[LabeledWord],
    *,
    turns: Sequence[SpeakerTurn] = (),
    pause_s: float = PAUSE_SPLIT_S,
    max_s: float = MAX_SEGMENT_S,
    crosstalk_s: float = CROSSTALK_MIN_S,
    low_asr_conf: float = LOW_ASR_CONF,
) -> list[Segment]:
    """Speaker-labelled words → segments with ids s0001…, diarization confidence and quality flags."""
    ordered = sorted(words, key=lambda lw: (lw.word.t_start, lw.word.t_end))
    by_speaker: dict[str, list[LabeledWord]] = {}
    for lw in ordered:
        by_speaker.setdefault(lw.speaker, []).append(lw)
    runs: list[list[LabeledWord]] = []
    for spk, ws in by_speaker.items():
        others = [lw for lw in ordered if lw.speaker != spk]
        starts = [lw.word.t_start for lw in others]
        for run in _runs_for_speaker(ws, starts, others, pause_s):
            runs += _split_long(run, max_s)
    runs.sort(key=lambda r: (r[0].word.t_start, r[0].speaker))

    segments = []
    for k, run in enumerate(runs, start=1):
        ws = [lw.word for lw in run]
        segments.append(
            Segment(
                segment_id=f"s{k:04d}",
                speaker_label=run[0].speaker,
                diarization_conf=round(sum(lw.diarization_conf for lw in run) / len(run), 4),
                t_start=ws[0].t_start,
                t_end=ws[-1].t_end,
                words=ws,
                text=" ".join(w.text for w in ws),
            )
        )
    _flag(segments, turns, crosstalk_s, low_asr_conf)
    return segments


def _flag(segments: list[Segment], turns: Sequence[SpeakerTurn], crosstalk_s: float, low_asr_conf: float) -> None:
    speech = {s.segment_id: _union([(w.t_start, w.t_end) for w in s.words]) for s in segments}
    starts = [s.t_start for s in segments]
    longest = max((s.t_end - s.t_start for s in segments), default=0.0)
    for s in segments:
        if s.words and s.mean_conf < low_asr_conf:
            s.flags.add("low_asr")
        lo = bisect.bisect_left(starts, s.t_start - longest)
        hi = bisect.bisect_right(starts, s.t_end)
        others = [o for o in segments[lo:hi] if o.speaker_label != s.speaker_label and o.t_end > s.t_start and o.t_start < s.t_end]
        overlap = _intersection(speech[s.segment_id], _union([iv for o in others for iv in speech[o.segment_id]])) if others else 0.0
        if turns:
            overlap = max(overlap, overlap_seconds(turns, s.t_start - TURN_OVERLAP_PAD_S, s.t_end + TURN_OVERLAP_PAD_S))
        if overlap > crosstalk_s:
            s.flags.add("crosstalk")
        # Interrupted: another speaker started over this one and this one stopped soon after, mid-sentence.
        cut_in = [o for o in others if s.t_start < o.t_start < s.t_end and s.t_end - o.t_start <= INTERRUPTED_TAIL_S]
        if cut_in and s.words and not s.words[-1].text.rstrip().endswith(_SENTENCE_END):
            s.flags.add("interrupted")


def crosstalk_seconds(segments: Sequence[Segment]) -> float:
    """Total time where two speakers' words overlap."""
    by_speaker: dict[str, list[tuple[float, float]]] = {}
    for s in segments:
        by_speaker.setdefault(s.speaker_label, []).extend((w.t_start, w.t_end) for w in s.words)
    unions = [_union(v) for v in by_speaker.values()]
    events = sorted([(a, 1) for u in unions for a, _ in u] + [(b, -1) for u in unions for _, b in u], key=lambda e: (e[0], e[1]))
    total, depth, last = 0.0, 0, None
    for x, d in events:
        if depth >= 2 and last is not None:
            total += x - last
        depth += d
        last = x
    return total
