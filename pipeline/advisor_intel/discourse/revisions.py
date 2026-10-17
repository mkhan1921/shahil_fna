"""Self-correction and revision detection (spec §6.4).

A deterministic pass finds the patterns ("actually", "no wait", "sorry, I mean", "let me correct
that", a repeated number with a different value within three segments, a spouse correcting the
client), and the small model tags the rest. Revisions are never merged silently: extraction turns
them into UPDATE or RETRACT ops (§7.3).
"""

from __future__ import annotations

import re

from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput

from .. import llm
from ..models.entities import Revision
from ..models.runs import VersionPins
from ..models.transcript import Segment, Transcript
from ..prompts import pinned
from .text import _SCALES, _TENS, _UNITS, numbers_in, render_segment

LOOKBACK = 3
# Correction cues. Bare "rather" ("I'd rather retire at sixty") and bare "I mean" (a filler) are not corrections;
# "or rather", "sorry, I mean" and "no, I mean" are.
_CUES = re.compile(
    r"\b(actually|no wait|wait,? no|sorry,? i mean|no,? i mean|let me correct|correction|scratch that|or rather|"
    r"no,? it'?s more like|more like|not \w+,? (?:but|it'?s)|i was wrong|make that|eintlik|nee wag)\b",
    re.IGNORECASE,
)
_SPOUSE_CORRECTION = re.compile(r"^\s*(no|nee|um,? no|well,? no|actually)\b", re.IGNORECASE)
_CONTENT_WORD = re.compile(r"[a-z']+")
# Words that carry no topic when deciding whether a later segment restates an earlier one.
_STOP = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "so",
        "is",
        "are",
        "was",
        "were",
        "be",
        "it",
        "its",
        "it's",
        "that",
        "this",
        "we",
        "i",
        "you",
        "my",
        "our",
        "your",
        "he",
        "she",
        "they",
        "of",
        "to",
        "for",
        "in",
        "on",
        "at",
        "with",
        "per",
        "each",
        "now",
        "then",
        "about",
        "around",
        "roughly",
        "more",
        "less",
        "like",
        "bit",
        "ja",
        "yes",
        "no",
        "well",
        "um",
        "uh",
        "sorry",
        "think",
        "say",
        "month",
        "months",
        "year",
        "years",
        "week",
        "weeks",
        "day",
        "days",
        "rand",
        "r",
        "point",
        "half",
        "another",
        "still",
        "also",
        "just",
        "only",
        "maybe",
        "probably",
        "actually",
        "wait",
        "mean",
        "really",
        "quite",
        "very",
        "there",
        "here",
        "what",
        "have",
        "has",
        "had",
        "get",
        "got",
        "do",
        "does",
        "did",
        "not",
        "don't",
        "from",
        "up",
        "down",
        "out",
        "all",
        "some",
        "any",
    }
)


class RevisionOut(BaseModel):
    segment_id: str
    revises_segment_id: str
    scope: str
    trigger: str


class RevisionsOut(BaseModel):
    revisions: list[RevisionOut]


revision_agent: Agent[None, RevisionsOut] = Agent(None, output_type=NativeOutput(RevisionsOut), name="discourse_revisions")


def _client(seg: Segment) -> bool:
    return seg.speaker_role in ("client_primary", "client_secondary")


def _content_words(text: str) -> set[str]:
    return {
        w
        for w in _CONTENT_WORD.findall(text.lower())
        if w not in _STOP and w not in _UNITS and w not in _TENS and w not in _SCALES and len(w) > 1
    }


def restates(earlier: Segment, later: Segment) -> bool:
    """The later segment repeats the earlier one's subject with a different number (§6.4 "a repeated number
    with a different value"): at least one shared content word and half the content words in common, so a
    list of different items ("groceries eight thousand", "petrol three thousand") is not a revision."""
    a, b = _content_words(earlier.text), _content_words(later.text)
    if not a or not b or not (a & b):
        return False
    return len(a & b) / len(a | b) >= 0.5


def pattern_revisions(transcript: Transcript, topic_of: dict[str, str] | None = None) -> list[Revision]:
    """The deterministic pass: cue words, changed repeated numbers and spouse corrections."""
    segs = transcript.segments
    topic_of = topic_of or {}
    out: list[Revision] = []
    for i, seg in enumerate(segs):
        if not _client(seg):
            continue
        nums = numbers_in(seg.text)
        cue = _CUES.search(seg.text)
        spouse_fix = seg.speaker_role == "client_secondary" and _SPOUSE_CORRECTION.search(seg.text) and nums
        for j in range(i - 1, max(-1, i - LOOKBACK - 1), -1):
            prev = segs[j]
            if not _client(prev):
                continue
            prev_nums = numbers_in(prev.text)
            changed = bool(nums and prev_nums and not (nums & prev_nums))
            repeated = changed and prev.speaker_role == seg.speaker_role and restates(prev, seg)
            if (
                (cue and (prev_nums or j == i - 1))
                or (changed and (cue or spouse_fix))
                or (spouse_fix and prev.speaker_role == "client_primary" and prev_nums)
                or repeated
            ):
                trigger = cue.group(0) if cue else ("spouse correction" if spouse_fix else "changed number")
                out.append(
                    Revision(
                        segment_id=seg.segment_id,
                        revises_segment_id=prev.segment_id,
                        scope=topic_of.get(prev.segment_id, "unknown"),
                        trigger=trigger,
                    )
                )
                break
        if cue and len(nums) >= 2 and not any(r.segment_id == seg.segment_id for r in out):
            # A bare self-correction inside one turn ("forty, sorry, I mean forty-five") revises itself.
            out.append(
                Revision(
                    segment_id=seg.segment_id,
                    revises_segment_id=seg.segment_id,
                    scope=topic_of.get(seg.segment_id, "unknown"),
                    trigger=cue.group(0),
                )
            )
    return out


async def detect_revisions(
    transcript: Transcript, pins: VersionPins, *, use_llm: bool, groups: list[str], topic_of: dict[str, str] | None = None, window: int = 60
) -> tuple[list[Revision], dict[str, int]]:
    usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
    found = pattern_revisions(transcript, topic_of)
    if use_llm and transcript.segments:
        rm = llm.for_role("small", pins)
        prompt = pinned("discourse_revisions", pins.prompt_versions)
        ids = {s.segment_id: i for i, s in enumerate(transcript.segments)}
        for start in range(0, len(transcript.segments), window - 10):
            chunk = transcript.segments[start : start + window]
            text = f"Slot groups: {', '.join(groups)}\n\n" + "\n".join(render_segment(s) for s in chunk)
            result = await revision_agent.run(text, model=rm.model, model_settings=rm.settings, instructions=prompt.text)
            usage["requests"] += result.usage.requests
            usage["input_tokens"] += result.usage.input_tokens or 0
            usage["output_tokens"] += result.usage.output_tokens or 0
            for r in result.output.revisions:
                if r.segment_id in ids and r.revises_segment_id in ids and ids[r.revises_segment_id] <= ids[r.segment_id]:
                    scope = r.scope if r.scope in groups else topic_of.get(r.revises_segment_id, "unknown") if topic_of else "unknown"
                    found.append(
                        Revision(
                            segment_id=r.segment_id, revises_segment_id=r.revises_segment_id, scope=scope, trigger=r.trigger or "model"
                        )
                    )
            if start + window >= len(transcript.segments):
                break
    dedup: dict[tuple[str, str], Revision] = {}
    for r in found:
        dedup.setdefault((r.segment_id, r.revises_segment_id), r)
    return sorted(dedup.values(), key=lambda r: (r.segment_id, r.revises_segment_id)), usage
