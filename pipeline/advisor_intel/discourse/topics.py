"""The topic index: which slot groups each segment is relevant to (spec §6.5).

Two passes, and a segment is kept for a group if either selects it with relevance ≥ 0.3:

1. **Embedding pass.** Each segment (original and rewritten text) is embedded once; each slot group
   has labelled example utterances (``topic_examples.yaml``, to be rebuilt from gold calls) and a
   per-group threshold set for 0.97 recall on gold calls (``fit_thresholds``).
2. **LLM pass.** The small model tags windows of segments, catching what depends on context.

The segment vectors are kept with the discourse output and reused by ``search_transcript``.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
import yaml
from pydantic import BaseModel, Field, WithJsonSchema
from pydantic_ai import Agent, NativeOutput

from .. import llm
from ..embeddings import Embedder, cosine
from ..models.runs import VersionPins
from ..models.transcript import Transcript
from ..prompts import pinned
from .text import render_segment

EXAMPLES_FILE = Path(__file__).parent / "topic_examples.yaml"
KEEP = 0.3  # relevance needed to index a segment (§6.5)
LLM_WINDOW = 30


@lru_cache(maxsize=1)
def load_examples() -> dict:
    return yaml.safe_load(EXAMPLES_FILE.read_text())


def default_threshold(embedder_name: str) -> float:
    d = load_examples()["default_threshold"]
    return float(d.get(embedder_name, d["default"]))


def segment_vectors(transcript: Transcript, rewrites: dict[str, str], embedder: Embedder) -> dict[str, np.ndarray]:
    """One vector per segment: the original text, averaged with its rewrite when there is one."""
    segs = transcript.segments
    if not segs:
        return {}
    base = embedder.embed_documents([s.text for s in segs])
    out: dict[str, np.ndarray] = {}
    rewritten = [(i, rewrites[s.segment_id]) for i, s in enumerate(segs) if s.segment_id in rewrites]
    rw_vecs = embedder.embed_documents([t for _, t in rewritten]) if rewritten else np.zeros((0, embedder.dim))
    rw_by_index = {i: rw_vecs[k] for k, (i, _) in enumerate(rewritten)}
    for i, s in enumerate(segs):
        v = base[i]
        if i in rw_by_index:
            v = v + rw_by_index[i]
            n = np.linalg.norm(v)
            v = v / n if n else v
        out[s.segment_id] = v.astype(np.float32)
    return out


def embedding_scores(
    vectors: dict[str, np.ndarray], groups: list[str], embedder: Embedder, thresholds: dict[str, float] | None = None
) -> dict[str, dict[str, float]]:
    """segment id → {group: relevance} from similarity to each group's examples (mean of the top 3)."""
    ex = load_examples()["groups"]
    ids = list(vectors)
    if not ids:
        return {}
    mat = np.stack([vectors[i] for i in ids])
    thr_default = default_threshold(embedder.name)
    scores: dict[str, dict[str, float]] = {i: {} for i in ids}
    for g in groups:
        examples = ex.get(g) or []
        if not examples:
            continue
        sims = cosine(mat, embedder.embed_documents(examples))
        k = min(3, sims.shape[1])
        top = np.sort(sims, axis=1)[:, -k:].mean(axis=1)
        thr = (thresholds or {}).get(g, thr_default)
        for row, sid in enumerate(ids):
            s = float(top[row])
            if s >= thr:
                # Map similarity above the threshold onto relevance in [0.3, 1].
                rel = KEEP + (1 - KEEP) * min(1.0, (s - thr) / max(1e-6, 1 - thr))
                scores[sid][g] = round(rel, 4)
    return scores


def fit_thresholds(vectors: dict[str, np.ndarray], gold: dict[str, set[str]], embedder: Embedder, recall: float = 0.97) -> dict[str, float]:
    """Per-group thresholds that reach ``recall`` on labelled segments (gold: group → segment ids)."""
    ex = load_examples()["groups"]
    ids = list(vectors)
    mat = np.stack([vectors[i] for i in ids])
    out: dict[str, float] = {}
    for g, positives in gold.items():
        examples = ex.get(g) or []
        pos = [ids.index(s) for s in positives if s in ids]
        if not examples or not pos:
            continue
        sims = cosine(mat, embedder.embed_documents(examples))
        top = np.sort(sims, axis=1)[:, -min(3, sims.shape[1]) :].mean(axis=1)
        out[g] = float(np.quantile(top[pos], 1 - recall))
    return out


def _topics_model(groups: list[str]) -> type[BaseModel]:
    enum = Annotated[Literal[tuple(groups)], WithJsonSchema({"type": "string", "enum": list(groups)})]  # type: ignore[valid-type]

    class GroupScore(BaseModel):
        group: enum  # type: ignore[valid-type]
        relevance: float

    class SegmentTopics(BaseModel):
        segment_id: str
        groups: list[GroupScore] = Field(description="every relevant group with relevance 0..1")

    class TopicsOut(BaseModel):
        segments: list[SegmentTopics]

    return TopicsOut


topic_agent: Agent[None, BaseModel] = Agent(None, output_type=str, name="discourse_topics")


async def llm_scores(
    transcript: Transcript,
    rewrites: dict[str, str],
    groups: list[str],
    pins: VersionPins,
    *,
    only: set[str] | None = None,
    concurrency: int = 4,
) -> tuple[dict[str, dict[str, float]], dict[str, int]]:
    usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
    segs = [s for s in transcript.segments if only is None or s.segment_id in only]
    if not segs:
        return {}, usage
    rm = llm.for_role("small", pins)
    prompt = pinned("discourse_topics", pins.prompt_versions)
    out_model = _topics_model(groups)
    sem = asyncio.Semaphore(concurrency)
    scores: dict[str, dict[str, float]] = {}
    wanted = {s.segment_id for s in segs}

    async def run(chunk) -> None:
        text = f"Slot groups: {', '.join(groups)}\n\n" + "\n".join(render_segment(s, rewrite=rewrites.get(s.segment_id)) for s in chunk)
        async with sem:
            result = await topic_agent.run(
                text, model=rm.model, model_settings=rm.settings, instructions=prompt.text, output_type=NativeOutput(out_model)
            )
        usage["requests"] += result.usage.requests
        usage["input_tokens"] += result.usage.input_tokens or 0
        usage["output_tokens"] += result.usage.output_tokens or 0
        for st in result.output.segments:  # type: ignore[attr-defined]
            if st.segment_id not in wanted:
                continue
            for gs in st.groups:
                rel = max(0.0, min(1.0, gs.relevance))
                if rel >= KEEP:
                    cur = scores.setdefault(st.segment_id, {})
                    cur[gs.group] = max(cur.get(gs.group, 0.0), round(rel, 4))

    await asyncio.gather(*(run(segs[i : i + LLM_WINDOW]) for i in range(0, len(segs), LLM_WINDOW)))
    return scores, usage


def merge_scores(*parts: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for p in parts:
        for sid, gs in p.items():
            cur = out.setdefault(sid, {})
            for g, v in gs.items():
                cur[g] = max(cur.get(g, 0.0), v)
    return out


def index_from_scores(scores: dict[str, dict[str, float]], order: list[str]) -> dict[str, list[str]]:
    pos = {sid: i for i, sid in enumerate(order)}
    index: dict[str, list[str]] = {}
    for sid, gs in scores.items():
        for g, v in gs.items():
            if v >= KEEP:
                index.setdefault(g, []).append(sid)
    return {g: sorted(ids, key=lambda s: pos.get(s, 0)) for g, ids in index.items()}
