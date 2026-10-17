"""Stage 2 entry point: ``run_discourse`` (spec §6), run as the Temporal activity ``run_discourse``.

Order: seed the registry from the prior profile and CRM → discovery windows and reference
resolution (frontier model) → merge pass → CRM linking → rewrites (small model, checked) →
revision detection (patterns + small model) → topic index (embeddings + small model). With a stub
model pin ("test") the model passes are skipped and the deterministic passes still run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from .. import llm
from ..embeddings import get_embedder
from ..models.crm import CrmRecord
from ..models.entities import DiscourseOutput
from ..models.profile import Profile
from ..models.runs import VersionPins
from ..models.transcript import Transcript
from ..schema.slots import Catalog, load_catalog

if TYPE_CHECKING:
    from ..lexicon.loader import Lexicon
from . import registry as R
from .revisions import detect_revisions
from .rewrite import rewrite_segments
from .topics import embedding_scores, index_from_scores, llm_scores, merge_scores, segment_vectors


def _add(total: dict[str, int], part: dict[str, int]) -> None:
    for k, v in part.items():
        total[k] = total.get(k, 0) + int(v)


async def run_discourse(
    transcript: Transcript,
    *,
    prior: Profile | None = None,
    crm: CrmRecord | None = None,
    pins: VersionPins | None = None,
    catalog: Catalog | None = None,
    llm_topic_pass: str = "all",  # "all" | "untagged" | "off" (§6.5: reduce once embeddings reach recall)
    extra_hedges: tuple[str, ...] = (),
    lexicon: Lexicon | None = None,  # the run's vagueness lexicon; its hedge table backs the rewrite checks (§6.3)
) -> DiscourseOutput:
    pins = pins or VersionPins()
    catalog = catalog or load_catalog(pins.schema_version)
    groups = catalog.group_names()
    usage: dict[str, int] = {}
    if lexicon is None:
        from ..lexicon.loader import load_lexicon

        lexicon = load_lexicon(pins.lexicon_version)

    reg = R.seed_registry(
        prior.entities if prior else None, crm, secondary_speaker=any(s.speaker_role == "client_secondary" for s in transcript.segments)
    )
    use_frontier = not llm.is_stub(llm.role_model_name("frontier", pins))
    use_small = not llm.is_stub(llm.role_model_name("small", pins))
    reg = await R.discover(reg, transcript, pins, use_llm=use_frontier)
    reg = await R.merge_pass(reg, pins, use_llm=use_frontier)
    _add(usage, reg.usage)

    embedder = get_embedder(pins.embedding_model)
    reg = R.link_crm(reg, crm, transcript, embedder)

    rewrites, sources, rejected, u = await rewrite_segments(
        transcript, reg.mentions, pins, use_llm=use_small, extra_hedges=extra_hedges, lexicon=lexicon
    )
    _add(usage, u)

    vectors = segment_vectors(transcript, rewrites, embedder)
    emb_scores = embedding_scores(vectors, groups, embedder)
    llm_part: dict[str, dict[str, float]] = {}
    if use_small and llm_topic_pass != "off":
        only = {sid for sid, gs in emb_scores.items() if not gs} if llm_topic_pass == "untagged" else None
        llm_part, u = await llm_scores(transcript, rewrites, groups, pins, only=only)
        _add(usage, u)
    scores = merge_scores(emb_scores, llm_part)
    order = [s.segment_id for s in transcript.segments]
    topic_index = index_from_scores(scores, order)
    topic_of = {sid: max(gs, key=gs.get) for sid, gs in scores.items() if gs}

    revisions, u = await detect_revisions(transcript, pins, use_llm=use_small, groups=groups, topic_of=topic_of)
    _add(usage, u)

    return DiscourseOutput(
        entities=reg.entities,
        mentions=reg.mentions,
        ambiguous_refs=sorted(reg.ambiguous),
        rewrites=rewrites,
        rewrite_sources=sources,
        revisions=revisions,
        topic_index=topic_index,
        topic_scores={sid: gs for sid, gs in scores.items() if gs},
        possible_duplicates=reg.possible_duplicates,
        embedding_model=embedder.name,
        segment_vectors={sid: np.round(v, 5).tolist() for sid, v in vectors.items()},
        rewrite_rejections=rejected,
        usage=usage,
    )


def with_rewrites(transcript: Transcript, discourse: DiscourseOutput | None) -> Transcript:
    """Stage 2's output on the segments, for the agent and verification: the self-contained rewrites in
    ``rewritten_text`` (the original text is never overwritten, §6) and the ``self_correction`` flag on
    segments that revise an earlier value (§3.1, §6.4)."""
    if discourse is None or not (discourse.rewrites or discourse.revisions):
        return transcript
    revising = {r.segment_id for r in discourse.revisions}
    segs = [
        s.model_copy(
            update={
                "rewritten_text": discourse.rewrites.get(s.segment_id, s.rewritten_text),
                "flags": (s.flags | {"self_correction"}) if s.segment_id in revising else s.flags,
            }
        )
        for s in transcript.segments
    ]
    return transcript.model_copy(update={"segments": segs})
