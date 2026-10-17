"""Entailment check: verifier agent, token probabilities, MiniCheck pre-filter and corrective ops (§9.1a, §9.1e)."""

from __future__ import annotations

import json
import math
import re

import httpx
import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from advisor_intel.extract.reducer import apply_state_ops, check_profile, make_ops
from advisor_intel.models.runs import VersionPins
from advisor_intel.schema.compiler import anthropic_schema, check_limits, openai_schema, schema_stats
from advisor_intel.verify.claims import claims_for_fact
from advisor_intel.verify.entailment import (
    EntailmentVerdicts,
    check_entailment,
    corrective_ops,
    entailment_agent,
    label_probabilities,
    logprob_settings,
    token_logprobs,
)
from advisor_intel.verify.minicheck import FakeMiniCheck, MiniCheckClient, yes_probability

from .verify_factories import ProfileBuilder, ev, money, point, segment, transcript


def _profile():
    b = ProfileBuilder()
    ra = b.add(
        "retirement.{p}.retirementAge",
        "client",
        [(point(60, "age"), 0.6), (point(65, "age"), 0.4)],
        evidence=[ev("s0002", "I want to retire at sixty")],
        interpretation="SECRET-INTERPRETATION",
        self_score=0.77,
    )
    inc = b.add("income.{p}.grossMonthly", "client", money(45000), evidence=[ev("s0004", "forty-five thousand a month")])
    occ = b.add("{p}.occupation", "client", point("engineer"), evidence=[ev("s0006", "I'm an engineer")])
    return b.build(), ra, inc, occ


def _transcript():
    return transcript(
        [
            segment("s0001", "So when do you want to stop working?", "advisor", t0=0),
            segment("s0002", "I want to retire at sixty, my wife says sixty-five", t0=5, rewritten="The client wants to retire at sixty."),
            segment("s0003", "And what do you earn?", "advisor", t0=12),
            segment("s0004", "About forty-five thousand a month before tax", t0=15, confs=[0.9, 0.5, 0.9, 0.9, 0.9, 0.9, 0.9]),
            segment("s0006", "I'm an engineer at Eskom", t0=25),
        ]
    )


def _verdicts_from(rule):
    """A FunctionModel answering each claim with rule(claim_text) → verdict; it records prompts."""
    prompts: list[str] = []

    def fn(messages, info: AgentInfo):
        prompt = messages[-1].parts[-1].content if hasattr(messages[-1].parts[-1], "content") else ""
        prompts.append(str(prompt))
        claims = re.findall(r"^- (c\d+): (.*)$", str(prompt), flags=re.MULTILINE)
        out = {"verdicts": [{"claim_id": cid, "verdict": rule(text), "reason": "because"} for cid, text in claims]}
        return ModelResponse(parts=[TextPart(json.dumps(out))])

    return FunctionModel(fn), prompts


async def test_verdicts_scores_and_independence_from_the_extractor():
    p, ra, inc, _occ = _profile()
    model, prompts = _verdicts_from(lambda t: "contradicted" if "65" in t else "entailed")
    with entailment_agent.override(model=model):
        res = await check_entailment(p, [ra, inc], transcript=_transcript(), pins=VersionPins())
    r = res.facts[ra]
    assert [c.verdict for c in r.checks] == ["entailed", "contradicted"]
    assert r.status == "mixed" and r.contradicted == [1]
    assert r.entail_score == pytest.approx(0.6 * 1.0 + 0.4 * 0.0)
    assert all(c.score_source == "verdict" for c in r.checks)
    assert res.facts[inc].status == "entailed" and res.facts[inc].entail_score == 1.0
    # the verifier sees the evidence segments and claims only: no interpretation, no self-score
    joined = "\n".join(prompts)
    assert "SECRET-INTERPRETATION" not in joined and "0.77" not in joined
    assert "s0002 | client_primary" in joined and "⟨rewrite: The client wants to retire at sixty.⟩" in joined
    assert "About {forty-five?0.50} thousand" in joined  # low-ASR words are marked as in the extraction prompts
    assert "s0001" not in joined  # not an evidence segment
    assert res.usage["requests"] == 2


async def test_corrective_ops_retract_contradicted_hypotheses_through_the_reducer():
    p, ra, inc, _ = _profile()
    model, _ = _verdicts_from(lambda t: "contradicted" if ("65" in t or "45,000" in t) else "entailed")
    with entailment_agent.override(model=model):
        res = await check_entailment(p, [ra, inc], transcript=_transcript(), pins=VersionPins())
    specs = corrective_ops(p, res)
    assert ("RETRACT", ra) in [(s[0], s[1]) for s in specs]
    hyp = next(s for s in specs if s[1] == ra)
    assert hyp[3]["hypothesis_index"] == 1 and hyp[4] == "verifier" and hyp[3]["reason"] == "entailment_contradicted"
    whole = next(s for s in specs if s[1] == inc)
    assert "hypothesis_index" not in whole[3]
    r = apply_state_ops(p, make_ops(p, specs))
    assert not r.rejected
    assert inc not in r.profile.facts  # an extraction error: removed
    assert [h.value.dist.value for h in r.profile.facts[ra].hypotheses] == [60]
    assert r.profile.facts[ra].hypotheses[0].weight == pytest.approx(1.0)
    assert check_profile(r.profile) == []


async def test_a_contradicted_fact_with_a_prior_value_is_kept_for_review_as_contradicted():
    """§4.3: a contradicted fact is always sent to review, as a mixture of the call's reading and the prior value."""
    b = ProfileBuilder()
    fid = b.add("income.{p}.grossMonthly", "client", money(45000), evidence=[ev("s0004", "forty-five thousand")], prior=money(40000))
    p = b.build()
    model, _ = _verdicts_from(lambda t: "contradicted")
    with entailment_agent.override(model=model):
        res = await check_entailment(p, [fid], transcript=_transcript(), pins=VersionPins())
    assert res.facts[fid].status == "contradicted"
    specs = corrective_ops(p, res)
    assert [(s[0], s[1], s[4]) for s in specs] == [("UPDATE", fid, "verifier")] and specs[0][3]["status"] == "contradicted"
    r = apply_state_ops(p, make_ops(p, specs))
    f = r.profile.facts[fid]
    assert not r.rejected and f.status == "contradicted" and f.source == "call"
    assert [h.value for h in f.hypotheses] == [money(45000), money(40000)] and [h.weight for h in f.hypotheses] == [0.5, 0.5]
    assert "prior FNA value" in f.hypotheses[1].rationale and check_profile(r.profile) == []
    # the prior already among the readings is not added twice
    again = corrective_ops(r.profile, res)
    assert again[0][3] == {"status": "contradicted", "reason": specs[0][3]["reason"]}
    assert res.facts[fid].verification()["contradicted"] is True


def _tokens(text: str, split_value: str | None = None):
    """Tokenise JSON text roughly like a BPE tokenizer; optionally split one verdict value in two."""
    toks = re.findall(r"\w+|[^\w\s]+|\s+", text)
    if split_value:
        out = []
        for t in toks:
            if t == split_value:
                out += [t[:3], t[3:]]
            else:
                out.append(t)
        toks = out
    return toks


def _logprob_entries(toks, alts_at: dict[int, list[tuple[str, float]]]):
    entries = []
    for i, t in enumerate(toks):
        alts = alts_at.get(i, [])
        lp = next((math.log(p) for tok, p in alts if tok == t), 0.0) if alts else 0.0
        entries.append({"token": t, "logprob": lp, "top_logprobs": [{"token": tok, "logprob": math.log(p)} for tok, p in alts]})
    return entries


async def test_entail_score_from_token_probabilities():
    p, ra, _, _ = _profile()

    def fn(messages, info: AgentInfo):
        out = {
            "verdicts": [
                {"claim_id": "c0", "verdict": "entailed", "reason": "said sixty"},
                {"claim_id": "c1", "verdict": "neutral", "reason": "the wife said it"},
            ]
        }
        text = json.dumps(out)
        toks = _tokens(text, split_value="entailed")
        i_ent = toks.index("ent")
        i_neu = toks.index("neutral")
        alts = {i_ent: [("ent", 0.8), ("neutral", 0.15), ("contr", 0.05)], i_neu: [("neutral", 0.6), ("ent", 0.3), ("contradicted", 0.1)]}
        return ModelResponse(parts=[TextPart(text)], provider_details={"logprobs": _logprob_entries(toks, alts)})

    with entailment_agent.override(model=FunctionModel(fn)):
        res = await check_entailment(p, [ra], transcript=_transcript(), pins=VersionPins())
    c0, c1 = res.facts[ra].checks
    assert c0.score_source == "logprobs" and c0.entail_score == pytest.approx(0.8)
    assert c1.verdict == "neutral" and c1.entail_score == pytest.approx(0.3)
    assert res.facts[ra].entail_score == pytest.approx(0.6 * 0.8 + 0.4 * 0.3)


def test_label_probabilities_handle_merged_tokens_and_missing_alternatives():
    # the opening quote and the start of the label share one token
    toks = [
        ('{"verdicts":[{"claim_id":"c0","verdict', 0.0, []),
        ('":"ent', math.log(0.7), [('":"ent', math.log(0.7)), ('":"contr', math.log(0.2)), ('":"neutral', math.log(0.1))]),
        ('ailed","reason":"x"}]}', 0.0, []),
    ]
    probs = label_probabilities(toks)
    assert probs[0] == pytest.approx({"entailed": 0.7, "neutral": 0.1, "contradicted": 0.2})
    # no alternatives: the chosen token's probability, the rest split evenly
    toks2 = [('{"verdict":"', 0.0, []), ("neutral", math.log(0.9), []), ('"}', 0.0, [])]
    assert label_probabilities(toks2)[0] == pytest.approx({"entailed": 0.05, "neutral": 0.9, "contradicted": 0.05})


def test_token_logprobs_reads_openai_responses_and_gemini_shapes():
    chat = ModelResponse(parts=[TextPart("x")], provider_details={"logprobs": [{"token": "a", "logprob": -0.1, "top_logprobs": []}]})
    assert token_logprobs(chat) == [("a", -0.1, [])]
    responses = ModelResponse(
        parts=[
            TextPart(
                "x", provider_details={"logprobs": [{"token": "b", "logprob": -0.2, "top_logprobs": [{"token": "c", "logprob": -2.0}]}]}
            )
        ]
    )
    assert token_logprobs(responses) == [("b", -0.2, [("c", -2.0)])]
    gemini = ModelResponse(
        parts=[TextPart("x")],
        provider_details={
            "logprobs": {
                "chosen_candidates": [{"token": "d", "log_probability": -0.3}],
                "top_candidates": [{"candidates": [{"token": "d", "log_probability": -0.3}, {"token": "e", "log_probability": -1.5}]}],
            }
        },
    )
    assert token_logprobs(gemini) == [("d", -0.3, [("d", -0.3), ("e", -1.5)])]
    assert token_logprobs(ModelResponse(parts=[TextPart("x")])) is None


def test_logprobs_requested_only_where_the_provider_returns_them():
    assert logprob_settings("openai:gpt-4.1") == {"openai_logprobs": True, "openai_top_logprobs": 5}
    assert logprob_settings("openai:gpt-5") == {}  # reasoning models do not return log-probabilities
    assert logprob_settings("anthropic:claude-opus-5-5") == {}


async def test_minicheck_skip_rule_only_for_tier_c_above_threshold():
    p, ra, inc, occ = _profile()
    claims = {c.text: c for fid in (ra, inc, occ) for c in claims_for_fact(p.facts[fid], profile=p)}
    occ_claim = next(t for t in claims if "occupation" in t)
    inc_claim = next(t for t in claims if "gross salary" in t)
    mc = FakeMiniCheck({occ_claim: 0.95, inc_claim: 0.97}, default=0.4)
    model, prompts = _verdicts_from(lambda t: "entailed")
    with entailment_agent.override(model=model):
        res = await check_entailment(p, [ra, inc, occ], transcript=_transcript(), pins=VersionPins(), minicheck=mc)
    o = res.facts[occ].checks[0]
    assert o.skipped_verifier and o.score_source == "minicheck" and o.entail_score == pytest.approx(0.95)
    i = res.facts[inc].checks[0]
    assert not i.skipped_verifier and i.minicheck_score == pytest.approx(0.97)  # tier A always goes to the verifier
    assert res.facts[inc].minicheck_score == pytest.approx(0.97)
    assert all("occupation" not in pr for pr in prompts)  # the tier C claim never reached the verifier
    assert len(prompts) == 2 and len(mc.calls) == 4
    assert all("client_primary:" in doc for doc, _ in mc.calls)


async def test_tier_c_below_threshold_still_goes_to_the_verifier():
    p, _, _, occ = _profile()
    model, prompts = _verdicts_from(lambda t: "neutral")
    with entailment_agent.override(model=model):
        res = await check_entailment(p, [occ], transcript=_transcript(), pins=VersionPins(), minicheck=FakeMiniCheck(default=0.5))
    assert not res.facts[occ].checks[0].skipped_verifier and res.facts[occ].checks[0].verdict == "neutral"
    assert len(prompts) == 1


async def test_offline_pins_report_not_run_and_bad_outputs_degrade():
    p, ra, _, _ = _profile()
    res = await check_entailment(p, [ra], transcript=_transcript(), pins=VersionPins())
    assert res.facts[ra].status == "not_run" and res.facts[ra].entail_score is None

    def junk(messages, info):
        return ModelResponse(parts=[TextPart(json.dumps({"verdicts": [{"claim_id": "zz", "verdict": "entailed", "reason": ""}]}))])

    with entailment_agent.override(model=FunctionModel(junk)):
        res = await check_entailment(p, [ra], transcript=_transcript(), pins=VersionPins())
    assert res.facts[ra].status == "not_run" and res.facts[ra].error and "Exceeded" in res.facts[ra].error


async def test_facts_without_evidence_or_hypotheses_are_not_run():
    b = ProfileBuilder()
    seed = b.seed("income.{p}.grossMonthly", "client", money(40000))
    p = b.build()
    res = await check_entailment(p, [seed, "missing#x"], transcript=_transcript(), pins=VersionPins())
    assert res.facts[seed].status == "not_run" and "missing#x" not in res.facts


def test_verifier_output_schema_is_strict_friendly():
    for schema, provider in ((anthropic_schema(EntailmentVerdicts), "anthropic"), (openai_schema(EntailmentVerdicts), "openai")):
        assert check_limits([schema_stats(schema)], provider) == []


async def test_minicheck_client_reads_yes_probability_from_vllm():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["url"], seen["body"] = str(request.url), body
        top = [
            {"token": "Yes", "logprob": math.log(0.8)},
            {"token": "No", "logprob": math.log(0.15)},
            {"token": "The", "logprob": math.log(0.05)},
        ]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "Yes"},
                        "logprobs": {"content": [{"token": "Yes", "logprob": math.log(0.8), "top_logprobs": top}]},
                    }
                ]
            },
        )

    client = MiniCheckClient("http://vllm:8000/v1", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    scores = await client.score([("client_primary: I want to retire at sixty", "The client's target retirement age is 60.")])
    assert scores == [pytest.approx(0.8 / 0.95)]
    assert seen["url"] == "http://vllm:8000/v1/chat/completions"
    assert (
        seen["body"]["max_tokens"] == 1 and seen["body"]["logprobs"] is True and seen["body"]["model"] == "bespokelabs/Bespoke-MiniCheck-7B"
    )
    assert "Claim: The client's target retirement age is 60." in seen["body"]["messages"][1]["content"]
    assert yes_probability({"message": {"content": "No"}}) == 0.0


async def test_a_local_pin_runs_the_registered_model_without_override():
    from advisor_intel import llm

    p, ra, _, _ = _profile()
    model, prompts = _verdicts_from(lambda t: "entailed")
    pin = llm.register_local_model("verify-entailment-test", model)
    try:
        res = await check_entailment(p, [ra], transcript=_transcript(), pins=VersionPins(verifier_model=pin))
    finally:
        llm.unregister_local_model("verify-entailment-test")
    assert res.facts[ra].status == "entailed" and len(prompts) == 1 and res.facts[ra].model == pin


async def test_a_deployment_that_rejects_logprobs_is_retried_without_them():
    from pydantic_ai.exceptions import ModelHTTPError

    p, ra, _, _ = _profile()
    seen = []

    def fn(messages, info: AgentInfo):
        seen.append(dict(info.model_settings or {}))
        if (info.model_settings or {}).get("openai_logprobs"):
            raise ModelHTTPError(status_code=400, model_name="verifier", body={"error": "logprobs are not supported"})
        claims = re.findall(r"^- (c\d+): ", str(messages[-1].parts[-1].content), flags=re.MULTILINE)
        return ModelResponse(
            parts=[TextPart(json.dumps({"verdicts": [{"claim_id": c, "verdict": "entailed", "reason": "r"} for c in claims]}))]
        )

    with entailment_agent.override(model=FunctionModel(fn)):
        res = await check_entailment(p, [ra], transcript=_transcript(), pins=VersionPins(), logprobs=True)
    assert [("openai_logprobs" in s) for s in seen] == [True, False]
    assert res.facts[ra].status == "entailed" and all(c.score_source == "verdict" for c in res.facts[ra].checks)


async def test_the_verifier_runs_on_the_pinned_prompt():
    """The entailment prompt is a versioned file (§13.8): the pin picks it, the run records it, the model receives it."""
    p, _ra, inc, _occ = _profile()
    seen: list[str] = []

    def fn(messages, info: AgentInfo):
        instr = getattr(messages[0], "instructions", None) or getattr(info, "instructions", None) or ""
        seen.append(str(instr))
        claims = re.findall(r"^- (c\d+): (.*)$", str(messages[-1].parts[-1].content), flags=re.MULTILINE)
        return ModelResponse(
            parts=[TextPart(json.dumps({"verdicts": [{"claim_id": c, "verdict": "entailed", "reason": "ok"} for c, _ in claims]}))]
        )

    with entailment_agent.override(model=FunctionModel(fn)):
        res = await check_entailment(p, [inc], transcript=_transcript(), pins=VersionPins(prompt_versions={"entailment": "v1"}))
        assert res.prompt_version == "v1"
        assert seen and "never evidence" in seen[-1] and "one verdict per claim" in seen[-1]
        latest = await check_entailment(p, [inc], transcript=_transcript(), pins=VersionPins())
    assert latest.prompt_version == "v1"  # the latest version when nothing is pinned
    with pytest.raises(FileNotFoundError):
        await check_entailment(p, [inc], transcript=_transcript(), pins=VersionPins(prompt_versions={"entailment": "v9"}))
