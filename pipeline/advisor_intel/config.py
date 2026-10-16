"""Runtime settings, read from the environment (``ADVISOR_INTEL_*``) with safe local defaults.

Nothing here is a secret by default: API keys come only from the environment, and a missing
key switches the matching integration off (tests and offline runs use fakes instead).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from typing import Literal

from pydantic import BaseModel

PIPELINE_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = PIPELINE_DIR.parent


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(f"ADVISOR_INTEL_{name}", os.environ.get(name, default))


def _flag(name: str, default: bool) -> bool:
    v = _env(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


class ModelRouting(BaseModel):
    """Model ids per role (spec §13.8). Pydantic AI model strings, e.g. 'anthropic:claude-opus-5-5'."""

    frontier: str = "anthropic:claude-opus-5-5"
    reasoning: str = "anthropic:claude-opus-5-5"  # extraction, thinking on
    verifier: str = "openai:gpt-5"  # a different family from the extractor (§9.1e)
    small: str = "anthropic:claude-haiku-5-5"
    secondary: str = "google:gemini-2.5-pro"  # FallbackModel on provider outage
    # Extraction runs with thinking on (§7.2). Current Claude models take adaptive thinking with an
    # effort level rather than a token budget; "medium" is the closest match to the spec's ~4k budget.
    extraction_effort: Literal["low", "medium", "high"] = "medium"
    # When a request is refused, let the API retry it server-side on its default fallback model.
    refusal_fallbacks: bool = True


class Budgets(BaseModel):
    """Per-run budgets for a 60-minute call (§13.6); scaled linearly above 60 minutes."""

    orchestrator_requests: int = 80
    tool_calls: int = 200
    total_tokens: int = 2_750_000
    usd_cap: float = 25.0
    wall_clock_soft_s: int = 20 * 60
    wall_clock_hard_s: int = 25 * 60
    reextractions_per_group: int = 2
    tool_error_budget: int = 5
    tool_result_chars: int = 6000
    get_segments_chars: int = 20000

    scaled_for_minutes: float | None = None  # set by ``scaled``: budgets are scaled to a call once

    def scaled(self, call_minutes: float) -> "Budgets":
        """Budgets for a call of this length (per hour of call, at least one hour). Scaling twice is a no-op."""
        if self.scaled_for_minutes is not None:
            return self
        f = max(1.0, call_minutes / 60.0)
        return self.model_copy(
            update={
                "orchestrator_requests": int(self.orchestrator_requests * f),
                "tool_calls": int(self.tool_calls * f),
                "total_tokens": int(self.total_tokens * f),
                "usd_cap": self.usd_cap * f,
                "scaled_for_minutes": call_minutes,
            }
        )


class Settings(BaseModel):
    env: str = "local"
    database_url: str = "postgresql://localhost:5432/advisor_intel"
    object_store_uri: str = f"file://{PIPELINE_DIR / '.data' / 'objects'}"
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_api_key: str | None = None
    temporal_tls: bool = False
    task_queue: str = "advisor-intel"
    braintrust_api_key: str | None = None
    braintrust_project: str = "advisor-intel"
    otlp_endpoint: str = "https://api.braintrust.dev/otel"
    models: ModelRouting = ModelRouting()
    budgets: Budgets = Budgets()
    auto_accept_tier_a: bool = False
    conformal_alpha: float = 0.1
    min_conformal_calls: int = 50
    spot_check_rate: float = 0.1
    asr_provider: str = "assemblyai"
    assemblyai_api_key: str | None = None
    deepgram_api_key: str | None = None
    speechmatics_api_key: str | None = None
    pyannote_api_key: str | None = None
    vllm_base_url: str | None = None  # self-hosted second pass, MiniCheck, fine-tuned extractor
    parakeet_base_url: str | None = None  # self-hosted Parakeet + TurboBias gateway (ASR bake-off, constrained second pass)
    web_search_allowlist: list[str] = [
        "sars.gov.za",
        "fsca.co.za",
        "asisa.org.za",
        "discovery.co.za",
        "oldmutual.co.za",
        "sanlam.co.za",
        "liberty.co.za",
        "momentum.co.za",
        "allangray.co.za",
        "coronation.com",
        "ninetyone.com",
        "10x.co.za",
        "sygnia.co.za",
        "satrix.co.za",
    ]
    fna_tables_dir: Path = REPO_DIR / "fna_tables"
    api_token: str | None = None  # bearer token the FNA app's API client sends

    @property
    def store_local_dir(self) -> Path | None:
        return Path(self.object_store_uri.removeprefix("file://")) if self.object_store_uri.startswith("file://") else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    updates: dict = {}
    for field in ("database_url", "object_store_uri", "temporal_address", "temporal_namespace", "task_queue", "braintrust_project", "otlp_endpoint", "asr_provider", "env"):
        v = _env(field.upper())
        if v:
            updates[field] = v
    for field, name in (
        ("temporal_api_key", "TEMPORAL_API_KEY"),
        ("braintrust_api_key", "BRAINTRUST_API_KEY"),
        ("assemblyai_api_key", "ASSEMBLYAI_API_KEY"),
        ("deepgram_api_key", "DEEPGRAM_API_KEY"),
        ("speechmatics_api_key", "SPEECHMATICS_API_KEY"),
        ("pyannote_api_key", "PYANNOTEAI_API_KEY"),
        ("vllm_base_url", "VLLM_BASE_URL"),
        ("parakeet_base_url", "PARAKEET_BASE_URL"),
        ("api_token", "API_TOKEN"),
    ):
        v = _env(name)
        if v:
            updates[field] = v
    updates["temporal_tls"] = _flag("TEMPORAL_TLS", bool(updates.get("temporal_api_key")))
    updates["auto_accept_tier_a"] = _flag("AUTO_ACCEPT_TIER_A", False)
    if _env("FNA_TABLES_DIR"):
        updates["fna_tables_dir"] = Path(_env("FNA_TABLES_DIR"))  # type: ignore[arg-type]
    models = {}
    for role in ("frontier", "reasoning", "verifier", "small", "secondary"):
        v = _env(f"MODEL_{role.upper()}")
        if v:
            models[role] = v
    if models:
        updates["models"] = s.models.model_copy(update=models)
    return s.model_copy(update=updates)
