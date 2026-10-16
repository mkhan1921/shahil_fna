"""Follow-up drafting (§10.5): merging, range-or-choice, the client-safe validator, ordering, caps, templates."""

from __future__ import annotations

import json
import re

from pydantic_ai.messages import ModelResponse, RetryPromptPart, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from advisor_intel.models.dists import Categorical, Triangular
from advisor_intel.models.runs import VersionPins
from advisor_intel.schema.compiler import anthropic_schema, check_limits, openai_schema, schema_stats
from advisor_intel.voi.clarify import ClarifyDecision
from advisor_intel.voi.followups import Drafts, banned_terms, client_label, draft_followups, followup_agent
from advisor_intel.voi.priority import PriorityItem

from .verify_factories import CATALOG, ProfileBuilder, ev, qty


def _setup(n_extra: int = 0):
    b = ProfileBuilder(spouse=True)
    b.entity("person.thandi", "person", "Thandi")
    ids = {
        "age": b.add(
            "retirement.{p}.retirementAge", "client", qty(Triangular(low=58, mode=62, high=66), "age"), evidence=[ev("s1", "sixty-ish")]
        ),
        "start": b.add("dependants[].tertiaryStartAge", "person.thandi", qty(Triangular(low=18, mode=19, high=21), "age")),
        "cost": b.add(
            "dependants[].tertiaryCostAnnual", "person.thandi", qty(Triangular(low=80000, mode=150000, high=400000), "ZAR", "annual")
        ),
        "emp": b.add("{p}.employmentType", "spouse", qty(Categorical(probs={"salaried": 0.5, "contract": 0.5}), "none")),
    }
    for i in range(n_extra):
        b.entity(f"liability.l{i}", "liability", f"Loan {i}")
        ids[f"x{i}"] = b.add("liabilities[].balance", f"liability.l{i}", qty(Triangular(low=1000, mode=5000, high=9000), "ZAR", "once"))
    p = b.build()
    items, decisions = [], {}
    for n, (k, fid) in enumerate(ids.items()):
        change = 0.3 - 0.01 * n
        slot = fid.split("#")[0]
        items.append(
            PriorityItem(
                fact_id=fid,
                slot_id=slot,
                tier=CATALOG.slot(slot).sensitivity_tier,
                status="stated",
                p_correct=0.8,
                i_content=change,
                i_extract=0.0,
                i_fna=change,
                i_pos=0.0,
                forced=0.0,
                priority=change,
                why=f"why-{k}",
            )
        )
        decisions[fid] = ClarifyDecision(fact_id=fid, action="followup", rule="content", expected_pos_change=change)
    return p, ids, items, decisions


def _model(responses):
    """A FunctionModel answering with each response in turn; it records the prompts it saw."""
    seen: list[list] = []

    def fn(messages, info: AgentInfo):
        seen.append(messages)
        body = responses[min(len(seen) - 1, len(responses) - 1)]
        return ModelResponse(parts=[TextPart(json.dumps({"followups": body(messages) if callable(body) else body}))])

    return FunctionModel(fn), seen


def _ids_in_prompt(messages):
    text = str(messages[0].parts[-1].content)
    return dict(re.findall(r"^- (i\d+): (.*?)\.", text, flags=re.M))


async def test_merges_orders_by_expected_change_and_uses_vo_i_for_why():
    p, ids, items, decisions = _setup()

    def answer(messages):
        labels = _ids_in_prompt(messages)
        thandi = [i for i, label in labels.items() if "Thandi" in label]
        rest = [i for i in labels if i not in thandi]
        return [
            {
                "item_ids": thandi,
                "question": "When might Thandi start studying, and roughly what would a year cost: R100k, R250k or more?",
                "answer_type": "AMOUNT_RANGE",
            }
        ] + [
            {
                "item_ids": [i],
                "question": f"Would you say {i} is closer to A or B?",
                "answer_type": "CATEGORY" if "employment" in labels[i] else "AGE",
            }
            for i in rest
        ]

    model, seen = _model([answer])
    with followup_agent.override(model=model):
        res = await draft_followups(p, decisions, items, catalog=CATALOG, pins=VersionPins())
    assert res.drafted_by == "model" and len(seen) == 1
    merged = next(f for f in res.followups if len(f.fact_ids) == 2)
    assert set(merged.fact_ids) == {ids["start"], ids["cost"]} and merged.answer_type == "AMOUNT_RANGE"
    assert merged.expected_pos_change == 0.29 and merged.why == "why-start"  # the merged items' largest change and its why line
    assert [f.expected_pos_change for f in res.followups] == sorted((f.expected_pos_change for f in res.followups), reverse=True)
    assert res.followups[0].fact_ids == [ids["age"]] and res.followups[0].why == "why-age"
    assert all(f.status == "open" and f.followup_id.startswith("fu_") for f in res.followups)
    prompt = str(seen[0][0].parts[-1].content)
    assert (
        '"sixty-ish"' in prompt and "your target retirement age" in prompt and "the age at the start of tertiary study for Thandi" in prompt
    )


async def test_validator_rejects_client_text_about_probabilities_confidence_or_the_model():
    p, ids, items, decisions = _setup()
    bad = [
        {
            "item_ids": ["i1"],
            "question": "Our model has low confidence: is the probability of retiring at 60 or 65 higher?",
            "answer_type": "AGE",
        }
    ]
    good = [{"item_ids": ["i1"], "question": "Would you say 60, 63 or 65 is most realistic for retiring?", "answer_type": "AGE"}]
    model, seen = _model([bad, good])
    with followup_agent.override(model=model):
        res = await draft_followups(p, decisions, items, catalog=CATALOG, pins=VersionPins())
    assert len(seen) == 2
    retry = [part for m in seen[1] for part in getattr(m, "parts", []) if isinstance(part, RetryPromptPart)]
    assert (
        retry and "confidence" in str(retry[-1].content) and "model" in str(retry[-1].content) and "probability" in str(retry[-1].content)
    )
    assert [f.question for f in res.followups] == [good[0]["question"]]


async def test_validator_requires_known_ids_and_a_range_or_choice():
    p, ids, items, decisions = _setup()
    point_q = [{"item_ids": ["i1"], "question": "At what age will you retire?", "answer_type": "AGE"}]
    unknown = [{"item_ids": ["i99"], "question": "Would you say 60 or 65?", "answer_type": "AGE"}]
    fine = [{"item_ids": ["i1"], "question": "Roughly when: 60, 63 or 65?", "answer_type": "AGE"}]
    model, seen = _model([point_q, unknown, fine])
    with followup_agent.override(model=model):
        res = await draft_followups(p, decisions, items, catalog=CATALOG, pins=VersionPins())
    assert len(seen) == 3 and res.followups[0].question == fine[0]["question"]
    texts = [str(part.content) for m in seen[2] for part in getattr(m, "parts", []) if isinstance(part, RetryPromptPart)]
    assert any("range or a choice" in t for t in texts) and any("i99" in t for t in texts)


async def test_cap_of_eight_questions():
    p, ids, items, decisions = _setup(n_extra=8)

    def answer(messages):
        return [
            {"item_ids": [i], "question": f"Would you say {i} is between A and B?", "answer_type": "AMOUNT_RANGE"}
            for i in _ids_in_prompt(messages)
        ]

    model, _ = _model([answer])
    with followup_agent.override(model=model):
        res = await draft_followups(p, decisions, items, catalog=CATALOG, pins=VersionPins())
    assert len(res.followups) == 8 and res.followups[0].fact_ids == [ids["age"]]


async def test_templates_when_the_model_is_a_stub():
    p, ids, items, decisions = _setup()
    res = await draft_followups(p, decisions, items, catalog=CATALOG, pins=VersionPins())  # "test" pins: no live model
    assert res.drafted_by == "template"
    by_fact = {fid: f for f in res.followups for fid in f.fact_ids}
    assert by_fact[ids["start"]] is by_fact[ids["cost"]]  # merged per person and topic
    age_q = by_fact[ids["age"]].question
    assert age_q.startswith("Roughly where would you put your target retirement age: closer to 60 or to 64, or somewhere in between?")
    assert by_fact[ids["emp"]].question.startswith("Which of these best describes your spouse's employment type:")
    assert all(not banned_terms(f.question) for f in res.followups)


def test_banned_terms_and_labels():
    assert banned_terms("What are the odds you retire early?") == ["odds"]
    assert banned_terms("How confident is the model?") == ["confident", "model"]
    assert banned_terms("There is a 30% chance of that") == ["% chance"]
    assert banned_terms("Would you say 60 or 65?") == []
    p, ids, _, _ = _setup()
    assert client_label(p.facts[ids["emp"]], p, CATALOG) == "your spouse's employment type"
    assert client_label(p.facts[ids["cost"]], p, CATALOG) == "the tertiary study cost for Thandi"


def test_output_schema_is_strict_friendly():
    for schema, provider in ((anthropic_schema(Drafts), "anthropic"), (openai_schema(Drafts), "openai")):
        assert check_limits([schema_stats(schema)], provider) == []
