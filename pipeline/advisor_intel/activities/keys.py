"""Cache and idempotency keys per stage (spec §5, §13.1, §15.1).

- Ingest output depends only on the ASR pins: ``(call_id, asr_provider, asr_model_version,
  diarization model, grammar version)``. A re-run for any other new pin reuses the transcript.
- Discourse output adds the models and prompts Stage 2 uses; extraction prompt changes (GEPA,
  §15.6) reuse it, so experiments that change only extraction start from the cache.
- Later stages are per run: ``(run id, Temporal execution, all pins, stage inputs)``. A retry within
  an execution finds its own output; a workflow reset (a new execution) runs the stage again.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..models.runs import VersionPins

INGEST_PINS = ("asr_provider", "asr_model_version", "diarization_model", "grammar_version")
DISCOURSE_PINS = INGEST_PINS + ("frontier_model", "small_model", "embedding_model", "schema_version", "lexicon_version")
DISCOURSE_PROMPT_PREFIXES = ("discourse", "entity", "coref", "rewrite", "revision", "topic", "role")


def digest(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()[:24]


def ingest_key(pins: VersionPins) -> str:
    return digest("ingest", {k: getattr(pins, k) for k in INGEST_PINS})


def discourse_key(pins: VersionPins) -> str:
    prompts = {k: v for k, v in pins.prompt_versions.items() if k.startswith(DISCOURSE_PROMPT_PREFIXES)}
    return digest("discourse", {k: getattr(pins, k) for k in DISCOURSE_PINS}, prompts)


def run_key(stage: str, run_id: str, pins: VersionPins, *extra: Any, execution: str = "") -> str:
    return digest(stage, run_id, execution, pins.key(), *extra)
