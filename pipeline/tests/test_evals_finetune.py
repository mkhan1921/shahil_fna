"""The fine-tuning track (§15.8): export splits and exclusions, the Tinker-compatible loops on a fake client, promotion."""

from __future__ import annotations

import json
from collections import defaultdict
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from advisor_intel.evals import finetune as FT
from advisor_intel.evals.extraction_eval import EvalReport
from advisor_intel.evals.gold import GoldFact, GoldValue
from advisor_intel.evals.rl_reward import CheckRow, HackingReport, RolloutFact
from advisor_intel.evals.synthetic import generate_calls
from advisor_intel.extract.reducer import apply_state_ops, make_ops
from advisor_intel.models.dists import Point, Quantity, Triangular
from advisor_intel.models.extraction import FORM_TYPES, ExtractionTask
from advisor_intel.models.facts import Hypothesis
from advisor_intel.models.runs import VersionPins
from advisor_intel.schema.slots import load_catalog
from tests.evals_fakes import FakePipeline

CAT = load_catalog()


def tasks_for(profile):
    by_group = defaultdict(lambda: (set(), set()))
    for f in profile.facts.values():
        if f.source != "call":
            continue
        slot = CAT.slot(f.slot_id)
        by_group[slot.group][0].add(slot.slot_id)
        by_group[slot.group][1].add(f.instance_key)
    return [
        ExtractionTask(task_id=f"t-{g}", slot_group=g, slot_ids=sorted(s), instance_keys=sorted(i, key=lambda x: x or ""))
        for g, (s, i) in by_group.items()
    ]


@pytest.fixture(scope="module")
def reviewed_calls():
    calls = generate_calls(12, seed=41)
    golds = {c.call_id: c.gold for c in calls}
    fake = FakePipeline(golds.__getitem__)
    out = []
    for i, c in enumerate(calls):
        call = SimpleNamespace(call_id=c.call_id, prior_document=c.prior_document, transcript=c.transcript())
        prof = fake(call, VersionPins()).profile
        call_facts = sorted(f.fact_id for f in prof.facts.values() if f.source == "call" and f.status in ("stated", "implied"))
        specs = [("CONFIRM", fid, None, {"review": {"status": "accepted", "reviewer_id": "adv"}}, "reviewer") for fid in call_facts[2:]]
        specs.append(("RETRACT", call_facts[0], None, {"reason": "misheard"}, "reviewer"))
        f1 = prof.facts[call_facts[1]]
        if f1.value is not None and f1.value.unit == "ZAR":
            specs.append(
                (
                    "UPDATE",
                    call_facts[1],
                    None,
                    {
                        "replace_hypotheses": [
                            Hypothesis(
                                value=Quantity(dist=Triangular(low=1000, mode=2000, high=3000), unit="ZAR", period=f1.value.period),
                                weight=1,
                                rationale="edit",
                            )
                        ],
                        "review": {"status": "edited", "reviewer_id": "adv"},
                    },
                    "reviewer",
                )
            )
        else:
            specs.append(("CONFIRM", call_facts[1], None, {"review": {"status": "accepted"}}, "reviewer"))
        prof = apply_state_ops(prof, make_ops(prof, specs)).profile
        out.append(
            FT.ReviewedCall(
                call_id=c.call_id,
                client_id=f"client-{i // 2}",
                profile=prof,
                tasks=tasks_for(prof),
                transcript=c.transcript(),
                is_gold=i % 3 == 0,
                heldout=i == 11,
                disputed_fact_ids=[call_facts[3]] if i == 4 else [],
            )
        )
    return out


def render(call, task, stage):
    return [{"role": "system", "content": f"extract_{stage} v3"}, {"role": "user", "content": f"{call.call_id} {task.slot_group}"}]


def test_export_splits_by_client_and_excludes_heldout(reviewed_calls):
    ex, st = FT.export_training_data(reviewed_calls, render_prompt=render, heldout_ids={"syn-41-0010"})
    assert st.skipped_heldout_calls == 2 and not ({"syn-41-0010", "syn-41-0011"} & {e.call_id for e in ex})
    by_client = defaultdict(set)
    for e in ex:
        by_client[e.client_id].add(e.split)
    assert all(len(s) == 1 for s in by_client.values())  # no client in two splits
    disputed = reviewed_calls[4]
    disputed_group = CAT.slot(disputed.profile.facts[disputed.disputed_fact_ids[0]].slot_id).group
    assert st.skipped_disputed_tasks == 1
    assert not any(e.call_id == disputed.call_id and e.group == disputed_group for e in ex)
    assert any(e.call_id == disputed.call_id for e in ex)  # the call's other tasks are kept
    assert {e.weight for e in ex if e.call_id == "syn-41-0000"} == {3.0} and {e.weight for e in ex if e.call_id == "syn-41-0001"} == {1.0}
    assert st.gold_examples == sum(e.weight == 3.0 for e in ex) and st.examples == len(ex)
    e = ex[0]
    assert e.messages[0]["content"].startswith("extract_") and e.messages[-1]["role"] == "assistant"
    assert json.loads(e.messages[-1]["content"]) == e.target
    assert {"objects", "statuses", "values"} >= {x.stage for x in ex}


def test_targets_teach_not_discussed_and_carry_edits(reviewed_calls):
    c = reviewed_calls[1]
    rejected = next(f for f in c.profile.facts.values() if f.review.status == "rejected")
    task = next(t for t in c.tasks if CAT.slot(rejected.slot_id).slot_id in t.slot_ids)
    targets = FT.stage_targets(c, task)
    st = next(s for s in targets["statuses"] if s["fna_path"] == rejected.fna_path)
    assert st == {"fna_path": rejected.fna_path, "status": "not_discussed", "segment_ids": []}
    assert not any(v["fna_path"] == rejected.fna_path for v in targets["values"])
    for call in reviewed_calls:
        edited = [f for f in call.profile.facts.values() if f.review.status == "edited"]
        if not edited:
            continue
        f = edited[0]
        task = next(t for t in call.tasks if CAT.slot(f.slot_id).slot_id in t.slot_ids)
        v = next(v for v in FT.stage_targets(call, task)["values"] if v["fna_path"] == f.fna_path)
        assert v["hypotheses"][0]["intermediate"] == {
            **v["hypotheses"][0]["intermediate"],
            "type": "AMOUNT_RANGE",
            "low": 1000,
            "high": 3000,
        }
        assert v["said"]  # evidence quotes travel with the target
        break
    else:
        pytest.fail("no edited fact in the fixture")


def test_form_from_quantity_round_trips_through_the_form_models():
    cases = [
        ("income.{p}.grossMonthly", Quantity(dist=Point(value=45000), unit="ZAR", period="monthly"), "AMOUNT_EXACT"),
        ("retirement.{p}.retirementAge", Quantity(dist=Triangular(low=60, mode=62, high=65), unit="age"), "AGE"),
        ("{p}.dateOfBirth", Quantity(dist=Point(value="1986-04-12"), unit="date"), "DATE_ABS"),
        ("goals[].targetYear", Quantity(dist=Point(value=2029), unit="date"), "DATE_ABS"),
        ("liabilities[].interestRate", Quantity(dist=Point(value=0.1175), unit="pct"), "RATE"),
        ("{p}.smoker", Quantity(dist=Point(value=False), unit="none"), "BOOLEAN"),
        ("estate.{p}.hasWill", Quantity(dist=Point(value="yes"), unit="none"), "CATEGORY"),
        ("dependants[].tertiaryYears", Quantity(dist=Point(value=4), unit="years"), "DURATION_FROM_ANCHOR"),
        ("{p}.occupation", Quantity(dist=Point(value="teacher"), unit="none"), "TEXT"),
    ]
    for slot_id, q, kind in cases:
        form = FT.form_from_quantity(CAT.slot(slot_id), q)
        assert form["type"] == kind and FORM_TYPES[kind].model_validate(form)
    dob = FT.form_from_quantity(CAT.slot("{p}.dateOfBirth"), cases[2][1])
    assert (dob["year"], dob["month"], dob["day"]) == (1986, 4, 12)


def test_write_jsonl(tmp_path, reviewed_calls):
    ex, _ = FT.export_training_data(reviewed_calls, render_prompt=render)
    paths = FT.write_jsonl(ex, tmp_path)
    rows = [json.loads(line) for line in paths["train"].read_text().splitlines()]
    assert rows and set(rows[0]) == {"messages", "weight", "metadata"} and rows[0]["metadata"]["stage"] in ("objects", "statuses", "values")


# --------------------------------------------------------------------------- a fake Tinker-compatible backend


class Tok:
    def encode(self, text):
        return list(text.encode())

    def decode(self, tokens):
        return bytes(tokens).decode(errors="ignore")


class Fut:
    def __init__(self, v):
        self.v = v

    def result(self):
        return self.v


class FakeBackend:
    """A policy over a fixed set of completions per prompt; optimiser steps move probability toward completions
    with positive advantage, so the loop's mechanics (sampling, rewards, KL, advantages, data) are observable."""

    def __init__(self, completions):
        self.completions = completions  # prompt text → list of completions
        self.logits = defaultdict(float)
        self.pending = defaultdict(float)
        self.batches, self.steps, self.checkpoints = [], [], []
        self.rng = np.random.default_rng(0)

    # TrainingClient
    def get_tokenizer(self):
        return Tok()

    def forward_backward(self, data, loss_fn):
        self.batches.append((loss_fn, data))
        for d in data:
            adv = [a for a in d.loss_fn_inputs.get("advantages", []) if a != 0.0]
            text = Tok().decode(d.loss_fn_inputs["target_tokens"])
            if adv:
                for comps in self.completions.values():
                    for c in comps:
                        if text.endswith(c):
                            self.pending[c] += adv[0]
        return Fut({"loss": 1.0 / (1 + len(self.batches))})

    def optim_step(self, params):
        self.steps.append(params.learning_rate)
        for c, a in self.pending.items():
            self.logits[c] += a
        self.pending.clear()
        return Fut({})

    def save_weights_and_get_sampling_client(self, name):
        self.checkpoints.append(name)
        return self

    # SamplingClient
    def sample(self, prompt, num_samples, sampling_params):
        text = Tok().decode(prompt.to_ints())
        comps = next(v for k, v in self.completions.items() if k in text)
        w = np.exp([self.logits[c] for c in comps])
        idx = self.rng.choice(len(comps), size=num_samples, p=w / w.sum())
        return Fut(
            SimpleNamespace(
                sequences=[SimpleNamespace(tokens=Tok().encode(comps[i]), logprobs=[-0.5] * len(comps[i].encode())) for i in idx]
            )
        )

    def compute_logprobs(self, prompt):
        return Fut([None] + [-0.6] * (len(prompt.to_ints()) - 1))


GOOD = json.dumps(
    {
        "facts": [
            {
                "slot_id": "retirement.{p}.retirementAge",
                "instance_key": "client",
                "status": "stated",
                "value": 65,
                "quote": "retire at 65",
                "segment_id": "s1",
                "self_score": 0.9,
            }
        ]
    }
)
INVENTED = json.dumps(
    {
        "facts": [
            {
                "slot_id": "retirement.{p}.retirementAge",
                "instance_key": "client",
                "status": "stated",
                "value": 65,
                "quote": "retire at 65",
                "segment_id": "s1",
                "self_score": 0.9,
            },
            {
                "slot_id": "estate.{p}.hasWill",
                "instance_key": "client",
                "status": "stated",
                "value": "yes",
                "quote": "a will",
                "segment_id": "s1",
                "self_score": 0.9,
            },
        ]
    }
)
DODGE = json.dumps(
    {"facts": [{"slot_id": "retirement.{p}.retirementAge", "instance_key": "client", "status": "not_discussed", "value": None}]}
)


def postprocess(text, prompt):
    try:
        facts = json.loads(text)["facts"]
    except (ValueError, KeyError):
        return []
    out = []
    for f in facts:
        unit = "age" if f["slot_id"].startswith("retirement") else "none"
        out.append(
            RolloutFact(
                slot_id=f["slot_id"],
                instance_key=f["instance_key"],
                status=f["status"],
                value=None if f.get("value") is None else Quantity(dist=Point(value=f["value"]), unit=unit),
                quotes=[(f["segment_id"], f["quote"])] if f.get("quote") else [],
                self_score=f.get("self_score"),
            )
        )
    return out


def rl_prompt(pid="p1", call_id="c1"):
    return FT.RLPrompt(
        prompt_id=pid,
        call_id=call_id,
        group="retirement",
        messages=[{"role": "user", "content": f"extract retirement {pid}"}],
        reviewed=[
            GoldFact(
                slot_id="retirement.{p}.retirementAge", instance_key="client", status="stated", value=GoldValue(kind="numeric", p50=65)
            )
        ],
        segments={"s1": "I want to retire at 65"},
    )


def test_grpo_loop_improves_reward_on_a_fake_backend():
    backend = FakeBackend({"extract retirement p1": [GOOD, INVENTED, DODGE], "extract retirement p2": [GOOD, INVENTED, DODGE]})
    cfg = FT.GRPOConfig(steps=12, prompts_per_step=2, group_size=8, learning_rate=1e-5, kl_coef=0.01, seed=3)
    seen = []
    res = FT.grpo_train(
        [rl_prompt("p1"), rl_prompt("p2", "c2")],
        training_client=backend,
        reference_client=backend,
        postprocess=postprocess,
        config=cfg,
        on_step=seen.append,
    )
    assert len(res.steps) == 12 and seen == res.steps
    first, last = res.steps[0].mean_reward, np.mean([m.mean_reward for m in res.steps[-3:]])
    assert last > first  # probability moved to the grounded, honest completion
    loss_fn, data = backend.batches[0]
    assert loss_fn == "importance_sampling" and len(data) == 16  # 8 samples × 2 prompts
    d = data[0].loss_fn_inputs
    assert len(d["target_tokens"]) == len(d["logprobs"]) == len(d["advantages"]) == data[0].model_input.length
    assert res.steps[0].mean_kl == pytest.approx(0.1)  # −0.5 − (−0.6) per token
    assert backend.checkpoints[0] == "grpo-0" and res.checkpoint.startswith("grpo-")
    assert backend.steps and set(backend.steps) == {1e-5}
    with pytest.raises(ValueError, match="held-out"):
        FT.grpo_train([rl_prompt()], training_client=backend, reference_client=backend, postprocess=postprocess, heldout_ids={"c1"})


def test_groups_without_signal_are_skipped():
    backend = FakeBackend({"extract retirement p1": [GOOD]})
    res = FT.grpo_train(
        [rl_prompt()], training_client=backend, reference_client=backend, postprocess=postprocess, config=FT.GRPOConfig(steps=2)
    )
    assert all(m.groups_with_signal == 0 and m.checkpoint == "" for m in res.steps) and not backend.batches


def test_sft_masks_the_prompt_and_weights_gold(reviewed_calls):
    ex, _ = FT.export_training_data(reviewed_calls, render_prompt=render)
    backend = FakeBackend({})
    metrics = FT.sft_train(ex[:20], backend, batch_size=8, learning_rate=2e-4)
    assert metrics and metrics[-1].checkpoint == "sft-final" and backend.batches[0][0] == "cross_entropy"
    d = backend.batches[0][1][0].loss_fn_inputs
    assert d["weights"][0] == 0.0 and max(d["weights"]) in (1.0, 3.0) and len(d["weights"]) == len(d["target_tokens"])


def _report(group_scores, ece):
    return EvalReport(summary={"by_group": {g: dict(zip(FT.PROMOTION_METRICS, s)) for g, s in group_scores.items()}, "ece_by_group": ece})


def test_promotion_check_per_group(tmp_path):
    inc = _report({"income": (0.9, 0.9, 0.8), "retirement": (0.9, 0.9, 0.8)}, {"income": 0.05, "retirement": 0.05})
    cand = _report({"income": (0.92, 0.9, 0.85), "retirement": (0.95, 0.85, 0.9)}, {"income": 0.04, "retirement": 0.04})
    res = {p.group: p for p in FT.promotion_check(cand, inc, ["income", "retirement"])}
    assert res["income"].switch and not res["retirement"].switch and "slot_recall" in res["retirement"].reasons[0]
    worse_ece = _report({"income": (0.92, 0.9, 0.85)}, {"income": 0.07})
    assert not FT.promotion_check(worse_ece, inc, ["income"])[0].switch
    bad = HackingReport(passed=False, not_discussed=[CheckRow(key="income", candidate=0.3, reference=0.2, passed=False)], range_width=[])
    assert not FT.promotion_check(cand, inc, ["income"], hacking=bad)[0].switch
    assert not FT.promotion_check(cand, inc, ["income"], human_review_signed_off=False)[0].switch
    path = FT.write_group_routing(list(res.values()), "vllm:qwen-27b-lora-7", tmp_path / "routing.yaml")
    assert yaml.safe_load(path.read_text()) == {"extraction_groups": {"income": "vllm:qwen-27b-lora-7"}}
    assert FT.write_group_routing([res["retirement"]], "m", tmp_path / "none.yaml") is None
