"""Entailment check (spec §9.1a, §9.1e).

Every hypothesis with weight ≥ 0.1 becomes a claim sentence (``claims.py``). A verifier agent on the
``verifier`` role — a different provider family from extraction — sees only the fact's evidence segments
and the claims, never the extractor's interpretation, reasoning or self-score, and answers
``entailed | neutral | contradicted`` with a one-line reason per claim.

``entail_score`` is P(entailed) from the verifier's token probabilities when the provider returns them
(OpenAI chat and vLLM's OpenAI-compatible API in ``provider_details['logprobs']``, the Responses API on
the text part, Gemini as ``chosen_candidates`` / ``top_candidates``), else 1 / 0.5 / 0 for the verdict.

Grounding pre-filter: when a MiniCheck scorer is given, every claim is scored against the evidence
segments first (``minicheck_score``); tier C claims scoring ≥ 0.9 skip the verifier LLM and take the
MiniCheck score as their entail score. Tier A and B claims, and tier C claims below 0.9, always go to
the verifier.

Corrective ops: a hypothesis the verifier marks contradicted is retracted (``RETRACT`` with
``hypothesis_index``, actor ``verifier``) when another checked hypothesis survives. When every checked
hypothesis is contradicted the whole fact is retracted as an extraction error, unless that would lose a
prior FNA value; then the fact stays, marked contradicted, for review. Ops go through the reducer.
"""

from __future__ import annotations

import asyncio
import bisect
import logging
import math
import re
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, ModelRetry, NativeOutput, RunContext
from pydantic_ai.messages import ModelResponse, TextPart

from .. import llm
from ..models.facts import Fact
from ..models.profile import Profile
from ..models.runs import VersionPins
from ..models.transcript import Transcript
from ..prompts import pinned
from ..schema.slots import Catalog, load_catalog
from .agents import Resolved, Usage, format_segment, is_offline, resolve, run_guarded
from .claims import Claim, claims_for_fact
from .minicheck import SKIP_THRESHOLD, GroundingScorer

log = logging.getLogger(__name__)

Verdict = Literal["entailed", "neutral", "contradicted"]
VERDICTS: tuple[str, ...] = ("entailed", "neutral", "contradicted")
VERDICT_SCORE: dict[str, float] = {"entailed": 1.0, "neutral": 0.5, "contradicted": 0.0}
SEED_SOURCES = frozenset({"prior_fna", "crm", "fna_default"})
TOP_LOGPROBS = 5
API_ERRORS = ("ModelHTTPError", "ModelAPIError", "FallbackExceptionGroup")  # provider rejections worth one retry without log-probabilities


# --------------------------------------------------------------------------- the verifier agent


class ClaimVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(description="the claim's id, e.g. c0")
    verdict: Verdict = Field(description="entailed, neutral or contradicted")
    reason: str = Field(description="one short line: what in the excerpts decides it")


class EntailmentVerdicts(BaseModel):
    """One verdict per claim."""

    model_config = ConfigDict(extra="forbid")

    verdicts: list[ClaimVerdict]


PROMPT = "entailment"  # prompts/entailment/<version>.md, pinned by VersionPins.prompt_versions["entailment"] (§13.8, §15.6)

entailment_agent: Agent[frozenset[str], EntailmentVerdicts] = Agent(
    None,
    name="entailment_verifier",
    deps_type=frozenset,
    output_type=NativeOutput(EntailmentVerdicts, name="verdicts", strict=True),
    retries={"output": 2},  # instructions come from the pinned prompt at run time
)


@entailment_agent.output_validator
def _one_verdict_per_claim(ctx: RunContext[frozenset[str]], out: EntailmentVerdicts) -> EntailmentVerdicts:
    if is_offline(ctx):
        return out
    got = [v.claim_id for v in out.verdicts]
    expected = set(ctx.deps or ())
    missing, unknown = expected - set(got), set(got) - expected
    dups = {g for g in got if got.count(g) > 1}
    if missing or unknown or dups:
        raise ModelRetry(
            f"Return exactly one verdict for each claim id {sorted(expected)}; missing {sorted(missing)}, "
            f"unknown {sorted(unknown)}, repeated {sorted(dups)}."
        )
    return out


# --------------------------------------------------------------------------- token probabilities

TokenLogprob = tuple[str, float, list[tuple[str, float]]]


def token_logprobs(response: ModelResponse | None) -> list[TokenLogprob] | None:
    """The response's output tokens with their top alternatives, normalised across providers."""
    if response is None:
        return None
    raw: Any = (response.provider_details or {}).get("logprobs")
    if raw is None:
        parts = [p for p in response.parts if isinstance(p, TextPart) and p.provider_details and p.provider_details.get("logprobs")]
        if parts:
            raw = [lp for p in parts for lp in p.provider_details["logprobs"]]  # type: ignore[index]
    if not raw:
        return None
    out: list[TokenLogprob] = []
    if isinstance(raw, list):  # OpenAI chat / Responses, vLLM, xAI
        for d in raw:
            if not isinstance(d, dict) or d.get("logprob") is None:
                continue
            alts = [(t.get("token", ""), float(t["logprob"])) for t in d.get("top_logprobs") or [] if t.get("logprob") is not None]
            out.append((str(d.get("token", "")), float(d["logprob"]), alts))
        return out or None
    if isinstance(raw, dict) and raw.get("chosen_candidates"):  # Gemini
        tops = raw.get("top_candidates") or []
        for i, c in enumerate(raw["chosen_candidates"]):
            if c.get("log_probability") is None:
                continue
            cands = (tops[i] or {}).get("candidates") or [] if i < len(tops) else []
            alts = [(a.get("token", ""), float(a["log_probability"])) for a in cands if a.get("log_probability") is not None]
            out.append((str(c.get("token", "")), float(c["log_probability"]), alts))
        return out or None
    return None


def _match_label(rest: str, labels: Sequence[str]) -> str | None:
    r = rest.lstrip().lower()
    if not r:
        return None
    hits = [lab for lab in labels if lab.startswith(r) or r.startswith(lab)]
    return hits[0] if len(hits) == 1 else None


def label_probabilities(
    tokens: list[TokenLogprob], *, key: str = "verdict", labels: Sequence[str] = VERDICTS
) -> list[dict[str, float] | None]:
    """P(label) at each ``"<key>": "`` value in the generated JSON, in order of appearance.

    The token covering the first character of the value and its top alternatives are mapped to labels
    by prefix ("ent" → entailed); their probabilities are renormalised over the labels. Without
    alternatives only the chosen token's probability is known, and the rest is split evenly.
    """
    text = "".join(t for t, _, _ in tokens)
    starts: list[int] = []
    pos = 0
    for t, _, _ in tokens:
        starts.append(pos)
        pos += len(t)
    out: list[dict[str, float] | None] = []
    for m in re.finditer(r'"' + re.escape(key) + r'"\s*:\s*"', text):
        at = m.end()
        i = bisect.bisect_right(starts, at) - 1
        if i < 0 or i >= len(tokens) or at >= starts[i] + len(tokens[i][0]):
            out.append(None)
            continue
        tok, lp, alts = tokens[i]
        offset = at - starts[i]
        lead = tok[:offset]
        chosen = _match_label(tok[offset:], labels)
        if chosen is None:
            out.append(None)
            continue
        if not alts:
            p = math.exp(lp)
            out.append({lab: (p if lab == chosen else (1 - p) / (len(labels) - 1)) for lab in labels})
            continue
        mass = {lab: 0.0 for lab in labels}
        seen: set[str] = set()
        for t, l in [(tok, lp)] + alts:
            if t in seen or (offset and not t.startswith(lead)):
                continue
            seen.add(t)
            lab = _match_label(t[offset:], labels)
            if lab:
                mass[lab] += math.exp(l)
        total = sum(mass.values())
        out.append({lab: v / total for lab, v in mass.items()} if total > 0 else None)
    return out


def logprob_settings(model_name: str) -> dict[str, Any]:
    """Ask for token log-probabilities where the provider returns them for this model."""
    provider, mid = llm.provider_of(model_name), llm.model_id(model_name)
    reasoning = re.match(r"^(o\d|gpt-5)", mid) is not None
    if provider in ("openai", "openai-chat", "azure", "vllm", "deepseek", "together", "fireworks") and not reasoning:
        return {"openai_logprobs": True, "openai_top_logprobs": TOP_LOGPROBS}
    return {}


# --------------------------------------------------------------------------- results


class HypothesisCheck(BaseModel):
    index: int
    weight: float
    claim: str
    verdict: Verdict | None = None  # None when the verifier did not answer
    reason: str = ""
    entail_score: float | None = None
    score_source: Literal["logprobs", "verdict", "minicheck", "none"] = "none"
    minicheck_score: float | None = None
    skipped_verifier: bool = False


class FactEntailment(BaseModel):
    fact_id: str
    checks: list[HypothesisCheck] = []
    entail_score: float | None = None  # weight-averaged over the checked hypotheses
    minicheck_score: float | None = None
    contradicted: list[int] = []  # hypothesis indices marked contradicted
    status: Literal["entailed", "neutral", "contradicted", "mixed", "not_run"] = "not_run"
    model: str = ""
    independent: bool = True  # verifier and extractor are on different provider families (§9.1e)
    error: str | None = None

    def verification(self) -> dict[str, Any]:
        """The fields this check writes into ``Fact.verification``."""
        return {
            "entailment": {
                "status": self.status,
                "model": self.model,
                "independent": self.independent,
                "error": self.error,
                "hypotheses": [c.model_dump() for c in self.checks],
            },
            "entail_score": self.entail_score,
            "minicheck_score": self.minicheck_score,
            "contradicted": bool(self.contradicted),
        }


class EntailmentResult(BaseModel):
    facts: dict[str, FactEntailment] = {}
    usage: dict[str, int] = {}
    prompt_version: str = ""  # the pinned entailment prompt the verdicts came from


# --------------------------------------------------------------------------- evidence context


def evidence_segment_ids(fact: Fact, transcript: Transcript) -> list[str]:
    index = transcript.index()
    return [sid for sid in dict.fromkeys(e.segment_id for e in fact.evidence) if sid in index]


def evidence_block(fact: Fact, transcript: Transcript) -> str:
    """The fact's evidence segments, as the verifier sees them: no interpretation, no self-score."""
    index = transcript.index()
    lines = [format_segment(index[sid]) for sid in evidence_segment_ids(fact, transcript)]
    quotes = [f'- {e.segment_id}: "{e.quote}"' for e in fact.evidence if e.segment_id in index and e.quote.strip()]
    if quotes:
        lines += ["", "Spans the claims were read from:", *dict.fromkeys(quotes)]
    return "\n".join(lines)


def minicheck_document(fact: Fact, transcript: Transcript) -> str:
    index = transcript.index()
    parts = []
    for sid in evidence_segment_ids(fact, transcript):
        seg = index[sid]
        text = f"{seg.speaker_role}: {seg.text}"
        if seg.rewritten_text and seg.rewritten_text.strip() != seg.text.strip():
            text += f" ({seg.rewritten_text})"
        parts.append(text)
    return "\n".join(parts)


def verifier_prompt(fact: Fact, claims: Sequence[Claim], transcript: Transcript) -> str:
    claim_lines = "\n".join(f"- {c.claim_id}: {c.text}" for c in claims)
    return f"Transcript excerpts:\n{evidence_block(fact, transcript)}\n\nClaims:\n{claim_lines}"


# --------------------------------------------------------------------------- the check


def _status(checks: list[HypothesisCheck]) -> str:
    verdicts = [c.verdict for c in checks if c.verdict is not None]
    if not verdicts:
        return "not_run"
    if all(v == "entailed" for v in verdicts):
        return "entailed"
    if all(v == "contradicted" for v in verdicts):
        return "contradicted"
    if all(v == "neutral" for v in verdicts):
        return "neutral"
    return "mixed"


def _weighted(checks: list[HypothesisCheck], attr: str) -> float | None:
    pairs = [(c.weight, getattr(c, attr)) for c in checks if getattr(c, attr) is not None]
    total = sum(w for w, _ in pairs)
    return sum(w * s for w, s in pairs) / total if total > 0 else None


async def _verify_fact(
    fact: Fact, claims: list[Claim], transcript: Transcript, resolved: Resolved, *, want_logprobs: bool, sem: asyncio.Semaphore, instructions: str
) -> tuple[dict[str, tuple[str, str, float | None]], str | None, Usage]:
    """Run the verifier on one fact's claims: claim id → (verdict, reason, P(entailed) from logprobs)."""
    prompt = verifier_prompt(fact, claims, transcript)
    deps = frozenset(c.claim_id for c in claims)
    async with sem:
        outcome = await run_guarded(entailment_agent, prompt, resolved, deps=deps, instructions=instructions)
        usage = outcome.usage
        if outcome.output is None and want_logprobs and (outcome.error or "").startswith(API_ERRORS):
            # Some deployments reject log-probabilities: retry once without them.
            plain = Resolved(
                resolved.name, resolved.model, {k: v for k, v in (resolved.settings or {}).items() if "logprobs" not in k}, resolved.offline
            )  # type: ignore[arg-type]
            outcome = await run_guarded(entailment_agent, prompt, plain, deps=deps, instructions=instructions)
            usage = usage.merge(outcome.usage)
    if outcome.output is None:
        return {}, outcome.error, usage
    verdicts = outcome.output.verdicts
    probs: list[dict[str, float] | None] = []
    tokens = token_logprobs(outcome.response)
    if tokens:
        probs = label_probabilities(tokens)
        if len(probs) != len(verdicts):
            probs = []
    out: dict[str, tuple[str, str, float | None]] = {}
    for i, v in enumerate(verdicts):
        p = probs[i]["entailed"] if probs and probs[i] is not None else None  # type: ignore[index]
        out[v.claim_id] = (v.verdict, v.reason, p)
    return out, None, usage


async def check_entailment(
    profile: Profile,
    fact_ids: Sequence[str],
    *,
    transcript: Transcript,
    pins: VersionPins | None = None,
    catalog: Catalog | None = None,
    minicheck: GroundingScorer | None = None,
    concurrency: int = 8,
    logprobs: bool | None = None,
) -> EntailmentResult:
    """Entailment for each fact's hypotheses (weight ≥ 0.1) against its evidence segments."""
    catalog = catalog or load_catalog(profile.schema_version)
    extra = {}
    verifier_name = llm.role_model_name("verifier", pins)
    if logprobs is None or logprobs:
        extra = logprob_settings(verifier_name) if logprobs is None else {"openai_logprobs": True, "openai_top_logprobs": TOP_LOGPROBS}
    resolved = resolve("verifier", pins, empty_json='{"verdicts": []}', extra_settings=extra)
    prompt = pinned(PROMPT, pins.prompt_versions if pins else None)
    extractor_name = llm.role_model_name("reasoning", pins)
    independent = (
        llm.is_test(verifier_name) or llm.is_test(extractor_name) or llm.provider_of(verifier_name) != llm.provider_of(extractor_name)
    )
    if not independent:
        log.warning("verifier %s shares a provider family with the extractor %s (§9.1e)", verifier_name, extractor_name)

    work: list[tuple[Fact, list[Claim], str]] = []
    results: dict[str, FactEntailment] = {}
    for fid in dict.fromkeys(fact_ids):
        fact = profile.facts.get(fid)
        if fact is None:
            continue
        res = FactEntailment(fact_id=fid, model=resolved.name, independent=independent)
        results[fid] = res
        claims = claims_for_fact(fact, profile=profile, catalog=catalog)
        if not claims or not evidence_segment_ids(fact, transcript):
            res.error = "no claims" if not claims else "no evidence segments in the transcript"
            continue
        res.checks = [HypothesisCheck(index=c.hypothesis_index, weight=c.weight, claim=c.text) for c in claims]
        work.append((fact, claims, minicheck_document(fact, transcript)))

    # Grounding pre-filter over every claim in one batch.
    if minicheck is not None and work:
        pairs = [(doc, c.text) for _, claims, doc in work for c in claims]
        try:
            scores = await minicheck.score(pairs)
        except Exception as e:  # noqa: BLE001 - the pre-filter is optional; the verifier still runs
            log.warning("MiniCheck failed: %s", e)
            scores = [None] * len(pairs)
        k = 0
        for fact, claims, _ in work:
            for c in claims:
                chk = next(x for x in results[fact.fact_id].checks if x.index == c.hypothesis_index)
                chk.minicheck_score = scores[k]
                k += 1

    usage = Usage()
    sem = asyncio.Semaphore(concurrency)
    jobs = []
    for fact, claims, _ in work:
        tier = catalog.slot(fact.slot_id).sensitivity_tier
        res = results[fact.fact_id]
        by_index = {c.index: c for c in res.checks}
        to_verify = []
        for c in claims:
            chk = by_index[c.hypothesis_index]
            if tier == "C" and chk.minicheck_score is not None and chk.minicheck_score >= SKIP_THRESHOLD:
                chk.skipped_verifier = True
                chk.verdict = "entailed"
                chk.reason = f"grounding pre-filter score {chk.minicheck_score:.2f}"
                chk.entail_score = chk.minicheck_score
                chk.score_source = "minicheck"
            else:
                to_verify.append(c)
        if to_verify:
            jobs.append((fact, to_verify, _verify_fact(fact, to_verify, transcript, resolved, want_logprobs=bool(extra), sem=sem, instructions=prompt.text)))
    answers = await asyncio.gather(*(j[2] for j in jobs))
    for (fact, claims, _), (verdicts, error, used) in zip(jobs, answers):
        usage = usage.merge(used)
        res = results[fact.fact_id]
        res.error = error
        by_index = {c.index: c for c in res.checks}
        for c in claims:
            if c.claim_id not in verdicts:
                continue
            verdict, reason, p = verdicts[c.claim_id]
            chk = by_index[c.hypothesis_index]
            chk.verdict, chk.reason = verdict, reason  # type: ignore[assignment]
            chk.entail_score, chk.score_source = (p, "logprobs") if p is not None else (VERDICT_SCORE[verdict], "verdict")

    for res in results.values():
        res.status = _status(res.checks)  # type: ignore[assignment]
        res.contradicted = [c.index for c in res.checks if c.verdict == "contradicted"]
        res.entail_score = _weighted(res.checks, "entail_score")
        res.minicheck_score = _weighted(res.checks, "minicheck_score")
    return EntailmentResult(facts=results, usage=usage.as_dict(), prompt_version=prompt.version)


def corrective_ops(profile: Profile, result: EntailmentResult) -> list[tuple[str, str, str | None, dict[str, Any], str]]:
    """RETRACT specs (for ``reducer.make_ops``) for hypotheses the verifier marked contradicted."""
    specs: list[tuple[str, str, str | None, dict[str, Any], str]] = []
    for fid, res in result.facts.items():
        fact = profile.facts.get(fid)
        if fact is None or not res.contradicted:
            continue
        checked = [c for c in res.checks if c.verdict is not None]
        bad = sorted({c.index for c in checked if c.verdict == "contradicted"}, reverse=True)
        survivors = [c for c in checked if c.verdict != "contradicted"]
        seg = fact.evidence[0].segment_id if fact.evidence else None
        reasons = {c.index: c.reason for c in checked}
        if survivors:
            for i in bad:  # highest index first, so earlier indices stay valid
                specs.append(
                    (
                        "RETRACT",
                        fid,
                        seg,
                        {"reason": "entailment_contradicted", "hypothesis_index": i, "note": reasons.get(i, "")},
                        "verifier",
                    )
                )
        elif fact.source not in SEED_SOURCES and fact.prior_value is None:
            note = "; ".join(r for r in reasons.values() if r)
            specs.append(("RETRACT", fid, seg, {"reason": "entailment_contradicted", "note": note}, "verifier"))
    return specs
