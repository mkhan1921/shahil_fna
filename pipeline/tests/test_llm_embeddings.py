from __future__ import annotations

import numpy as np
import pytest
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import FunctionModel

from advisor_intel import llm
from advisor_intel.embeddings import HashedEmbedder, cosine, nearest
from advisor_intel.models.runs import VersionPins


def test_test_pins_never_reach_a_provider():
    rm = llm.for_role("reasoning", VersionPins(), thinking=True)
    assert isinstance(rm.model, FunctionModel)


def test_anthropic_settings_use_adaptive_thinking_and_refusal_fallback():
    s = llm.model_settings("anthropic:claude-opus-5-5", thinking=True)
    assert s["thinking"] == "medium"
    assert s["anthropic_betas"] == [llm.REFUSAL_FALLBACK_BETA]
    assert s["extra_body"] == {"fallbacks": "default"}
    assert "temperature" not in s
    other = llm.model_settings("openai:gpt-5", thinking=True, effort="high")
    assert other["thinking"] == "high" and "anthropic_betas" not in other


def test_fallback_model_on_different_provider(monkeypatch):
    # Building a provider model needs a key; nothing is called, so placeholders suffice.
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder")
    monkeypatch.setenv("GOOGLE_API_KEY", "placeholder")
    m = llm.build_model("anthropic:claude-opus-5-5", secondary="google:gemini-2.5-pro")
    assert isinstance(m, FallbackModel)
    assert llm.build_model("anthropic:claude-opus-5-5", secondary="anthropic:claude-haiku-5-5") == "anthropic:claude-opus-5-5"


def test_cost_estimate():
    usd = llm.usage_cost_usd("anthropic:claude-opus-5-5", input_tokens=1_000_000, output_tokens=100_000, cache_read_tokens=500_000)
    assert abs(usd - (0.5 * 4.0 + 0.1 * 20.0 + 0.5 * 0.20)) < 1e-9
    # Cache writes are part of input_tokens and cost 1.25× the input price.
    written = llm.usage_cost_usd(
        "anthropic:claude-opus-5-5", input_tokens=1_000_000, output_tokens=0, cache_read_tokens=500_000, cache_write_tokens=200_000
    )
    assert abs(written - (0.3 * 4.0 + 0.5 * 0.20 + 0.2 * 4.0 * 1.25)) < 1e-9
    assert llm.usage_cost_usd("test", input_tokens=10, output_tokens=10) == 0.0
    assert llm.usage_cost_usd("anthropic:x", input_tokens=1, output_tokens=1, reported=0.42) == 0.42
    assert llm.usage_cost_usd("other:unknown-model", input_tokens=1_000_000, output_tokens=0) == 5.0  # pessimistic default


def test_settings_read_budgets_thresholds_and_hide_secrets(monkeypatch):
    from advisor_intel.config import get_settings

    for k, v in {
        "ADVISOR_INTEL_BUDGET_USD_CAP": "40",
        "ADVISOR_INTEL_BUDGET_TOOL_CALLS": "300",
        "ADVISOR_INTEL_BUDGET_WALL_CLOCK_SOFT_S": "900",
        "ADVISOR_INTEL_CONFORMAL_ALPHA": "0.05",
        "ADVISOR_INTEL_MIN_CONFORMAL_CALLS": "80",
        "ADVISOR_INTEL_SPOT_CHECK_RATE": "0.2",
        "ADVISOR_INTEL_EXTRACTION_EFFORT": "high",
        "ADVISOR_INTEL_REFUSAL_FALLBACKS": "false",
        "ADVISOR_INTEL_WEB_SEARCH_ALLOWLIST": "sars.gov.za, fsca.co.za",
        "ADVISOR_INTEL_API_TOKEN": "tok-secret-123",
        "BRAINTRUST_API_KEY": "bt-secret-456",
        "ADVISOR_INTEL_ENV": "production",
    }.items():
        monkeypatch.setenv(k, v)
    s = get_settings.__wrapped__()
    assert s.budgets.usd_cap == 40 and s.budgets.tool_calls == 300 and s.budgets.wall_clock_soft_s == 900
    assert s.budgets.orchestrator_requests == 80  # untouched fields keep their defaults
    assert s.conformal_alpha == 0.05 and s.min_conformal_calls == 80 and s.spot_check_rate == 0.2
    assert s.models.extraction_effort == "high" and s.models.refusal_fallbacks is False
    assert s.web_search_allowlist == ["sars.gov.za", "fsca.co.za"]
    assert s.env == "production" and s.api_token == "tok-secret-123" and s.braintrust_api_key == "bt-secret-456"
    shown = repr(s) + str(s)
    assert "tok-secret-123" not in shown and "bt-secret-456" not in shown
    monkeypatch.setenv("ADVISOR_INTEL_BUDGET_USD_CAP", "lots")
    with pytest.raises(ValueError, match="BUDGET_USD_CAP"):
        get_settings.__wrapped__()


def test_hashed_embedder_is_deterministic_and_normalised():
    e = HashedEmbedder()
    a = e.embed_documents(["my retirement annuity with Allan Gray", "we want to retire at sixty"])
    b = e.embed_documents(["my retirement annuity with Allan Gray", "we want to retire at sixty"])
    assert np.array_equal(a, b)
    assert np.allclose(np.linalg.norm(a, axis=1), 1.0, atol=1e-5)
    sims = cosine(e.embed_queries(["retirement annuity"]), a)[0]
    assert sims[0] > sims[1]


def test_nearest_keys():
    keys = ["groceries", "school-fees", "medical-aid", "rent"]
    top = nearest("school fees for the kids", keys, k=2)
    assert top[0][0] == "school-fees"
