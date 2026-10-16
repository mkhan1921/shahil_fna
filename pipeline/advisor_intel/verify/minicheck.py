"""Grounding pre-filter: Bespoke-MiniCheck-7B on the self-hosted vLLM deployment (spec §9.1a, §1.3).

MiniCheck scores whether a claim is supported by a document. The model is prompted to answer "Yes" or
"No" and the score is the probability of "Yes" against "No" on the first generated token, read from the
top log-probabilities that vLLM's OpenAI-compatible chat API returns (``max_tokens=1``,
``temperature=0``, ``logprobs=true``). Every claim is scored and the score enters the feature vector as
``minicheck_score``; a tier C claim scoring ≥ 0.9 skips the verifier LLM (§9.1a).

``FakeMiniCheck`` stands in for the deployment in tests and offline runs.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any, Callable, Mapping, Protocol, Sequence

import httpx

from ..config import Settings, get_settings

MODEL = "bespokelabs/Bespoke-MiniCheck-7B"
SKIP_THRESHOLD = 0.9

SYSTEM_PROMPT = (
    "Determine whether the provided claim is consistent with the corresponding document. Consistency in this context implies "
    "that all information presented in the claim is substantiated by the document. If not, it should be considered "
    'inconsistent. Please assess the claim\'s consistency with the document by responding with either "Yes" or "No".'
)
USER_TEMPLATE = "Document: {document}\nClaim: {claim}"


class GroundingScorer(Protocol):
    """Scores (document, claim) pairs: P(the document supports the claim)."""

    async def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]: ...


def yes_probability(choice: Mapping[str, Any]) -> float:
    """P("Yes") / (P("Yes") + P("No")) from one chat-completion choice's first-token log-probabilities."""
    content = ((choice.get("logprobs") or {}).get("content")) or []
    if content:
        first = content[0]
        cands = [(first.get("token", ""), first.get("logprob", -math.inf))] + [(t.get("token", ""), t.get("logprob", -math.inf)) for t in first.get("top_logprobs") or []]
        p_yes = p_no = 0.0
        seen: set[str] = set()
        for tok, lp in cands:
            if tok in seen or lp is None:
                continue
            seen.add(tok)
            word = tok.strip().lower()
            if word in ("yes", "y"):
                p_yes += math.exp(lp)
            elif word in ("no", "n"):
                p_no += math.exp(lp)
        if p_yes + p_no > 0:
            return p_yes / (p_yes + p_no)
    text = (((choice.get("message") or {}).get("content")) or "").strip().lower()
    return 1.0 if text.startswith("yes") else 0.0


class MiniCheckClient:
    """Bespoke-MiniCheck-7B served by vLLM (OpenAI-compatible ``/v1/chat/completions``)."""

    def __init__(
        self,
        base_url: str,
        *,
        model: str = MODEL,
        api_key: str | None = None,
        timeout: float = 30.0,
        concurrency: int = 8,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        base = base_url.rstrip("/")
        self.url = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
        self.model = model
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.timeout = timeout
        self._sem = asyncio.Semaphore(concurrency)
        self._client = client

    def request_body(self, document: str, claim: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_TEMPLATE.format(document=document.strip(), claim=claim.strip())},
            ],
            "max_tokens": 1,
            "temperature": 0,
            "logprobs": True,
            "top_logprobs": 5,
        }

    async def _one(self, client: httpx.AsyncClient, document: str, claim: str) -> float:
        async with self._sem:
            r = await client.post(self.url, json=self.request_body(document, claim), headers=self.headers, timeout=self.timeout)
            r.raise_for_status()
            choices = r.json().get("choices") or []
            return yes_probability(choices[0]) if choices else 0.0

    async def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        if self._client is not None:
            return list(await asyncio.gather(*(self._one(self._client, d, c) for d, c in pairs)))
        async with httpx.AsyncClient() as client:
            return list(await asyncio.gather(*(self._one(client, d, c) for d, c in pairs)))


class FakeMiniCheck:
    """A scripted scorer: a fixed score per claim text, or a function of (document, claim)."""

    def __init__(self, scores: Mapping[str, float] | Callable[[str, str], float] | None = None, *, default: float = 0.5) -> None:
        self.scores = scores or {}
        self.default = default
        self.calls: list[tuple[str, str]] = []

    async def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        out = []
        for document, claim in pairs:
            self.calls.append((document, claim))
            if callable(self.scores):
                out.append(float(self.scores(document, claim)))
            else:
                out.append(float(self.scores.get(claim, self.default)))
        return out


def minicheck_from_settings(settings: Settings | None = None) -> MiniCheckClient | None:
    """The deployment's MiniCheck client, or None when no vLLM endpoint is configured."""
    s = settings or get_settings()
    return MiniCheckClient(s.vllm_base_url) if s.vllm_base_url else None
