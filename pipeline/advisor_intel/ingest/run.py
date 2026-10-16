"""Stage 1 entry points (§5): audio → ``Transcript``, and a scripted call → ``Transcript`` without audio.

``ingest_call`` is pure orchestration over the steps of §5.2; storage, caching by
``(call_id, asr_model_version)`` and heartbeats belong to the ``ingest_audio`` activity that calls it.

    preprocess (16 kHz, −23 LUFS, layout) → VAD (speech regions, compacted timeline) → vocabulary (static
    + per-call biasing) → ASR → times back to the call → diarization (channels, Precision-3 on mono or a
    shared client channel) → segmentation → roles → numeric spans (grammars over the ASR lattice) →
    constrained second pass on risky spans → quality report

``transcript_from_script`` runs the same back half on a ``ScriptedCall`` rendered by ``ScriptedASR``, for
tests and the synthetic-call generator.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Coroutine, Sequence
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal

from ..models.fna import FnaDocument
from ..models.runs import VersionPins
from ..models.transcript import RoleAssignment, Transcript, Word
from .asr.base import AsrAudio, ASRProvider
from .asr.fake import NoiseConfig, ScriptedASR, ScriptedCall, ScriptRender
from .audio import PreparedAudio, preprocess
from .biasing import build_vocabulary, product_names_for_call
from .diarization import ChannelDiarizer, Diarizer, assign_by_turns, relabel
from .grammars import ProductName
from .quality import quality_report
from .roles import apply_roles, assign_roles
from .second_pass import ScriptedSecondPass, SecondPassProvider, run_second_pass
from .segmentation import LabeledWord, segment_words
from .spans import annotate_transcript
from .types import AsrWord, CallMeta, CrmNames, SpeakerTurn
from .vad import SpeechMap, VadConfig, speech_regions, union_regions

MONO_SPEAKERS = (2, 4)
SHARED_CHANNEL_SPEAKERS = (1, 3)


def to_word(w: AsrWord) -> Word:
    return Word(text=w.text, t_start=w.t_start, t_end=w.t_end, conf=w.conf, alts=list(w.alts), raw_spoken=w.raw_spoken)


def _to_call_time(words: Sequence[AsrWord], smap: SpeechMap) -> list[AsrWord]:
    return [w.model_copy(update={"t_start": round(smap.to_call(w.t_start), 3), "t_end": round(smap.to_call(w.t_end), 3)}) for w in words]


def _turns_to_call(turns: Sequence[SpeakerTurn], smap: SpeechMap, time_base: str) -> list[SpeakerTurn]:
    if time_base == "call":
        return list(turns)
    return [t.model_copy(update={"start": smap.to_call(t.start), "end": smap.to_call(t.end)}) for t in turns]


async def diarize_words(
    words: Sequence[AsrWord],
    audio: PreparedAudio,
    smap: SpeechMap,
    regions: Sequence[Sequence[tuple[float, float]]],
    meta: CallMeta,
    diarizer: Diarizer,
) -> tuple[list[LabeledWord], list[SpeakerTurn]]:
    """Speaker label and diarization confidence per word (§5.2 step 4)."""
    time_base = getattr(diarizer, "time_base", "input")
    if audio.layout == "stereo_split":
        channel_turns = ChannelDiarizer().turns(regions)
        shared: dict[int, list[SpeakerTurn]] = {}
        if meta.clients_share_a_channel():
            channels = (
                [c for c in range(audio.n_channels) if c != meta.advisor_channel]
                if meta.advisor_channel is not None
                else list(range(audio.n_channels))
            )
            for c in channels:
                compact = smap.compact(audio.channel(c), audio.sample_rate)
                turns = await diarizer.diarize(
                    compact,
                    audio.sample_rate,
                    min_speakers=SHARED_CHANNEL_SPEAKERS[0],
                    max_speakers=SHARED_CHANNEL_SPEAKERS[1],
                    key=f"{meta.call_id}-ch{c}",
                )
                turns = [t for t in _turns_to_call(turns, smap, time_base) if t.channel in (None, c)]
                if len({t.speaker for t in turns}) > 1:
                    shared[c] = relabel(turns, prefix=f"ch{c}_spk")
        labeled = []
        by_channel: dict[int, list[AsrWord]] = {}
        for w in words:
            by_channel.setdefault(w.channel or 0, []).append(w)
        for c, ws in by_channel.items():
            if c in shared:
                labeled += [LabeledWord(to_word(w), a.speaker, a.confidence, c) for w, a in zip(ws, assign_by_turns(ws, shared[c]))]
            else:
                labeled += [LabeledWord(to_word(w), ChannelDiarizer.label(c), 1.0, c) for w in ws]
        return labeled, [*channel_turns, *(t for ts in shared.values() for t in ts)]
    compact = smap.compact(audio.channel(0), audio.sample_rate)
    turns = await diarizer.diarize(
        compact, audio.sample_rate, min_speakers=MONO_SPEAKERS[0], max_speakers=MONO_SPEAKERS[1], key=meta.call_id
    )
    turns = relabel(_turns_to_call(turns, smap, time_base))
    return [LabeledWord(to_word(w), a.speaker, a.confidence, None) for w, a in zip(words, assign_by_turns(words, turns))], turns


async def assemble(
    labeled: Sequence[LabeledWord],
    turns: Sequence[SpeakerTurn],
    *,
    call_id: str,
    anchor: date,
    language: str,
    provider: str,
    model_version: str,
    alternatives: bool,
    layout: Literal["stereo_split", "mono"],
    role_pins: VersionPins | None,
    crm: CrmNames | None,
    roles: RoleAssignment | None = None,
    use_role_agent: bool | None = None,
    second_pass: SecondPassProvider | None = None,
    audio: PreparedAudio | None = None,
    extra_products: Sequence[ProductName] = (),
) -> Transcript:
    """Segmentation → roles → numeric spans → second pass → quality (§5.2 steps 5–7, §5.3, §5.5, §5.6)."""
    segments = segment_words(labeled, turns=turns if layout == "mono" else ())
    assignment = roles or await assign_roles(segments, pins=role_pins, crm=crm, use_agent=use_role_agent)
    segments = apply_roles(segments, assignment)
    transcript = Transcript(
        call_id=call_id,
        anchor_date=anchor,
        language=language,
        segments=segments,
        asr_provider=provider,
        asr_model_version=model_version,
        asr_alternatives=alternatives,
        channel_layout=layout,
    )
    transcript = annotate_transcript(transcript, extra_products=extra_products)
    if second_pass is not None:
        transcript = await run_second_pass(transcript, second_pass, audio=audio, extra_products=extra_products)
    transcript.quality = quality_report(
        transcript.segments,
        transcript.numeric_spans,
        asr_alternatives=alternatives,
        role_conf={k: v for k, v in assignment.confidence.items()},
    )
    return transcript


async def ingest_call(
    audio: list[Path] | Path,
    meta: CallMeta,
    *,
    asr: ASRProvider,
    diarizer: Diarizer,
    role_agent_pins: VersionPins | None = None,
    second_pass: SecondPassProvider | None = None,
    prior_fna: FnaDocument | None = None,
    crm_names: CrmNames | None = None,
    vad_config: VadConfig | None = None,
    want_alternatives: bool = True,
    use_role_agent: bool | None = None,
) -> Transcript:
    """Run every Stage 1 step on one call's audio and return the transcript with quality report and numeric spans."""
    prepared = preprocess(audio)
    regions = speech_regions(prepared, vad_config or VadConfig())
    smap = SpeechMap(tuple(union_regions(regions)))
    compact = AsrAudio(
        samples=smap.compact(prepared.samples, prepared.sample_rate),
        sample_rate=prepared.sample_rate,
        layout=prepared.layout,
        speech_regions=tuple(smap.compact_regions()),
    )
    vocab = build_vocabulary(prior_fna, anchor=meta.anchor, crm=crm_names)
    result = await asr.transcribe(compact, vocab, want_alternatives=want_alternatives)
    words = _to_call_time(result.words, smap) if result.time_base == "input" else list(result.words)
    words.sort(key=lambda w: (w.t_start, w.channel or 0))
    labeled, turns = await diarize_words(words, prepared, smap, regions, meta, diarizer)
    return await assemble(
        labeled,
        turns,
        call_id=meta.call_id,
        anchor=meta.anchor,
        language=meta.language,
        provider=result.provider,
        model_version=result.model_version,
        alternatives=result.alternatives_available,
        layout=prepared.layout,
        role_pins=role_agent_pins,
        crm=crm_names,
        use_role_agent=use_role_agent,
        second_pass=second_pass,
        audio=prepared,
        extra_products=product_names_for_call(prior_fna, crm_names),
    )


def script_roles(render: ScriptRender, labels: dict[str, str]) -> RoleAssignment:
    """The script's gold roles as a ``RoleAssignment`` over the transcript's labels."""
    roles = {labels[s]: render.roles.get(s, "unknown") for s in render.speakers}
    return RoleAssignment(
        roles=roles, confidence={lab: 1.0 if r != "unknown" else 0.0 for lab, r in roles.items()}, rationale="scripted roles"
    )


async def transcript_from_script_async(
    script: ScriptedCall | str,
    anchor_date: date,
    *,
    call_id: str = "synthetic",
    seed: int = 0,
    noise: NoiseConfig | None = None,
    want_alternatives: bool = True,
    roles: Literal["heuristic", "script", "agent"] = "heuristic",
    role_agent_pins: VersionPins | None = None,
    second_pass: SecondPassProvider | Literal["scripted"] | None = None,
    prior_fna: FnaDocument | None = None,
    crm_names: CrmNames | None = None,
    diarization_conf: float = 1.0,
    asr: ScriptedASR | None = None,
) -> Transcript:
    """A ``Transcript`` from a scripted call (no audio): ScriptedASR words, gold speaker turns, then the real back half.

    Speakers are labelled ``spk_0``, ``spk_1``… in order of first speech, as a diarizer would. ``roles`` picks
    the heuristic, the script's gold roles, or the role agent (on ``role_agent_pins``). ``second_pass="scripted"``
    re-checks risky spans against what the script really said.
    """
    asr = asr or ScriptedASR(script, seed=seed, noise=noise)
    render = asr.render(want_alternatives)
    labels = {spk: f"spk_{i}" for i, spk in enumerate(render.speakers)}
    labeled = [LabeledWord(to_word(w), labels[w.speaker or render.speakers[0]], diarization_conf, w.channel) for w in render.words]
    turns = [
        SpeakerTurn(speaker=labels[t.speaker], start=t.start, end=t.end, confidence=diarization_conf, channel=t.channel)
        for t in render.turns
    ]
    layout: Literal["stereo_split", "mono"] = "stereo_split" if asr.script.stereo else "mono"
    assignment = script_roles(render, labels) if roles == "script" else None
    sp = ScriptedSecondPass(render) if second_pass == "scripted" else second_pass
    return await assemble(
        labeled,
        turns,
        call_id=call_id,
        anchor=anchor_date,
        language="en-ZA",
        provider=asr.name,
        model_version=asr.model_version,
        alternatives=want_alternatives,
        layout=layout,
        role_pins=role_agent_pins,
        crm=crm_names,
        roles=assignment,
        use_role_agent=True if roles == "agent" else False if roles == "heuristic" else None,
        second_pass=sp,
        extra_products=product_names_for_call(prior_fna, crm_names),
    )


def _run_sync[R](coro: Coroutine[Any, Any, R]) -> R:
    """Run a coroutine to completion from sync code, also when an event loop is already running in this thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def transcript_from_script(script: ScriptedCall | str, anchor_date: date | datetime, **kwargs: Any) -> Transcript:
    """Sync convenience wrapper around ``transcript_from_script_async`` (same arguments)."""
    anchor = anchor_date.date() if isinstance(anchor_date, datetime) else anchor_date
    return _run_sync(transcript_from_script_async(script, anchor, **kwargs))


def meta_for(call_id: str, anchor: date, **kwargs: Any) -> CallMeta:
    """A ``CallMeta`` starting at 09:00 on the anchor date (tests and synthetic calls)."""
    return CallMeta(call_id=call_id, started_at=datetime.combine(anchor, time(9, 0)), **kwargs)
