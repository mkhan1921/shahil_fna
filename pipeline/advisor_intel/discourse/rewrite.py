"""Contextual rewriting (spec §6.3).

Segments with a resolved reference, an ellipsis or a fragment get a self-contained rewrite from the
small model, batched 20 per call with 5 preceding segments as context. The original text is never
overwritten. A rewrite is kept only if it passes three checks: no new numbers (every number must
occur in the segment or in an earlier segment it names as a source), no hedge removed, and at most
three times the original length.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from pydantic_ai import Agent, NativeOutput

from .. import llm
from ..models.entities import Mention
from ..models.runs import VersionPins
from ..models.transcript import Segment, Transcript
from ..prompts import pinned
from .text import hedges_in, numbers_in, render_segment

if TYPE_CHECKING:
    from ..lexicon.loader import Lexicon

BATCH = 20
CONTEXT = 5
MAX_RATIO = 3.0
_PRONOUNS = re.compile(r"\b(he|she|they|him|her|them|his|hers|their|it|its|that|this|those|these|there)\b", re.IGNORECASE)
_BACKCHANNEL = re.compile(
    r"^\s*(ja|yes|yeah|yep|no|okay|ok|mm+|uh[- ]huh|right|sure|great|thanks|thank you|shame|eish)[.!?,\s]*$", re.IGNORECASE
)


class RewriteOut(BaseModel):
    segment_id: str
    rewritten: str
    source_segment_ids: list[str] = Field(description="earlier segments whose facts the rewrite brings in")


class RewritesOut(BaseModel):
    rewrites: list[RewriteOut]


rewrite_agent: Agent[None, RewritesOut] = Agent(None, output_type=NativeOutput(RewritesOut), name="discourse_rewrite")


@dataclass
class RewriteCheck:
    ok: bool
    reason: str = ""


def check_rewrite(
    original: Segment, rewritten: str, sources: list[Segment], extra_hedges: tuple[str, ...] = (), lexicon: Lexicon | None = None
) -> RewriteCheck:
    """The three §6.3 checks. Hedges are the built-in list, ``extra_hedges`` and, with a ``lexicon``, its hedge table."""
    if not rewritten.strip():
        return RewriteCheck(False, "empty")
    if len(rewritten) > MAX_RATIO * max(len(original.text), 1):
        return RewriteCheck(False, "longer than 3x the original")
    allowed = numbers_in(original.text)
    for s in sources:
        allowed |= numbers_in(s.text)
    new_numbers = {n for n in numbers_in(rewritten) if not any(abs(n - a) < 1e-9 for a in allowed)}
    if new_numbers:
        return RewriteCheck(False, f"new numbers {sorted(new_numbers)[:3]}")
    lost = hedges_in(original.text, extra_hedges) - hedges_in(rewritten, extra_hedges)
    if lexicon is not None:
        from ..lexicon.hedges import find_hedges

        kept = {h.lexicon_key for h in find_hedges(rewritten, lexicon)}
        lost |= {h.term for h in find_hedges(original.text, lexicon) if h.lexicon_key not in kept}
    if lost:
        return RewriteCheck(False, f"hedges removed: {sorted(lost)}")
    return RewriteCheck(True)


def needs_rewrite(seg: Segment, mentions: list[Mention]) -> bool:
    """A resolved reference, an ellipsis or a fragment (§6.3); backchannels are left alone."""
    if _BACKCHANNEL.match(seg.text):
        return False
    resolved = [m for m in mentions if m.segment_id == seg.segment_id and m.entity_id]
    if any(_PRONOUNS.search(m.surface) or m.surface.lower().startswith(("the ", "that ", "this ")) for m in resolved):
        return True
    words = seg.text.split()
    if len(words) < 6 and any(ch.isdigit() for ch in seg.text):
        return True
    return bool(_PRONOUNS.search(seg.text)) and len(words) < 25


async def rewrite_segments(
    transcript: Transcript,
    mentions: list[Mention],
    pins: VersionPins,
    *,
    use_llm: bool,
    extra_hedges: tuple[str, ...] = (),
    concurrency: int = 4,
    lexicon: Lexicon | None = None,
) -> tuple[dict[str, str], dict[str, list[str]], dict[str, str], dict[str, int]]:
    """Returns (rewrites that passed, their source segments, rejected rewrites with reasons, usage)."""
    usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
    if not use_llm:
        return {}, {}, {}, usage
    segs = transcript.segments
    index = {s.segment_id: i for i, s in enumerate(segs)}
    targets = [s for s in segs if needs_rewrite(s, mentions)]
    rm = llm.for_role("small", pins)
    prompt = pinned("discourse_rewrite", pins.prompt_versions)
    sem = asyncio.Semaphore(concurrency)
    accepted: dict[str, str] = {}
    sources: dict[str, list[str]] = {}
    rejected: dict[str, str] = {}

    async def run_batch(batch: list[Segment]) -> None:
        first = index[batch[0].segment_id]
        context = segs[max(0, first - CONTEXT) : first]
        ref_lines = []
        for m in mentions:
            if m.entity_id and any(m.segment_id == b.segment_id for b in batch):
                ref_lines.append(f"- {m.segment_id}: '{m.surface}' → {m.entity_id}")
        text = (
            "## Context (do not rewrite)\n"
            + "\n".join(render_segment(s) for s in context)
            + "\n\n## Resolved references\n"
            + ("\n".join(ref_lines) or "(none)")
            + "\n\n## Rewrite these segments\n"
            + "\n".join(render_segment(s) for s in batch)
        )
        async with sem:
            result = await rewrite_agent.run(text, model=rm.model, model_settings=rm.settings, instructions=prompt.text)
        usage["requests"] += result.usage.requests
        usage["input_tokens"] += result.usage.input_tokens or 0
        usage["output_tokens"] += result.usage.output_tokens or 0
        wanted = {s.segment_id for s in batch}
        for rw in result.output.rewrites:
            if rw.segment_id not in wanted:
                continue
            seg = segs[index[rw.segment_id]]
            srcs = [segs[index[sid]] for sid in rw.source_segment_ids if sid in index and index[sid] < index[rw.segment_id]]
            chk = check_rewrite(seg, rw.rewritten, srcs, extra_hedges, lexicon)
            if chk.ok:
                accepted[rw.segment_id] = rw.rewritten.strip()
                sources[rw.segment_id] = [s.segment_id for s in srcs]
            else:
                rejected[rw.segment_id] = chk.reason

    await asyncio.gather(*(run_batch(targets[i : i + BATCH]) for i in range(0, len(targets), BATCH)))
    return accepted, sources, rejected, usage
