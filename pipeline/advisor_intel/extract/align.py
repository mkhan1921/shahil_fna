"""Span alignment (spec §7.3 steps 1–3): grounding is its own step after extraction.

The model's ``said`` text and segment ids are hints for finding evidence, never citations in
themselves. For each field value:

1. **Candidates**: the stage-2 segments plus the top 10 window segments by BM25 and by embedding
   similarity to ``said`` and the slot question.
2. **Alignment**: ``said`` and each candidate are canonicalised (spoken numbers to digits, case and
   punctuation folded) and compared with rapidfuzz; a candidate scoring ≥ 90 is accepted with no
   model call. The match must run in the right direction (the quote is found inside the segment,
   not a short segment inside the quote) and every number in the quote must appear in the segment
   (allowing an elided scale word), so "about one point one million" never grounds "about one
   point two million". Only values still unresolved go to the aligner sub-agent, on a different
   model family from the extractor, which scores each candidate.
3. **Keep or drop**: spans scoring ≥ 0.5 become the fact's evidence and the best score is a
   calibration feature. A value with no located span is dropped and logged — it never reaches the
   profile. If ``said`` does not match the top span (partial ratio < 85), it is replaced by the
   span's text. Character offsets come from fuzzy matching within the segment, and timestamps and
   ASR confidence from the words under the span.

A value with a revising hypothesis (§6.4, §7.3 step 7) is also grounded in the revising statement:
a candidate segment that discourse flagged as a revision and that carries the revising
hypothesis's number becomes ``revises`` evidence, so the 0.8-weight option is never cited only by
the words it replaced.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np
from pydantic import BaseModel, Field
from pydantic_ai import Agent, NativeOutput
from rank_bm25 import BM25Okapi
from rapidfuzz import fuzz

from .. import llm
from ..discourse.text import _SCALES, _TENS, _UNITS, render_segment
from ..embeddings import cosine, get_embedder
from ..models.extraction import FieldValue
from ..models.facts import Evidence
from ..models.transcript import Segment
from ..prompts import pinned
from ..schema.slots import SlotDef
from .context import ExtractionDeps

ACCEPT_DETERMINISTIC = 90
KEEP_SCORE = 0.5
SAID_MATCH = 85
TOP_K = 10
MIN_CONTAINMENT = 0.75  # a candidate shorter than this share of the quote cannot contain it
REVISION_SCORE = 0.9  # a revising statement located by its number, below a verbatim match
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


class SpanScore(BaseModel):
    segment_id: str
    score: float = Field(description="0 = unrelated, 0.5 = implies it, 1 = states it")


class SpanScores(BaseModel):
    scores: list[SpanScore]


aligner_agent: Agent[None, SpanScores] = Agent(None, output_type=NativeOutput(SpanScores), name="aligner", retries=2)


# --------------------------------------------------------------------------- canonical text


def canon(text: str) -> str:
    """Lowercase, punctuation folded, number words turned into digits ('forty-two thousand' → '42000')."""
    t = text.lower().replace("-", " ")
    t = re.sub(r"(?<=\d)[ ,](?=\d{3}\b)", "", t)
    t = re.sub(r"[^\w\s.%]", " ", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)  # sentence full stops go; decimal points stay
    words = t.split()
    out: list[str] = []
    i = 0
    numeric = lambda w: w in _UNITS or w in _TENS
    while i < len(words):
        w = words[i]
        if numeric(w):
            total, current = 0.0, 0.0
            j = i
            while j < len(words):
                x = words[j]
                if x in _UNITS:
                    current += _UNITS[x]
                elif x in _TENS:
                    current += _TENS[x]
                elif x == "hundred":
                    current = max(current, 1) * 100
                elif x in ("point", "comma") and j + 1 < len(words) and words[j + 1] in _UNITS:
                    # "one point two million", SA "one comma two mil": decimal digits follow
                    frac, k = "", j + 1
                    while k < len(words) and words[k] in _UNITS:
                        frac += str(_UNITS[words[k]] % 10)
                        k += 1
                    current += float("0." + frac)
                    j = k - 1
                elif x in _SCALES:
                    total += max(current, 1) * _SCALES[x]
                    current = 0
                elif x == "and" and j + 1 < len(words) and numeric(words[j + 1]):
                    pass
                else:
                    break
                j += 1
            v = total + current
            out.append(str(int(v)) if v == int(v) else f"{v:g}")
            i = j
        else:
            out.append(w)
            i += 1
    return " ".join(out)


def _numbers(c: str) -> list[float]:
    return [float(m) for m in _NUMBER.findall(c)]


def _number_in(value: float, numbers: list[float]) -> bool:
    """``value`` is among ``numbers``, or differs from one only by a power of ten (an elided scale word)."""
    for n in numbers:
        if math.isclose(n, value, rel_tol=1e-6, abs_tol=1e-9):
            return True
        if n > 0 and value > 0:
            k = math.log10(value / n)
            if abs(k - round(k)) < 1e-6 and abs(round(k)) <= 9:
                return True
    return False


def deterministic_score(said_c: str, seg_c: str) -> float:
    """The rapidfuzz partial ratio of a canonical quote against a canonical segment, or 0 when the
    segment cannot contain the quote (too short) or lacks a number the quote carries."""
    if not said_c or not seg_c or len(seg_c) < MIN_CONTAINMENT * len(said_c):
        return 0.0
    seg_numbers = _numbers(seg_c)
    if any(not _number_in(v, seg_numbers) for v in _numbers(said_c)):
        return 0.0
    return float(fuzz.partial_ratio(said_c, seg_c))


# --------------------------------------------------------------------------- spans


@dataclass
class Aligned:
    value: FieldValue
    evidence: list[Evidence]
    aligner_score: float
    method: str  # "deterministic" | "aligner"
    said_replaced: bool = False


@dataclass
class AlignResult:
    aligned: dict[str, Aligned] = field(default_factory=dict)
    dropped: dict[str, str] = field(default_factory=dict)  # field key → why
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


def _word_offsets(seg: Segment) -> list[tuple[int, int]]:
    """Character offsets of each word within the segment text (sequential search)."""
    offs: list[tuple[int, int]] = []
    pos = 0
    low = seg.text.lower()
    for w in seg.words:
        token = w.text.lower()
        i = low.find(token, pos)
        if i < 0:
            i = pos
        offs.append((i, i + len(token)))
        pos = i + len(token)
    return offs


def span_in_segment(seg: Segment, said: str) -> tuple[int, int, float]:
    """Best fuzzy location of ``said`` in the segment: (start, end, ratio 0–100)."""
    if not said.strip():
        return 0, len(seg.text), 0.0
    al = fuzz.partial_ratio_alignment(said.lower(), seg.text.lower())
    if al is None or al.score < 60:
        return 0, len(seg.text), al.score if al else 0.0
    start, end = al.dest_start, al.dest_end
    # Widen to whole words.
    while start > 0 and seg.text[start - 1].isalnum():
        start -= 1
    while end < len(seg.text) and seg.text[end].isalnum():
        end += 1
    return start, end, al.score


def evidence_for(seg: Segment, start: int, end: int, *, role: str = "supports", score: float | None = None) -> Evidence:
    offs = _word_offsets(seg)
    covered = [w for w, (a, b) in zip(seg.words, offs) if b > start and a < end] or seg.words
    t0 = covered[0].t_start if covered else seg.t_start
    t1 = covered[-1].t_end if covered else seg.t_end
    conf = float(np.mean([w.conf for w in covered])) if covered else seg.mean_conf
    return Evidence(segment_id=seg.segment_id, char_start=start, char_end=end, quote=seg.text[start:end], speaker_role=seg.speaker_role,
                    t_start=t0, t_end=t1, asr_mean_conf=round(conf, 4), role=role, aligner_score=None if score is None else round(score, 4))  # type: ignore[arg-type]


def candidates(deps: ExtractionDeps, fv: FieldValue, slot: SlotDef, status_segments: list[str]) -> list[Segment]:
    window = deps.window()
    if not window:
        return []
    idx = {s.segment_id: s for s in window}
    picked: list[str] = [s for s in status_segments if s in idx]
    query = f"{fv.said} {slot.label} {slot.question}"
    corpus = [canon(s.text).split() or ["_"] for s in window]
    bm = BM25Okapi(corpus)
    bm_scores = bm.get_scores(canon(query).split())
    picked += [window[i].segment_id for i in np.argsort(-bm_scores)[:TOP_K] if bm_scores[i] > 0]
    emb = get_embedder(deps.pins.embedding_model)
    vecs = deps.discourse.segment_vectors
    if all(s.segment_id in vecs for s in window):
        mat = np.asarray([vecs[s.segment_id] for s in window], dtype=np.float32)
    else:
        mat = emb.embed_documents([s.text for s in window])
    sims = cosine(emb.embed_queries([query]), mat)[0]
    picked += [window[i].segment_id for i in np.argsort(-sims)[:TOP_K]]
    return [idx[s] for s in dict.fromkeys(picked)]


async def align_values(
    deps: ExtractionDeps,
    values: dict[str, FieldValue],
    status_segments: dict[str, list[str]],
    slots: dict[str, SlotDef],
) -> AlignResult:
    """Ground every field value in transcript spans, or drop it (§7.3)."""
    res = AlignResult()
    unresolved: dict[str, list[Segment]] = {}
    candidates_by_key: dict[str, list[Segment]] = {}
    for key, fv in values.items():
        slot = slots[key]
        cands = candidates(deps, fv, slot, status_segments.get(key, []))
        if not cands:
            res.dropped[key] = "no candidate segments in the window"
            continue
        candidates_by_key[key] = cands
        said_c = canon(fv.said)
        scored = sorted(((deterministic_score(said_c, canon(s.text)), s) for s in cands), key=lambda t: -t[0])
        best_ratio = scored[0][0]
        if best_ratio >= ACCEPT_DETERMINISTIC and len(said_c) >= 3:
            ev = []
            for ratio, s in scored:
                if ratio < ACCEPT_DETERMINISTIC:
                    break
                a, b, _ = span_in_segment(s, fv.said)
                ev.append(evidence_for(s, a, b, role=_role(fv, s, deps), score=ratio / 100))
            res.aligned[key] = Aligned(value=fv, evidence=ev[:3], aligner_score=best_ratio / 100, method="deterministic")
        else:
            unresolved[key] = cands
    if unresolved:
        await _model_align(deps, values, unresolved, slots, res)
    for key, al in list(res.aligned.items()):
        extra = _revision_evidence(al.value, al.evidence, candidates_by_key.get(key, []), deps)
        if extra:
            res.aligned[key] = Aligned(value=al.value, evidence=al.evidence + extra, aligner_score=al.aligner_score, method=al.method)
    for key, al in list(res.aligned.items()):
        top = al.evidence[0]
        seg = deps.transcript.segment(top.segment_id)
        if fuzz.partial_ratio(canon(al.value.said), canon(top.quote)) < SAID_MATCH:
            res.aligned[key] = Aligned(value=al.value.model_copy(update={"said": seg.text[top.char_start : top.char_end] or top.quote}),
                                       evidence=al.evidence, aligner_score=al.aligner_score, method=al.method, said_replaced=True)
    return res


def _role(fv: FieldValue, seg: Segment, deps: ExtractionDeps) -> str:
    """Evidence from a segment that revises an earlier one is ``revises`` (§6.4)."""
    if any(r.segment_id == seg.segment_id for r in deps.discourse.revisions) and any(h.revises for h in fv.hypotheses):
        return "revises"
    return "supports"


def _form_numbers(fv: FieldValue) -> list[float]:
    """The numbers the revising hypotheses quote (amounts, bounds, ranges, ages, years)."""
    out: list[float] = []
    for h in fv.hypotheses:
        if not h.revises:
            continue
        f = h.intermediate
        for name in ("value", "low", "high", "bound"):
            v = getattr(f, name, None)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                out.append(float(v))
    return out


def _revision_evidence(fv: FieldValue, evidence: list[Evidence], cands: list[Segment], deps: ExtractionDeps) -> list[Evidence]:
    """``revises`` evidence for a value whose later hypothesis is not yet cited: a candidate segment that
    discourse flagged as a revision and that carries one of the revising hypothesis's numbers."""
    numbers = _form_numbers(fv)
    if not numbers:
        return []
    cited = {e.segment_id for e in evidence}
    revising = {r.segment_id for r in deps.discourse.revisions}
    out: list[Evidence] = []
    for s in cands:
        if s.segment_id in cited or s.segment_id not in revising:
            continue
        seg_numbers = _numbers(canon(s.text))
        if any(_number_in(v, seg_numbers) for v in numbers):
            out.append(evidence_for(s, 0, len(s.text), role="revises", score=REVISION_SCORE))
            cited.add(s.segment_id)
    return out[:2]


async def _model_align(deps: ExtractionDeps, values: dict[str, FieldValue], unresolved: dict[str, list[Segment]], slots: dict[str, SlotDef], res: AlignResult) -> None:
    rm = llm.for_role("verifier", deps.pins)
    res.model = rm.name
    prompt = pinned("aligner", deps.pins.prompt_versions)
    for key, cands in unresolved.items():
        fv = values[key]
        slot = slots[key]
        if llm.is_stub(rm.name):
            res.dropped[key] = "no span matched the quoted words and no aligner model is configured"
            continue
        claim = f"Field: {slot.label} ({slot.slot_id}) for {key.split('#', 1)[-1] if '#' in key else 'the household'}\nValue as quoted: \"{fv.said}\"\nReading: {fv.interpretation}"
        text = f"{claim}\n\n## Candidate segments\n" + "\n".join(render_segment(s) for s in cands)
        result = await aligner_agent.run(text, model=rm.model, model_settings=rm.settings, instructions=prompt.text)
        res.requests += result.usage.requests
        res.input_tokens += result.usage.input_tokens or 0
        res.output_tokens += result.usage.output_tokens or 0
        by_id = {s.segment_id: s for s in cands}
        kept = sorted(((max(0.0, min(1.0, sc.score)), by_id[sc.segment_id]) for sc in result.output.scores if sc.segment_id in by_id and sc.score >= KEEP_SCORE),
                      key=lambda t: -t[0])
        if not kept:
            res.dropped[key] = "the aligner found no span stating or implying the value"
            continue
        ev = []
        for score, s in kept[:3]:
            a, b, _ = span_in_segment(s, fv.said)
            ev.append(evidence_for(s, a, b, role=_role(fv, s, deps), score=score))
        res.aligned[key] = Aligned(value=fv, evidence=ev, aligner_score=kept[0][0], method="aligner")
