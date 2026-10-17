"""The fine-tuning track (spec §15.8) and the GRPO loop of the RL track (§15.9).

Training data: for every reviewed call and every extraction task of its run, the exact production stage prompt
(rendered by the injected production renderer, same window and context pack) paired with the reviewed stage output
— objects, field statuses and field values. Accepted and edited facts are targets with their evidence quotes;
rejected facts teach ``not_discussed``; adjudicated gold calls weigh 3×; a task with an unresolved review
disagreement (or an unreviewed fact) is left out. Splits are by client, never by call or fact, and the 20 held-out
gold calls are never exported.

Training runs through a Tinker-compatible API (managed Tinker, SkyRL's Tinker backend or Baseten Loops): the loops
below use only ``create_lora_training_client``, ``get_tokenizer``, ``forward_backward``, ``optim_step``,
``save_weights_and_get_sampling_client``, ``sample`` and ``compute_logprobs``, with Tinker's ``ModelInput``,
``Datum``, ``AdamParams`` and ``SamplingParams`` types (local stand-ins when the ``tinker`` package is absent), so
they run against a fake client in tests. ``grpo_train`` samples 8 outputs per prompt, scores them with
``rl_reward.reward``, subtracts a KL penalty toward the reference (M6) checkpoint, normalises advantages within each
group and takes an importance-sampling policy-gradient step.

Promotion is per slot group and never automatic: the candidate must match or beat the incumbent on status accuracy,
slot recall and value coverage with ECE no worse (RL additionally needs the reward-hacking checks and a person's
sign-off on the sampled outputs).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np
from pydantic import BaseModel

from .. import stats
from ..models.extraction import FORM_TYPES, ExtractionTask
from ..models.facts import Fact
from ..models.profile import Profile
from ..models.transcript import Transcript
from ..schema.slots import Catalog, SlotDef, load_catalog
from ._runtime import stable_hash, unit_hash
from .datasets import heldout_call_ids
from .extraction_eval import EvalReport
from .gold import GoldFact
from .rl_reward import HackingReport, RewardBreakdown, RolloutFact, reward

Stage = Literal["objects", "statuses", "values"]
Split = Literal["train", "val", "test"]
GOLD_WEIGHT = 3.0
LIST_GROUPS_STAGES: tuple[Stage, ...] = ("objects", "statuses", "values")
SINGLE_STAGES: tuple[Stage, ...] = ("statuses", "values")


# --------------------------------------------------------------------------- training-data export (§15.8)


class ReviewedCall(BaseModel):
    """A reviewed call: the profile after review, the run's plan and the transcript the prompts were built from."""

    call_id: str
    client_id: str
    profile: Profile
    tasks: list[ExtractionTask]
    transcript: Transcript | None = None
    is_gold: bool = False  # an adjudicated gold call: weighted 3×
    heldout: bool = False  # one of the 20 held-out gold calls: never exported
    disputed_fact_ids: list[str] = []  # facts with an unresolved review disagreement
    prompt_versions: dict[str, str] = {}


PromptRenderer = Callable[[ReviewedCall, ExtractionTask, Stage], "list[dict[str, str]] | str"]


class TrainingExample(BaseModel):
    id: str
    call_id: str
    client_id: str
    group: str
    task_id: str
    stage: Stage
    split: Split
    weight: float
    messages: list[dict[str, str]]
    target: Any


class ExportStats(BaseModel):
    examples: int = 0
    by_split: dict[str, int] = {}
    skipped_heldout_calls: int = 0
    skipped_disputed_tasks: int = 0
    skipped_unreviewed_tasks: int = 0
    gold_examples: int = 0


def split_of(client_id: str, ratios: tuple[float, float, float] = (0.8, 0.1, 0.1)) -> Split:
    """Split by client (§15.8): a client's calls all land in one split, whatever the call order."""
    u = unit_hash(f"finetune-split/{client_id}")
    return "train" if u < ratios[0] else ("val" if u < ratios[0] + ratios[1] else "test")


def _likelihood_word(p: float) -> str:
    return "definitely" if p >= 0.9 else ("probably" if p >= 0.6 else ("might" if p >= 0.3 else "unlikely"))


def form_from_quantity(slot: SlotDef, q: Any) -> dict[str, Any]:
    """An intermediate form (§8.1) that states a reviewed value, for edited facts whose spoken form was wrong."""
    d = q.dist
    vt = slot.value_type
    period = getattr(q, "period", "none")
    currency = q.unit if q.unit in ("ZAR", "USD", "GBP", "EUR") else "ZAR"
    basis = getattr(q, "basis", "na")
    if vt == "amount":
        if d.kind == "point":
            form = {
                "type": "AMOUNT_EXACT",
                "value": float(stats.as_number(d.value)),
                "currency": currency,
                "period": period,
                "basis": basis,
            }
        elif d.kind == "triangular":
            form = {"type": "AMOUNT_RANGE", "low": d.low, "high": d.high, "currency": currency, "period": period, "basis": basis}
        else:
            form = {
                "type": "AMOUNT_APPROX",
                "value": stats.median(d),
                "approx_word": "about",
                "currency": currency,
                "period": period,
                "basis": basis,
            }
    elif vt == "age":
        if d.kind == "triangular":
            form = {"type": "AGE", "form": "range", "value": 0, "low": d.low, "high": d.high, "vague_word": ""}
        else:
            form = {"type": "AGE", "form": "value", "value": round(stats.median(d), 1), "low": 0, "high": 0, "vague_word": ""}
    elif vt in ("years", "months"):
        form = {
            "type": "DURATION_FROM_ANCHOR",
            "form": "value",
            "value": round(stats.median(d), 2),
            "vague_word": "",
            "unit": vt,
            "anchor": "call",
        }
    elif vt in ("date", "year"):
        y = stats.median(d) if not (d.kind == "point" and isinstance(d.value, str)) else stats.iso_to_year(d.value)
        iso = stats.year_to_iso(y)
        form = {
            "type": "DATE_ABS",
            "year": int(iso[:4]),
            "month": 0 if vt == "year" else int(iso[5:7]),
            "day": 0 if vt == "year" else int(iso[8:10]),
        }
    elif vt == "rate":
        form = {
            "type": "RATE",
            "form": "value",
            "value": stats.median(d),
            "vague_word": "",
            "real_or_nominal": getattr(q, "real_or_nominal", "na"),
        }
    elif vt == "probability":
        p = stats.p_true(d) if stats.is_boolean(d) else stats.mean(d)
        form = {"type": "PROBABILITY_EVENT", "event": slot.label or slot.slot_id, "likelihood_word": _likelihood_word(p)}
    elif vt == "boolean":
        form = {"type": "BOOLEAN", "value": stats.p_true(d) >= 0.5, "certainty_word": ""}
    elif vt in ("category", "count"):
        form = {"type": "CATEGORY", "label": stats.mode_label(d), "certainty_word": ""}
    else:
        form = {"type": "TEXT", "text": str(getattr(d, "value", ""))}
    return FORM_TYPES[form["type"]].model_validate(form).model_dump(mode="json")


def _task_facts(call: ReviewedCall, task: ExtractionTask, catalog: Catalog) -> list[Fact]:
    slots = set(task.slot_ids)
    insts = {i for i in task.instance_keys if i is not None}
    out = []
    for f in call.profile.facts.values():
        generic = catalog.slot(f.slot_id).slot_id
        if generic not in slots:
            continue
        if insts and f.instance_key is not None and f.instance_key not in insts:
            continue
        out.append(f)
    return out


def _reviewed(f: Fact) -> bool:
    return f.review.status in ("accepted", "edited", "rejected") or f.auto_accepted or f.source == "reviewer"


def stage_targets(call: ReviewedCall, task: ExtractionTask, *, catalog: Catalog | None = None) -> dict[str, Any] | None:
    """The reviewed stage outputs for one task, or None when a disputed or unreviewed call-extracted fact is in it."""
    catalog = catalog or load_catalog()
    facts = _task_facts(call, task, catalog)
    disputed = set(call.disputed_fact_ids)
    if any(f.fact_id in disputed for f in facts):
        raise _Disputed()
    extracted = [f for f in facts if f.source in ("call", "derived", "followup", "reviewer")]
    if any(not _reviewed(f) and f.status in ("stated", "implied", "contradicted") for f in extracted):
        raise _Unreviewed()
    statuses, values, objects = [], [], {}
    for f in sorted(extracted, key=lambda x: x.fact_id):
        slot = catalog.slot(f.slot_id)
        segs = sorted({e.segment_id for e in f.evidence if e.role in ("supports", "revises")})
        if f.review.status == "rejected":
            statuses.append({"fna_path": f.fna_path, "status": "not_discussed", "segment_ids": []})  # rejections teach not_discussed
            continue
        statuses.append({"fna_path": f.fna_path, "status": f.status, "segment_ids": segs if f.status != "not_discussed" else []})
        if f.instance_key and slot.per_entity and f.instance_key in call.profile.entities:
            e = call.profile.entities[f.instance_key]
            obj = objects.setdefault(
                f.instance_key, {"entity_id": e.entity_id, "entity_type": e.type, "name": e.canonical_name, "segment_ids": set()}
            )
            obj["segment_ids"].update(segs)
        if f.status in ("stated", "implied", "contradicted") and f.value is not None:
            original = (f.normalization or {}).get("form") if f.review.status != "edited" else None
            if isinstance(original, dict) and original.get("type") in FORM_TYPES:
                try:
                    form = FORM_TYPES[original["type"]].model_validate(original).model_dump(mode="json")
                except ValueError:
                    form = form_from_quantity(slot, f.value)
            else:
                form = form_from_quantity(slot, f.value)
            quotes = [e.quote for e in f.evidence if e.role in ("supports", "revises")]
            values.append(
                {
                    "fna_path": f.fna_path,
                    "hypotheses": [{"intermediate": form, "weight": 1.0, "rationale": "reviewed value", "revises": False}],
                    "hedges": [h.term for h in f.hedges],
                    "speaker_attribution": f.speaker_attribution,
                    "said": quotes[0] if quotes else f.said,
                    "interpretation": f.interpretation or "reviewed by the adviser",
                    "self_score": round(float(f.p_correct), 3),
                }
            )
    for slot_id in task.slot_ids:  # every field the task asked about has a status
        slot = catalog.slot(slot_id)
        for inst in task.instance_keys or [None]:
            path = slot.fna_path_for(inst if slot.per_person else None) if not slot.per_entity else None
            if path and not any(s["fna_path"] == path for s in statuses):
                statuses.append({"fna_path": path, "status": "not_discussed", "segment_ids": []})
    return {
        "objects": [dict(o, segment_ids=sorted(o["segment_ids"])) for o in objects.values()],
        "statuses": sorted(statuses, key=lambda s: s["fna_path"]),
        "values": values,
    }


class _Disputed(Exception):
    pass


class _Unreviewed(Exception):
    pass


def _messages(rendered: list[dict[str, str]] | str) -> list[dict[str, str]]:
    return [{"role": "user", "content": rendered}] if isinstance(rendered, str) else [dict(m) for m in rendered]


def export_training_data(
    calls: Iterable[ReviewedCall],
    *,
    render_prompt: PromptRenderer,
    heldout_ids: Iterable[str] | None = None,
    gold_weight: float = GOLD_WEIGHT,
    ratios: tuple[float, float, float] = (0.8, 0.1, 0.1),
    catalog: Catalog | None = None,
) -> tuple[list[TrainingExample], ExportStats]:
    """Supervised examples from reviewed calls (§15.8). ``heldout_ids`` defaults to the gold dataset's held-out calls
    (``datasets.heldout_call_ids``), so a reviewed call whose ``heldout`` flag was not set is still kept out."""
    catalog = catalog or load_catalog()
    held = set(heldout_ids) if heldout_ids is not None else heldout_call_ids()
    examples: list[TrainingExample] = []
    st = ExportStats()
    for c in calls:
        if c.heldout or c.call_id in held:
            st.skipped_heldout_calls += 1
            continue
        split = split_of(c.client_id, ratios)
        for task in c.tasks:
            try:
                targets = stage_targets(c, task, catalog=catalog)
            except _Disputed:
                st.skipped_disputed_tasks += 1
                continue
            except _Unreviewed:
                st.skipped_unreviewed_tasks += 1
                continue
            assert targets is not None
            stages = (
                LIST_GROUPS_STAGES if catalog.groups.get(task.slot_group) and catalog.groups[task.slot_group].list_entity else SINGLE_STAGES
            )
            for stage in stages:
                target = targets[stage]
                msgs = _messages(render_prompt(c, task, stage)) + [{"role": "assistant", "content": json.dumps(target, sort_keys=True)}]
                examples.append(
                    TrainingExample(
                        id=stable_hash([c.call_id, task.task_id, stage]),
                        call_id=c.call_id,
                        client_id=c.client_id,
                        group=task.slot_group,
                        task_id=task.task_id,
                        stage=stage,
                        split=split,
                        weight=gold_weight if c.is_gold else 1.0,
                        messages=msgs,
                        target=target,
                    )
                )
                st.gold_examples += c.is_gold
    leaked = {e.call_id for e in examples} & held
    if leaked:  # pragma: no cover - guarded above; kept as an invariant
        raise AssertionError(f"held-out calls in the training export: {sorted(leaked)}")
    st.examples = len(examples)
    st.by_split = {s: sum(e.split == s for e in examples) for s in ("train", "val", "test")}
    return examples, st


def write_jsonl(examples: Sequence[TrainingExample], out_dir: Path) -> dict[str, Path]:
    """One chat-format JSONL file per split (messages + weight + metadata)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for split in ("train", "val", "test"):
        p = out_dir / f"{split}.jsonl"
        rows = [e for e in examples if e.split == split]
        p.write_text(
            "".join(
                json.dumps(
                    {
                        "messages": e.messages,
                        "weight": e.weight,
                        "metadata": {
                            "call_id": e.call_id,
                            "client_id": e.client_id,
                            "group": e.group,
                            "stage": e.stage,
                            "task_id": e.task_id,
                        },
                    },
                    sort_keys=True,
                )
                + "\n"
                for e in rows
            )
        )
        paths[split] = p
    return paths


# --------------------------------------------------------------------------- the Tinker-compatible API


@dataclass
class ModelInput:
    """Stand-in for ``tinker.types.ModelInput`` (a token sequence)."""

    tokens: list[int]

    @classmethod
    def from_ints(cls, tokens: Sequence[int]) -> ModelInput:
        return cls(tokens=list(tokens))

    def to_ints(self) -> list[int]:
        return list(self.tokens)

    @property
    def length(self) -> int:
        return len(self.tokens)


@dataclass
class Datum:
    model_input: Any
    loss_fn_inputs: dict[str, Any]


@dataclass
class AdamParams:
    learning_rate: float
    beta1: float = 0.9
    beta2: float = 0.95
    eps: float = 1e-8


@dataclass
class SamplingParams:
    max_tokens: int
    temperature: float = 1.0
    stop: list[str] = field(default_factory=list)


class _Types:
    ModelInput: Any = ModelInput
    Datum: Any = Datum
    AdamParams: Any = AdamParams
    SamplingParams: Any = SamplingParams


def tinker_types() -> Any:
    """``tinker.types`` when the Tinker SDK is installed, else the local stand-ins with the same constructors."""
    try:
        from tinker import types  # type: ignore[import-not-found]

        return types
    except ImportError:
        return _Types


class Future(Protocol):
    def result(self) -> Any: ...


class TokenizerLike(Protocol):
    def encode(self, text: str) -> list[int]: ...

    def decode(self, tokens: Sequence[int]) -> str: ...


class SamplingClientLike(Protocol):
    def sample(self, prompt: Any, num_samples: int, sampling_params: Any) -> Future: ...

    def compute_logprobs(self, prompt: Any) -> Future: ...


class TrainingClientLike(Protocol):
    def get_tokenizer(self) -> TokenizerLike: ...

    def forward_backward(self, data: list[Any], loss_fn: str) -> Future: ...

    def optim_step(self, adam_params: Any) -> Future: ...

    def save_weights_and_get_sampling_client(self, name: str) -> SamplingClientLike: ...


class ServiceClientLike(Protocol):
    def create_lora_training_client(self, base_model: str, rank: int = 32) -> TrainingClientLike: ...

    def create_sampling_client(self, base_model: str | None = None, model_path: str | None = None) -> SamplingClientLike: ...


def render_chat(messages: Sequence[Mapping[str, str]]) -> str:
    """A plain chat rendering for tokenisation (Tinker's renderers replace this for a real base model)."""
    return "".join(f"<|{m['role']}|>\n{m['content']}\n" for m in messages)


# --------------------------------------------------------------------------- supervised fine-tuning


class StepMetrics(BaseModel):
    step: int
    loss: float | None = None
    mean_reward: float | None = None
    mean_kl: float | None = None
    groups_with_signal: float | None = None
    not_discussed_rate: float | None = None
    checkpoint: str = ""


def sft_train(
    examples: Sequence[TrainingExample],
    training_client: TrainingClientLike,
    *,
    epochs: int = 1,
    batch_size: int = 16,
    learning_rate: float = 1e-4,
    seed: int = 0,
    name: str = "sft",
) -> list[StepMetrics]:
    """LoRA supervised fine-tuning with a cross-entropy loss on the assistant target tokens, weighted per example."""
    types = tinker_types()
    tok = training_client.get_tokenizer()
    train = [e for e in examples if e.split == "train"]
    rng = np.random.default_rng(seed)
    metrics, step = [], 0
    for _ in range(epochs):
        order = rng.permutation(len(train))
        for start in range(0, len(order), batch_size):
            data = []
            for i in order[start : start + batch_size]:
                e = train[int(i)]
                prompt_toks = tok.encode(render_chat(e.messages[:-1]))
                target_toks = tok.encode(e.messages[-1]["content"])
                tokens = prompt_toks + target_toks
                weights = [0.0] * (len(prompt_toks) - 1) + [e.weight] * len(target_toks)
                data.append(
                    types.Datum(
                        model_input=types.ModelInput.from_ints(tokens[:-1]),
                        loss_fn_inputs={"target_tokens": tokens[1:], "weights": weights},
                    )
                )
            out = training_client.forward_backward(data, "cross_entropy").result()
            training_client.optim_step(types.AdamParams(learning_rate=learning_rate)).result()
            step += 1
            metrics.append(StepMetrics(step=step, loss=_loss(out)))
    ckpt = f"{name}-final"
    training_client.save_weights_and_get_sampling_client(ckpt)
    if metrics:
        metrics[-1].checkpoint = ckpt
    return metrics


def _loss(out: Any) -> float | None:
    for k in ("loss", "mean_loss"):
        v = out.get(k) if isinstance(out, dict) else getattr(out, k, None)
        if isinstance(v, (int, float)):
            return float(v)
    m = getattr(out, "metrics", None) or (out.get("metrics") if isinstance(out, dict) else None)
    if isinstance(m, dict):
        for k, v in m.items():
            if "loss" in k and isinstance(v, (int, float)):
                return float(v)
    return None


# --------------------------------------------------------------------------- GRPO (§15.9)


class RLPrompt(BaseModel):
    """One slot-group prompt with its reviewed facts: the unit GRPO samples and scores."""

    prompt_id: str
    call_id: str
    client_id: str = ""
    group: str
    messages: list[dict[str, str]]
    reviewed: list[GoldFact]
    segments: dict[str, str]  # segment id → 1-best text (cached Stage 1–2 output, no ASR during training)
    slots: list[tuple[str, str | None]] | None = None


class GRPOConfig(BaseModel):
    group_size: int = 8  # samples per prompt (§15.9)
    kl_coef: float = 0.05  # penalty toward the reference (M6) checkpoint
    learning_rate: float = 1e-5
    max_tokens: int = 2048
    temperature: float = 1.0
    prompts_per_step: int = 8
    steps: int = 50
    loss_fn: str = "importance_sampling"
    seed: int = 0
    name: str = "grpo"


class GRPOResult(BaseModel):
    steps: list[StepMetrics]
    checkpoint: str


Postprocess = Callable[[str, RLPrompt], list[RolloutFact]]


def _sequences(res: Any) -> list[Any]:
    return list(res.sequences if hasattr(res, "sequences") else res["sequences"])


def grpo_train(
    prompts: Sequence[RLPrompt],
    *,
    training_client: TrainingClientLike,
    reference_client: SamplingClientLike,
    postprocess: Postprocess,
    config: GRPOConfig | None = None,
    heldout_ids: Iterable[str] | None = None,
    on_step: Callable[[StepMetrics], None] | None = None,
) -> GRPOResult:
    """GRPO on the extractor: per prompt, ``group_size`` samples; reward from ``rl_reward.reward`` minus
    ``kl_coef`` × the sampled tokens' log-ratio to the reference; advantages normalised within the group.
    ``heldout_ids`` defaults to the gold dataset's held-out calls (§15.9: never used for RL)."""
    cfg = config or GRPOConfig()
    held = set(heldout_ids) if heldout_ids is not None else heldout_call_ids()
    if any(p.call_id in held for p in prompts):
        raise ValueError("held-out gold calls are never used for RL (§15.9)")
    types = tinker_types()
    tok = training_client.get_tokenizer()
    sampler = training_client.save_weights_and_get_sampling_client(f"{cfg.name}-0")
    rng = np.random.default_rng(cfg.seed)
    history: list[StepMetrics] = []
    for step in range(1, cfg.steps + 1):
        chosen = rng.choice(len(prompts), size=min(cfg.prompts_per_step, len(prompts)), replace=False)
        data, rewards_all, kls, signal, nd = [], [], [], 0, []
        for i in chosen:
            p = prompts[int(i)]
            prompt_toks = tok.encode(render_chat(p.messages))
            res = sampler.sample(
                prompt=types.ModelInput.from_ints(prompt_toks),
                num_samples=cfg.group_size,
                sampling_params=types.SamplingParams(max_tokens=cfg.max_tokens, temperature=cfg.temperature),
            ).result()
            seqs = _sequences(res)
            adjusted, rewards = [], []
            for seq in seqs:
                facts = postprocess(tok.decode(seq.tokens), p)
                rb: RewardBreakdown = reward(facts, p.reviewed, p.segments, slots=p.slots)
                ref = reference_client.compute_logprobs(types.ModelInput.from_ints(prompt_toks + list(seq.tokens))).result()
                ref_lp = [x if x is not None else 0.0 for x in list(ref)[len(prompt_toks) :]]
                lp = list(seq.logprobs or [0.0] * len(seq.tokens))
                kl = float(np.sum(np.asarray(lp[: len(ref_lp)]) - np.asarray(ref_lp[: len(lp)]))) if lp else 0.0
                kls.append(kl / max(1, len(lp)))
                rewards.append(rb.total)
                adjusted.append(rb.total - cfg.kl_coef * kl)
                nd += [f.status == "not_discussed" for f in facts]
            rewards_all += rewards
            a = np.asarray(adjusted, dtype=float)
            if len(a) < 2 or float(a.std()) < 1e-8:
                continue  # every sample scored the same: no learning signal in this group
            signal += 1
            adv = (a - a.mean()) / (a.std() + 1e-6)
            for seq, adv_i in zip(seqs, adv):
                tokens = prompt_toks + list(seq.tokens)
                ob = len(prompt_toks) - 1
                lp = list(seq.logprobs or [0.0] * len(seq.tokens))
                data.append(
                    types.Datum(
                        model_input=types.ModelInput.from_ints(tokens[:-1]),
                        loss_fn_inputs={
                            "target_tokens": tokens[1:],
                            "logprobs": [0.0] * ob + lp,
                            "advantages": [0.0] * ob + [float(adv_i)] * len(seq.tokens),
                        },
                    )
                )
        loss = None
        if data:
            loss = _loss(training_client.forward_backward(data, cfg.loss_fn).result())
            training_client.optim_step(types.AdamParams(learning_rate=cfg.learning_rate)).result()
            sampler = training_client.save_weights_and_get_sampling_client(f"{cfg.name}-{step}")
        m = StepMetrics(
            step=step,
            loss=loss,
            mean_reward=float(np.mean(rewards_all)) if rewards_all else None,
            mean_kl=float(np.mean(kls)) if kls else None,
            groups_with_signal=signal / max(1, len(chosen)),
            not_discussed_rate=float(np.mean(nd)) if nd else None,
            checkpoint=f"{cfg.name}-{step}" if data else "",
        )
        history.append(m)
        if on_step:
            on_step(m)
    last = next((m.checkpoint for m in reversed(history) if m.checkpoint), f"{cfg.name}-0")
    return GRPOResult(steps=history, checkpoint=last)


# --------------------------------------------------------------------------- promotion (§15.8, §15.9)


class GroupPromotion(BaseModel):
    group: str
    switch: bool
    reasons: list[str]
    metrics: dict[str, tuple[float | None, float | None]]  # metric → (incumbent, candidate)


PROMOTION_METRICS = ("status_accuracy", "slot_recall", "value_coverage")


def promotion_check(
    candidate: EvalReport,
    incumbent: EvalReport,
    groups: Sequence[str],
    *,
    hacking: HackingReport | None = None,
    human_review_signed_off: bool | None = None,
    tol: float = 1e-9,
) -> list[GroupPromotion]:
    """Per slot group: switch only if status accuracy, slot recall and value coverage match or beat the incumbent
    and ECE does not worsen. For an RL candidate, the reward-hacking checks must pass and a person must have signed
    off the sampled outputs."""
    out = []
    for g in groups:
        reasons, metrics = [], {}
        for m in PROMOTION_METRICS:
            inc, cand = incumbent.metric(f"by_group.{g}.{m}"), candidate.metric(f"by_group.{g}.{m}")
            metrics[m] = (inc, cand)
            if inc is None or cand is None:
                reasons.append(f"{m} not measured")
            elif cand < inc - tol:
                reasons.append(f"{m} {cand:.3f} < {inc:.3f}")
        inc, cand = incumbent.metric(f"ece_by_group.{g}"), candidate.metric(f"ece_by_group.{g}")
        metrics["ece"] = (inc, cand)
        if inc is not None and cand is not None and cand > inc + tol:
            reasons.append(f"ECE {cand:.3f} > {inc:.3f}")
        if hacking is not None and not hacking.passed:
            reasons.append("reward-hacking checks failed: " + ", ".join(r.key for r in hacking.failures()))
        if human_review_signed_off is False:
            reasons.append("the 50 sampled outputs have not been signed off by a person")
        out.append(
            GroupPromotion(
                group=g, switch=not reasons, reasons=reasons or ["matches or beats the incumbent with ECE no worse"], metrics=metrics
            )
        )
    return out


def write_group_routing(promotions: Sequence[GroupPromotion], model: str, path: Path) -> Path | None:
    """Route the promoted slot groups to the fine-tuned model in the routing file, for a pull request (never automatic)."""
    import yaml

    switched = [p.group for p in promotions if p.switch]
    if not switched:
        return None
    data = (yaml.safe_load(path.read_text()) if path.exists() else None) or {}
    data.setdefault("extraction_groups", {}).update({g: model for g in switched})
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path
