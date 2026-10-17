"""Stage 1 — speech ingestion (spec §5): audio → a ``Transcript`` with roles, word confidence and numeric spans.

Entry points:

- ``ingest_call(audio, meta, asr=…, diarizer=…)``: the whole stage on real audio (used by the ``ingest_audio`` activity).
- ``transcript_from_script(script, anchor_date)``: the same back half on a ``ScriptedCall`` rendered by
  ``ScriptedASR`` (tests, synthetic calls); ``transcript_from_script_async`` inside an event loop.
- ``grammars``: the §5.3 amount, age and product grammars (``grammars.parse``, ``grammars.lattice_readings``).
- ``metrics``: numeric-token WER, DER and role accuracy for the evals (§15.3).
"""

from __future__ import annotations

from . import grammars, metrics
from .asr import (
    AsrAudio,
    ASRProvider,
    AssemblyAIASR,
    DeepgramASR,
    GraniteSpeechASR,
    NoiseConfig,
    ParakeetASR,
    ScriptedASR,
    ScriptedCall,
    ScriptRender,
    ScriptTurn,
    SpeechmaticsASR,
    asr_from_pins,
)
from .audio import PreparedAudio, preprocess
from .biasing import build_vocabulary, product_names_for_call
from .diarization import ChannelDiarizer, Diarizer, FakeDiarizer, PyannoteAIDiarizer
from .quality import quality_report
from .roles import assign_roles, heuristic_roles, role_agent
from .run import assemble, ingest_call, meta_for, transcript_from_script, transcript_from_script_async
from .second_pass import (
    AudioModelCheck,
    FakeSecondPass,
    GrammarConstrainedRedecode,
    ScriptedSecondPass,
    SecondPassProvider,
    run_second_pass,
    select_risky,
)
from .segmentation import LabeledWord, segment_words
from .spans import annotate_transcript, canonical_text
from .types import AsrError, AsrResult, AsrWord, BiasPhrase, CallMeta, CrmNames, ProductHint, SpeakerTurn, Vocabulary
from .vad import SpeechMap, VadConfig, detect_speech

__all__ = [
    "ASRProvider",
    "AsrAudio",
    "AsrError",
    "AsrResult",
    "AsrWord",
    "AssemblyAIASR",
    "AudioModelCheck",
    "BiasPhrase",
    "CallMeta",
    "ChannelDiarizer",
    "CrmNames",
    "DeepgramASR",
    "Diarizer",
    "FakeDiarizer",
    "FakeSecondPass",
    "GrammarConstrainedRedecode",
    "GraniteSpeechASR",
    "LabeledWord",
    "NoiseConfig",
    "ParakeetASR",
    "PreparedAudio",
    "ProductHint",
    "PyannoteAIDiarizer",
    "ScriptRender",
    "ScriptTurn",
    "ScriptedASR",
    "ScriptedCall",
    "ScriptedSecondPass",
    "SecondPassProvider",
    "SpeakerTurn",
    "SpeechMap",
    "SpeechmaticsASR",
    "VadConfig",
    "Vocabulary",
    "annotate_transcript",
    "asr_from_pins",
    "assemble",
    "assign_roles",
    "build_vocabulary",
    "canonical_text",
    "detect_speech",
    "grammars",
    "heuristic_roles",
    "ingest_call",
    "meta_for",
    "metrics",
    "preprocess",
    "product_names_for_call",
    "quality_report",
    "role_agent",
    "run_second_pass",
    "segment_words",
    "select_risky",
    "transcript_from_script",
    "transcript_from_script_async",
]
