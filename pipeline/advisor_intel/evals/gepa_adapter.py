"""Prompt optimisation with GEPA (spec §15.6).

GEPA evolves prompt text in place; the harness, schemas and code stay as they are. The components are each slot
group's instruction block (``prompts/extract_slot_group/<group>/v{n}.md``) and the entailment verifier prompt
(``prompts/entailment/v{n}.md``). ``ExtractionAdapter`` implements gepa 0.1.4's ``GEPAAdapter``:

- ``evaluate`` writes the candidate texts into an overlay copy of the prompts tree (read by the pipeline's prompt
  loader for the duration and pinned in ``VersionPins.prompt_versions``), runs extraction on a minibatch of
  cached calls through the injected pipeline callable, and scores each call 0.4·status accuracy + 0.4·slot recall
  + 0.2·value coverage over the optimised groups;
- ``make_reflective_dataset`` turns each call into feedback text ("missed retirement.client.retirementAge; marked
  advisor suggestion as client goal") with the relevant transcript excerpt, per component.

``optimize_group`` runs GEPA for one slot group with a budget of 100–500 evaluations, never on the 20 held-out
calls; ``promote_if_gate_passes`` evaluates the winner on the held-out calls, applies the §15.4 gate and writes
``v{n+1}.md`` for the pull request.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from gepa import EvaluationBatch
from pydantic import BaseModel

from ..models.runs import VersionPins
from ..schema.slots import load_catalog
from ._runtime import PROMPTS_DIR_ENV, call, lazy_module, prompts_dir, run_sync, stable_hash, unit_hash
from .datasets import Stage12Cache
from .extraction_eval import EvalReport, Runner, load_cached_call, run_local
from .gold import GoldCall
from .records import SystemCallOutput, summarise_result
from .scorers import Alignment, align, covers, slot_recall, status_accuracy, value_coverage, value_text

ENTAILMENT = "entailment"
MIN_BUDGET, MAX_BUDGET = 100, 500
HELDOUT_CALLS = 20
WEIGHTS = {"status_accuracy": 0.4, "slot_recall": 0.4, "value_coverage": 0.2}
_VERSION = re.compile(r"^v(\d+)\.md$")


def extract_component(group: str) -> str:
    return f"extract_slot_group/{group}"


def component_group(component: str) -> str | None:
    return component.split("/", 1)[1] if component.startswith("extract_slot_group/") else None


# --------------------------------------------------------------------------- the prompt files


class PromptStore:
    """``prompts/<component>/v{n}.md``: read the newest version, write the next, and overlay candidates."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or prompts_dir()

    def versions(self, component: str) -> list[int]:
        d = self.root / component
        return sorted(int(m.group(1)) for p in d.iterdir() if (m := _VERSION.match(p.name))) if d.exists() else []

    def latest(self, component: str) -> tuple[int, str]:
        vs = self.versions(component)
        if not vs:
            return 0, ""
        return vs[-1], (self.root / component / f"v{vs[-1]}.md").read_text()

    def read(self, component: str) -> str:
        n, text = self.latest(component)
        if n == 0:
            raise FileNotFoundError(f"no prompt versions under {self.root / component}")
        return text

    def write_next(self, component: str, text: str) -> Path:
        n, current = self.latest(component)
        if text == current:
            raise ValueError(f"{component}: the candidate is identical to v{n}")
        path = self.root / component / f"v{n + 1}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text if text.endswith("\n") else text + "\n")
        return path

    @contextlib.contextmanager
    def overlay(self, candidate: Mapping[str, str]) -> Iterator[tuple[Path, dict[str, str]]]:
        """A temporary copy of the prompts tree with each candidate text as the component's next version. For the
        duration, ``ADVISOR_INTEL_PROMPTS_DIR`` and the pipeline's own loader (``advisor_intel.prompts``, whose
        ``PROMPTS_DIR`` and version cache are swapped) read from it; both are restored afterwards. The swap is
        process-wide, so candidates are evaluated one at a time."""
        tmp = Path(tempfile.mkdtemp(prefix="gepa-prompts-"))
        versions: dict[str, str] = {}
        previous = os.environ.get(PROMPTS_DIR_ENV)
        loader = lazy_module("advisor_intel.prompts")
        loader_dir = getattr(loader, "PROMPTS_DIR", None)
        skip = shutil.ignore_patterns("__pycache__", "*.py", "*.pyc")
        try:
            if loader_dir is not None and Path(loader_dir).exists() and Path(loader_dir).resolve() != self.root.resolve():
                shutil.copytree(loader_dir, tmp, dirs_exist_ok=True, ignore=skip)  # every component the pipeline loads
            if self.root.exists():
                shutil.copytree(self.root, tmp, dirs_exist_ok=True, ignore=skip)
            for component, text in candidate.items():
                n, current = self.latest(component)
                if text == current and n:
                    versions[component] = f"v{n}"
                    continue
                # The candidate is the newest version for loaders that pick the highest v{n}.md, and also sits under
                # its unique pinned label for loaders (and caches) keyed by the pinned version.
                label = f"v{n + 1}+gepa.{stable_hash(text, 8)}"
                (tmp / component).mkdir(parents=True, exist_ok=True)
                (tmp / component / f"v{n + 1}.md").write_text(text)
                (tmp / component / f"{label}.md").write_text(text)
                versions[component] = label
            os.environ[PROMPTS_DIR_ENV] = str(tmp)
            if loader_dir is not None:
                loader.PROMPTS_DIR = tmp
                _clear_prompt_cache(loader)
            yield tmp, versions
        finally:
            if previous is None:
                os.environ.pop(PROMPTS_DIR_ENV, None)
            else:
                os.environ[PROMPTS_DIR_ENV] = previous
            if loader_dir is not None:
                loader.PROMPTS_DIR = loader_dir
                _clear_prompt_cache(loader)  # unpinned loads cached during the overlay saw the candidate as newest
            shutil.rmtree(tmp, ignore_errors=True)


def _clear_prompt_cache(loader: Any) -> None:
    clear = getattr(getattr(loader, "load_prompt", None), "cache_clear", None)
    if clear is not None:
        clear()


# --------------------------------------------------------------------------- held-out calls


def heldout_ids(cases: Sequence[Mapping[str, Any]], n: int = HELDOUT_CALLS) -> set[str]:
    """The held-out test calls: those marked ``split: heldout``, else the ``n`` calls with the lowest call-id hash
    (a choice that does not depend on list order and is stable as calls are added)."""
    marked = {c["input"]["call_id"] for c in cases if (c.get("metadata") or {}).get("split") == "heldout"}
    if marked:
        return marked
    ids = sorted({c["input"]["call_id"] for c in cases}, key=lambda cid: unit_hash(f"heldout/{cid}"))
    return set(ids[:n])


def split_cases(cases: Sequence[Mapping[str, Any]], *, heldout: set[str], val_share: float = 0.3) -> tuple[list[Any], list[Any], list[Any]]:
    """(train, val, heldout) by call id; GEPA sees only train and val."""
    train, val, test = [], [], []
    for c in cases:
        cid = c["input"]["call_id"]
        if cid in heldout:
            test.append(c)
        elif unit_hash(f"gepa-val/{cid}") < val_share:
            val.append(c)
        else:
            train.append(c)
    return train, val, test


# --------------------------------------------------------------------------- the adapter


def _label_value(v: Any) -> str:
    return value_text(v)


def feedback_lines(al: Alignment, groups: Sequence[str] | None) -> list[str]:
    """Per-fact feedback for the reflection model, in the words of §15.6."""
    lines = []
    for p in al.scoped(groups=groups):
        s, g = p.system, p.gold
        if p.gold_valued and not p.system_found:
            lines.append(f"missed {p.label}")
            continue
        if g is None and s is not None and s.extracted and s.status in ("stated", "implied"):
            lines.append(f"invented {p.label} (it was not discussed)")
            continue
        if s is not None and g is not None and p.system_status != p.gold_status:
            lines.append(f"marked {p.label} as {p.system_status}, expected {p.gold_status}")
            continue
        if p.gold_valued and p.system_found and g is not None and g.value is not None and not covers(s.value if s else None, g.value):
            lines.append(f"{p.label}: extracted {_label_value(s.value if s else None)}, but the client meant {_label_value(g.value)}")
        if p.gold_valued and p.system_found and s is not None and g is not None:
            sa = "advisor" if s.attribution == "advisor_suggested" else "client"
            ga = "advisor" if g.attribution == "advisor_suggested" else "client"
            if ga == "advisor" and sa == "client":
                lines.append(
                    "marked advisor suggestion as client goal"
                    if p.group in ("retirement", "goals")
                    else f"{p.label}: marked an advisor suggestion as the client's own statement"
                )
            elif ga == "client" and sa == "advisor":
                lines.append(f"{p.label}: marked the client's own statement as an advisor suggestion")
    return lines


def calibration_lines(al: Alignment, groups: Sequence[str] | None) -> list[str]:
    """Feedback for the entailment verifier: confident wrong facts, auto-accepted mistakes, doubted right facts."""
    from .scorers import fact_label

    lines = []
    for p in al.scoped(groups=groups):
        s = p.system
        if s is None or not s.extracted or s.p_correct is None or s.status == "not_discussed":
            continue
        y = fact_label(p)
        if y == 0 and s.auto_accepted:
            lines.append(f"{p.label}: auto-accepted but wrong (p_correct {s.p_correct:.2f})")
        elif y == 0 and s.p_correct >= 0.8:
            lines.append(f"{p.label}: judged entailed (p_correct {s.p_correct:.2f}) but the evidence does not support it")
        elif y == 1 and s.p_correct < 0.4:
            lines.append(f"{p.label}: correct but judged unsupported (p_correct {s.p_correct:.2f})")
    return lines


class ExtractionAdapter:
    """gepa 0.1.4 ``GEPAAdapter`` over the extraction pipeline (DataInst: an eval case; Trajectory: a dict)."""

    propose_new_texts = None  # GEPA's reflective proposer, driven by ``reflection_lm``

    def __init__(
        self,
        *,
        runner: Runner,
        pins: VersionPins,
        groups: Sequence[str] | None = None,
        prompt_store: PromptStore | None = None,
        cache: Stage12Cache | None = None,
        heldout: set[str] | frozenset[str] = frozenset(),
        weights: Mapping[str, float] = WEIGHTS,
        excerpt_chars: int = 2000,
        max_records: int = 8,
    ) -> None:
        self.runner, self.pins = runner, pins
        self.groups = list(groups) if groups is not None else None
        self.prompts = prompt_store or PromptStore()
        self.cache = cache or Stage12Cache()
        self.heldout = set(heldout)
        self.weights = dict(weights)
        self.excerpt_chars = excerpt_chars
        self.max_records = max_records
        self.metric_calls = 0

    # ------------------------------------------------------------ scoring

    def score(self, out: SystemCallOutput, gold: GoldCall) -> tuple[float, dict[str, float], Alignment]:
        al = align(out, gold)
        parts = {
            "status_accuracy": status_accuracy(out, gold, groups=self.groups, alignment=al).value,
            "slot_recall": slot_recall(out, gold, groups=self.groups, alignment=al).value,
            "value_coverage": value_coverage(out, gold, groups=self.groups, alignment=al).value,
        }
        filled = {k: (1.0 if v is None else float(v)) for k, v in parts.items()}  # nothing to get wrong scores full marks
        return sum(self.weights[k] * filled[k] for k in self.weights), filled, al

    def _excerpt(self, call: Any, gold: GoldCall) -> str:
        cat = load_catalog()
        segs = {s.segment_id for f in gold.facts for s in f.spans if self.groups is None or cat.slot(f.slot_id).group in self.groups}
        order = [s.segment_id for s in call.transcript.segments]
        keep = set()
        for sid in segs:
            if sid in order:
                i = order.index(sid)
                keep.update(order[max(0, i - 1) : i + 2])
        lines = [f"[{s.segment_id} | {s.speaker_role}] {s.text}" for s in call.transcript.segments if s.segment_id in keep]
        text = "\n".join(lines)
        return text if len(text) <= self.excerpt_chars else text[: self.excerpt_chars] + " …"

    # ------------------------------------------------------------ GEPAAdapter

    def evaluate(self, batch: list[dict[str, Any]], candidate: dict[str, str], capture_traces: bool = False) -> EvaluationBatch:
        leaked = [c["input"]["call_id"] for c in batch if c["input"]["call_id"] in self.heldout]
        if leaked:
            raise ValueError(f"held-out calls must never reach the optimiser (§15.6): {sorted(set(leaked))[:5]}")
        outputs, scores, trajectories, objectives = [], [], [], []
        with self.prompts.overlay(candidate) as (_, versions):
            pins = self.pins.model_copy(update={"prompt_versions": {**self.pins.prompt_versions, **versions}})
            for case in batch:
                cid = case["input"]["call_id"]
                try:
                    cached = load_cached_call(case["input"], case.get("metadata"), pins=pins, cache=self.cache)
                    result = run_sync(self.runner(cached, pins))
                    out = summarise_result(result, call=cached, transcript_kind=cached.transcript_kind)
                    gold = GoldCall.model_validate(case["expected"])
                    s, parts, al = self.score(out, gold)
                    outputs.append(out.model_dump(mode="json"))
                    scores.append(s)
                    objectives.append(parts)
                    if capture_traces:
                        trajectories.append(
                            {
                                "call_id": cid,
                                "score": s,
                                "parts": parts,
                                "feedback": feedback_lines(al, self.groups),
                                "calibration": calibration_lines(al, self.groups),
                                "facts": [
                                    f"{p.label}: {p.system_status} {_label_value(p.system.value) if p.system else ''}".strip()
                                    for p in al.scoped(groups=self.groups)
                                    if p.system is not None and p.system.extracted
                                ][:40],
                                "excerpt": self._excerpt(cached, gold),
                            }
                        )
                except Exception as e:  # noqa: BLE001 - GEPA's contract: a failing example scores 0 with the error as feedback
                    outputs.append({"call_id": cid, "error": f"{type(e).__name__}: {e}"})
                    scores.append(0.0)
                    objectives.append({k: 0.0 for k in self.weights})
                    if capture_traces:
                        trajectories.append(
                            {
                                "call_id": cid,
                                "score": 0.0,
                                "parts": {},
                                "feedback": [f"the pipeline failed: {type(e).__name__}: {e}"],
                                "calibration": [],
                                "facts": [],
                                "excerpt": "",
                            }
                        )
        self.metric_calls += len(batch)
        return EvaluationBatch(
            outputs=outputs,
            scores=scores,
            trajectories=trajectories if capture_traces else None,
            objective_scores=objectives,
            num_metric_calls=len(batch),
        )

    def make_reflective_dataset(
        self, candidate: dict[str, str], eval_batch: EvaluationBatch, components_to_update: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for component in components_to_update:
            records = []
            for t in eval_batch.trajectories or []:
                lines = t["calibration"] if component == ENTAILMENT else t["feedback"]
                records.append(
                    {
                        "Inputs": {
                            "call": t["call_id"],
                            "slot groups": ", ".join(self.groups or ["all"]),
                            "transcript excerpt": t["excerpt"],
                        },
                        "Generated Outputs": "\n".join(t["facts"]) or "(no facts extracted)",
                        "Feedback": "; ".join(lines) if lines else "every fact in these slot groups was right",
                        "score": round(t["score"], 4),
                    }
                )
            records.sort(key=lambda r: (r["Feedback"].startswith("every fact"), r["score"]))  # failures first, a few successes for contrast
            out[component] = records[: self.max_records]
        return out


# --------------------------------------------------------------------------- optimisation runs


class GroupOptimisation(BaseModel):
    group: str
    components: list[str]
    seed_candidate: dict[str, str]
    best_candidate: dict[str, str]
    val_score_seed: float | None
    val_score_best: float | None
    metric_calls: int
    candidates: int


def check_budget(budget: int) -> int:
    if not MIN_BUDGET <= budget <= MAX_BUDGET:
        raise ValueError(f"GEPA budget per slot group is {MIN_BUDGET}–{MAX_BUDGET} evaluations (§15.6), got {budget}")
    return budget


def default_reflection_lm(pins: VersionPins | None = None) -> Callable[[str | list[dict[str, Any]]], str]:
    """The reflection model: a Pydantic AI agent on the frontier role, as a GEPA ``LanguageModel`` callable."""
    from pydantic_ai import Agent

    from ..llm import for_role

    rm = for_role("frontier", pins)
    agent = Agent(
        rm.model,
        name="gepa_reflection",
        model_settings=rm.settings,
        instructions="You improve the instruction block of an information-extraction prompt for South African financial advice calls. "
        "Keep every rule that the feedback does not contradict; be specific about slots, statuses and attribution.",
    )

    def lm(prompt: str | list[dict[str, Any]]) -> str:
        if not isinstance(prompt, str):
            prompt = "\n\n".join(str(m.get("content", "")) for m in prompt)
        return str(call(agent.run, prompt).output)

    return lm


def optimize_group(
    group: str,
    cases: Sequence[dict[str, Any]],
    *,
    runner: Runner,
    pins: VersionPins,
    reflection_lm: Callable[[str | list[dict[str, Any]]], str] | None = None,
    budget: int = 300,
    include_entailment: bool = False,
    prompt_store: PromptStore | None = None,
    cache: Stage12Cache | None = None,
    heldout: set[str] | None = None,
    run_dir: str | None = None,
    seed: int = 0,
    enforce_budget: bool = True,
    minibatch_size: int = 3,
) -> tuple[GroupOptimisation, Any]:
    """Run GEPA on one slot group's instruction block (and optionally the entailment prompt)."""
    import gepa

    if enforce_budget:
        check_budget(budget)
    store = prompt_store or PromptStore()
    held = heldout if heldout is not None else heldout_ids(cases)
    train, val, _ = split_cases(cases, heldout=held)
    if not train or not val:
        raise ValueError("GEPA needs training and validation calls outside the held-out set")
    components = [extract_component(group)] + ([ENTAILMENT] if include_entailment else [])
    seed_candidate = {c: store.read(c) for c in components}
    adapter = ExtractionAdapter(runner=runner, pins=pins, groups=[group], prompt_store=store, cache=cache, heldout=held)
    result = gepa.optimize(
        seed_candidate=seed_candidate,
        trainset=train,
        valset=val,
        adapter=adapter,
        reflection_lm=reflection_lm or default_reflection_lm(pins),
        max_metric_calls=budget,
        reflection_minibatch_size=minibatch_size,
        run_dir=run_dir,
        seed=seed,
        display_progress_bar=False,
    )
    best = result.best_candidate if isinstance(result.best_candidate, dict) else {components[0]: result.best_candidate}
    scores = result.val_aggregate_scores
    summary = GroupOptimisation(
        group=group,
        components=components,
        seed_candidate=seed_candidate,
        best_candidate=dict(best),
        val_score_seed=scores[0] if scores else None,
        val_score_best=scores[result.best_idx] if scores else None,
        metric_calls=adapter.metric_calls,
        candidates=len(result.candidates),
    )
    return summary, result


class PromotionResult(BaseModel):
    promoted: bool
    paths: list[str]
    gate_markdown: str
    heldout_calls: int


def promote_if_gate_passes(
    best: Mapping[str, str],
    heldout_cases: Sequence[dict[str, Any]],
    *,
    runner: Runner,
    pins: VersionPins,
    prompt_store: PromptStore | None = None,
    cache: Stage12Cache | None = None,
    baseline: EvalReport | None = None,
) -> PromotionResult:
    """§15.6 promotion: the winner must pass the §15.4 gate on the held-out calls; then each changed component is
    written as ``v{n+1}.md`` for a pull request."""
    from .gate import compare

    store = prompt_store or PromptStore()
    if not heldout_cases:
        raise ValueError("promotion needs the held-out calls")
    if baseline is not None:
        current = baseline
    else:
        with store.overlay({c: store.read(c) for c in best}) as (_, versions):  # the current texts, loaded the same way
            cur_pins = pins.model_copy(update={"prompt_versions": {**pins.prompt_versions, **versions}})
            current = run_local(heldout_cases, runner=runner, pins=cur_pins, cache=cache, save=False, experiment="gepa-current")
    with store.overlay(best) as (_, versions):
        cand_pins = pins.model_copy(update={"prompt_versions": {**pins.prompt_versions, **versions}})
        candidate = run_local(heldout_cases, runner=runner, pins=cand_pins, cache=cache, save=False, experiment="gepa-candidate")
    res = compare(candidate, current)
    paths = []
    if res.passed:
        for component, text in best.items():
            try:
                paths.append(str(store.write_next(component, text)))
            except ValueError:
                continue  # unchanged component
    return PromotionResult(promoted=res.passed and bool(paths), paths=paths, gate_markdown=res.markdown(), heldout_calls=len(heldout_cases))
